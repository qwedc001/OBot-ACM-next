"""Bot orchestration over Dashboard's CurationService, official ORM and scheduler."""

import logging
import time

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from ..catalog import CommandSpec
from ..clients.jobs import QueueClosed, QueueFull
from ..contracts import AccessDenied, Role, SceneKind
from ..curation.pickone import chat
from ..curation.pickone.library import LibraryView
from ..models import PickOneContext, PickOneLibrary
from ..views.pickone import PickOneBackend
from .base import Service
from .scene_access import MANAGE, scene_scope

logger = logging.getLogger(__name__)
ADMIN = CommandSpec("fun.pickone.audit", "fun", "pickone.audit", "审核上传图片", permission=Role.SUPERUSER)


class PickOneService(Service):
    dependencies = ("curation", "clients.http", "clients.ocr", "clients.jobs", "clients.renderer")

    @classmethod
    def build(cls, container):
        return cls(container.curation, container.clients)

    def __init__(self, curation, clients):
        self.curation, self.clients = curation, clients
        self._ocr_jobs = {}
        self._scheduler = None
        clients.renderer.register("pickone", PickOneBackend())

    async def call(self, operation, *args):
        return await self.curation.run(operation, self.curation.pickone, *args)

    def libraries(self, context):
        group = (
            self.curation.group_pickone(scene_scope(context.scene))
            if context.scene.kind == SceneKind.GROUP
            else None
        )
        return LibraryView(self.curation.pickone, group)

    async def in_scene(self, context, operation, *args):
        return await self.curation.run(operation, self.libraries(context), *args)

    async def _remember(self, scene, field, value):
        async with self.curation.sessions() as session, session.begin():
            statement = insert(PickOneContext).values(
                scene=scene.key, **{field: {**value, "at": time.time()}}
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["scene"], set_={field: getattr(statement.excluded, field)}
                )
            )

    async def _recall(self, scene, field):
        async with self.curation.sessions() as session:
            row = await session.get(PickOneContext, scene.key)
            value = getattr(row, field) if row else None
            return value if value and time.time() - value["at"] < 86400 else None

    async def select(self, context, keyword, query="", prefix=None):
        preview = await self._recall(context.scene, "preview") or {}
        return await self.in_scene(context, chat.select, keyword, query, prefix, preview)

    async def picked(self, context, category, stat):
        await self.in_scene(context, chat.picked, category.img_key, stat.name)
        await self._remember(context.scene, "last", {"category": category.img_key, "name": stat.name})

    async def preview(self, context, keyword):
        return await self.in_scene(context, chat.preview, keyword)

    async def remember_preview(self, context, category, images):
        await self._remember(
            context.scene,
            "preview",
            {"category": category.img_key, "ids": [item["hash_id"] for item in images]},
        )

    async def target(self, context, keyword=None, prefix=None):
        if keyword is not None:
            cat, stat, _ = await self.select(context, keyword, prefix=prefix)
            return cat.img_key, stat.name
        last = await self._recall(context.scene, "last")
        if not last:
            raise chat.StoreError(
                "找不到本会话一天内上次发送的图片，请使用 /点赞来只 类别 ID 或 /评论来只 类别 ID 内容。"
            )
        return last["category"], last["name"]

    async def like(self, context, keyword=None, prefix=None):
        return await self.in_scene(context, chat.like, *await self.target(context, keyword, prefix))

    async def comment(self, context, text, keyword=None, prefix=None):
        return await self.in_scene(context, chat.comment, *await self.target(context, keyword, prefix), text)

    async def is_admin(self, context):
        return bool(context.command_allowed and await context.command_allowed(ADMIN))

    async def manages_group(self, context):
        return context.scene.kind == SceneKind.GROUP and bool(
            context.command_allowed and await context.command_allowed(MANAGE)
        )

    async def create_category(self, context, key, title, aliases=None):
        if not await self.manages_group(context):
            raise AccessDenied("Group administrator required")
        scope = scene_scope(context.scene)
        store = self.curation.group_pickone(scope)
        await self.curation.run(chat.create_category, store, key, title, context.actor.id, aliases)
        async with self.curation.sessions() as session, session.begin():
            await session.execute(insert(PickOneLibrary).values(scope=scope).on_conflict_do_nothing())

    async def manage(self, context, operation, *args):
        """Group-only Dashboard operations share the same files and bot authority."""
        if not await self.manages_group(context):
            raise AccessDenied("Group administrator required")
        return await self.curation.run(
            operation, self.curation.group_pickone(scene_scope(context.scene)), *args
        )

    async def upload(self, context, keyword, urls):
        if not urls:
            raise chat.StoreError("未识别到图片，请将图片和 /添加来只 类别 放在同一条消息中。")
        if len(urls) > 4:
            raise chat.StoreError("每次最多上传 4 张图片。")
        admin = await self.is_admin(context)
        # Resolve once before fetching attachments. Group managers may only
        # bypass review in their own group's library, never the public one.
        cat = await self.in_scene(context, lambda repo, store: chat.category(store, keyword, fuzzy=False))
        admin = admin or (cat.img_key.startswith("group:") and await self.manages_group(context))
        results = {"added": 0, "pending": 0, "duplicate": 0, "failed": 0}
        from ..clients.http import UpstreamError

        for url in urls:
            try:
                data = await self.clients.http.download(url, limit=chat.MAX_IMAGE_BYTES)
                status, _, _ = await self.in_scene(
                    context, chat.upload, cat.img_key, data, context.actor.id, admin
                )
                results[status] += 1
            except (chat.StoreError, UpstreamError, ValueError):
                results["failed"] += 1
        await self.enqueue_ocr()
        return results

    async def approve(self, context, library="public"):
        if library not in {"public", "group"}:
            raise chat.StoreError("请选择 public（公共）或 group（本群）图库。")
        if not await self.is_admin(context) and not (
            library == "group" and await self.manages_group(context)
        ):
            raise AccessDenied("Bot administrator required")
        view = self.libraries(context)
        store, _ = view.resolve(library + ":unused")
        result = await self.curation.run(chat.approve, store, context.actor.id)
        await self.enqueue_ocr()
        return result

    async def _recognize(self, scope, key, name):
        store = self.curation.group_pickone(scope) if scope else self.curation.pickone
        data = await self.curation.run(lambda repo: chat.read_image(store, key, name))
        text = await self.clients.ocr.read(data)
        await self.curation.run(chat.apply_ocr, store, key, name, text)

    def _ocr_finished(self, target, future):
        self._ocr_jobs.pop(target, None)
        if not future.cancelled() and future.exception():
            logger.warning("PickOne OCR failed: %s", type(future.exception()).__name__)

    async def enqueue_ocr(self):
        if not self.clients.ocr.enabled:
            return
        async with self.curation.sessions() as session:
            scopes = [None, *(await session.scalars(select(PickOneLibrary.scope))).all()]
        targets = []
        for scope in scopes:
            store = self.curation.group_pickone(scope) if scope else self.curation.pickone
            targets.extend((scope, *target) for target in await self.curation.run(chat.missing_text, store))
        for target in targets:
            if target in self._ocr_jobs:
                continue
            try:
                future = await self.clients.jobs.submit(
                    "pickone:ocr", lambda target=target: self._recognize(*target), key="ocr"
                )
            except (QueueClosed, QueueFull):
                return  # Next scheduler pass retries; uploads and other commands remain available.
            self._ocr_jobs[target] = future
            future.add_done_callback(lambda result, target=target: self._ocr_finished(target, result))

    async def start(self):
        if self.clients.ocr.enabled:
            from nonebot_plugin_apscheduler import scheduler

            self._scheduler = scheduler
            scheduler.add_job(
                self.enqueue_ocr,
                "interval",
                minutes=5,
                id="pickone-ocr",
                max_instances=1,
                replace_existing=True,
            )
            await self.enqueue_ocr()

    async def close(self):
        if self._scheduler and self._scheduler.get_job("pickone-ocr"):
            self._scheduler.remove_job("pickone-ocr")
        for future in tuple(self._ocr_jobs.values()):
            future.cancel()
