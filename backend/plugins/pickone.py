"""PickOne templates: one native handler per action, shared Dashboard storage."""

from functools import wraps

from arclet.alconna import Args, MultiVar
from nonebot.adapters import Bot, Event
from nonebot.plugin import PluginMetadata

from framework.catalog import CommandDirectory, CommandSpec, catalog
from framework.clients.jobs import QueueClosed, QueueFull
from framework.contracts import AccessDenied, Role, SceneKind
from framework.curation.pickone import chat
from framework.integration.command_context import BotServices, CommandContext, Reply
from framework.integration.commands import template_command
from framework.integration.platforms import get_platform
from framework.messages import Image, Message, Text
from framework.responses import text_chunks

__plugin_meta__ = PluginMetadata(
    name="PickOne",
    description="随机表情包、预览、上传与互动",
    usage="/来只 /预览来只 /添加来只",
    type="application",
)
catalog.register_directory(CommandDirectory(("fun", "pickone"), "PickOne 表情包"))


def command(
    name, summary, aliases, *args, usage="", permission=Role.EVERYONE, compact_aliases=(), priority=10
):
    return template_command(
        CommandSpec(
            f"fun.pickone.{name}",
            "fun",
            name,
            summary,
            directory=("fun", "pickone"),
            aliases=aliases,
            example_alias=aliases[0],
            usage=f"/{name} {usage}".strip(),
            examples=(f"/{name} {usage}".strip(),),
            permission=permission,
            scopes=frozenset({SceneKind.GROUP}) if permission == Role.GROUP_ADMIN else frozenset(SceneKind),
        ),
        *args,
        compact_aliases=compact_aliases,
        priority=priority,
        block=True,
    )


take = command(
    "take",
    "按类别随机取图，可用描述搜索",
    ("来只", "pickone"),
    Args["category", str, None]["query", MultiVar(str), ()],
    usage="[类别] [文字]",
    compact_aliases=("来只",),
)
preview = command(
    "preview",
    "预览八张图片，附 ID 前缀可取原图",
    ("预览来只", "预览", "preview"),
    Args["category", str, None]["prefix", str, None],
    usage="[类别] [ID前缀]",
    compact_aliases=("预览来只", "预览"),
)
random = command("random", "从有图片的类别随机取图", ("随机来只", "随便来只"))
capoo = command("capoo", "取一只咖波", ("capoo", "咖波"))
upload = command(
    "add",
    "上传图片，普通用户进入 Dashboard 审核队列",
    ("添加来只", "添加"),
    Args["category", str],
    usage="<类别>（附图片）",
    compact_aliases=("添加来只", "添加"),
)
like = command("like", "点赞本会话一天内最后发出的图片", ("点赞", "喜欢", "爱", "love", "like"))
like_id = command(
    "like-id",
    "按类别和 ID 前缀点赞",
    ("点赞来只", "喜欢来只", "爱来只"),
    Args["category", str]["prefix", str],
    usage="<类别> <ID前缀>",
    compact_aliases=("点赞来只", "喜欢来只", "爱来只"),
)
comment = command(
    "comment",
    "评论本会话最后发出的图片",
    ("评论", "回复", "comment", "say", "reply"),
    Args["content", MultiVar(str)],
    usage="<内容>",
    compact_aliases=("评论", "回复"),
)
comment_id = command(
    "comment-id",
    "按类别和 ID 前缀评论",
    ("评论来只", "回复来只"),
    Args["category", str]["prefix", str]["content", MultiVar(str)],
    usage="<类别> <ID前缀> <内容>",
    compact_aliases=("评论来只", "回复来只"),
    # This longer command must win over /评论<内容>, including malformed args.
    priority=9,
)
audit = command(
    "audit",
    "通过待审核图片（可先在 Dashboard 检查）",
    ("审核来只", "同意来只", "accept", "audit", "ac"),
    permission=Role.SUPERUSER,
)
group_create = command(
    "create",
    "创建本群专属图库类别",
    ("建图库",),
    Args["key", str]["title", str],
    usage="<英文标识> <名称>",
    permission=Role.GROUP_ADMIN,
)
group_audit = command(
    "group-audit",
    "通过本群图库的待审核图片",
    ("审核群图库",),
    permission=Role.GROUP_ADMIN,
)


def errors(handler):
    @wraps(handler)
    async def guarded(*args, **kwargs):
        try:
            await handler(*args, **kwargs)
        except chat.StoreError as error:
            await kwargs["reply"].finish(str(error))
        except AccessDenied:
            await kwargs["reply"].finish("此操作需要本群管理权限；公共图库由机器人超级管理员管理。")
        except (QueueClosed, QueueFull, TimeoutError):
            await kwargs["reply"].finish("图片服务繁忙，请稍后重试。")

    return guarded


async def sheet(context, services, reply, model):
    pages = await services.clients.renderer.render_pages(context.actor, "pickone", model)
    for page in pages:
        await reply.send(Image(await services.clients.artifacts.read(context.actor, page.id)))


async def send_image(context, services, reply, category, query="", prefix=None):
    cat, stat, data = await services.pickone.select(context, category, query, prefix)
    info = f"[PickOne] {chat.category_label(cat)}\nID: {stat.hash_id}\n点赞: {stat.likes} · 提起: {stat.pickup_times + 1}"
    if stat.comments:
        info += "\n评论：\n" + "\n".join(f"{index}. {value}" for index, value in enumerate(stat.comments, 1))
    chunks = text_chunks(info)
    await reply.send(Message(Text(chunks[0]), Image(data)))
    await services.pickone.picked(context, cat, stat)
    for chunk in chunks[1:]:
        await reply.send(chunk)


@take.assign("$main")
@errors
async def handle_take(
    context: CommandContext,
    services: BotServices,
    reply: Reply,
    category: str | None = None,
    query: tuple[str, ...] = (),
):
    if category is None:
        items = await services.pickone.in_scene(context, chat.categories)
        if not items:
            await reply.finish("还没有表情包类别，请先在 Dashboard 配置。")
        await sheet(
            context,
            services,
            reply,
            {"title": "PickOne · 来只什么", "hint": "/来只 类别 [文字] · /预览来只 类别", "items": items},
        )
    else:
        await send_image(context, services, reply, category, " ".join(query))


@preview.assign("$main")
@errors
async def handle_preview(
    context: CommandContext,
    services: BotServices,
    reply: Reply,
    category: str | None = None,
    prefix: str | None = None,
):
    if prefix:
        await send_image(context, services, reply, category, prefix=prefix)
        return
    cat, images = await services.pickone.preview(context, category)
    display = await services.pickone.in_scene(context, chat.describe_category, cat)
    await sheet(
        context,
        services,
        reply,
        {
            "title": display["label"] + " · 预览",
            "hint": f"/预览来只 {display['command_key']} ID前缀 · 获取原图",
            "preview": True,
            "items": images,
        },
    )
    await services.pickone.remember_preview(context, cat, images)


@random.assign("$main")
@errors
async def handle_random(context: CommandContext, services: BotServices, reply: Reply):
    await send_image(context, services, reply, "rand")


@capoo.assign("$main")
@errors
async def handle_capoo(context: CommandContext, services: BotServices, reply: Reply):
    await send_image(context, services, reply, "capoo")


@upload.assign("$main")
@errors
async def handle_upload(
    bot: Bot, event: Event, context: CommandContext, services: BotServices, reply: Reply, category: str
):
    counts = await services.pickone.upload(context, category, get_platform(bot).image_attachments(event))
    await reply.finish(
        f"{counts['category']}：\n"
        f"已添加 {counts['added']} 张；待审核 {counts['pending']} 张；重复 {counts['duplicate']} 张；失败 {counts['failed']} 张。"
        + ("\n普通用户上传需由该图库管理员审核后使用。" if counts["pending"] else "")
        + ("\n" + "；".join(counts["errors"]) if counts["errors"] else "")
    )


@like.assign("$main")
@errors
async def handle_like(context: CommandContext, services: BotServices, reply: Reply):
    await reply.finish(f"点赞成功，目前有 {await services.pickone.like(context)} 个赞。")


@like_id.assign("$main")
@errors
async def handle_like_id(
    context: CommandContext, services: BotServices, reply: Reply, category: str, prefix: str
):
    await reply.finish(f"点赞成功，目前有 {await services.pickone.like(context, category, prefix)} 个赞。")


@comment.assign("$main")
@errors
async def handle_comment(
    context: CommandContext, services: BotServices, reply: Reply, content: tuple[str, ...]
):
    await reply.finish(
        f"评论成功，目前有 {await services.pickone.comment(context, ' '.join(content))} 条评论。"
    )


@comment_id.assign("$main")
@errors
async def handle_comment_id(
    context: CommandContext,
    services: BotServices,
    reply: Reply,
    category: str,
    prefix: str,
    content: tuple[str, ...],
):
    await reply.finish(
        f"评论成功，目前有 {await services.pickone.comment(context, ' '.join(content), category, prefix)} 条评论。"
    )


@audit.assign("$main")
@errors
async def handle_audit(context: CommandContext, services: BotServices, reply: Reply):
    counts = await services.pickone.approve(context)
    await reply.finish(f"已通过 {counts['approved']} 张图片，重复 {counts['duplicate']} 张。")


@group_create.assign("$main")
@errors
async def handle_group_create(
    context: CommandContext, services: BotServices, reply: Reply, key: str, title: str
):
    await services.pickone.create_category(context, key, title)
    await reply.finish(
        f"已创建本群图库「{title}」，发送 /添加{key} 并在同一条消息附图片即可上传，使用 /来只{key} 取图。"
    )


@group_audit.assign("$main")
@errors
async def handle_group_audit(context: CommandContext, services: BotServices, reply: Reply):
    counts = await services.pickone.approve(context, "group")
    await reply.finish(f"本群已通过 {counts['approved']} 张图片，重复 {counts['duplicate']} 张。")
