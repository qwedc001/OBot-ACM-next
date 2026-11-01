"""Exercise PickOne through native QQ events and Alconna, with isolated storage."""

import asyncio
import hashlib
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import nonebot
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.config import BotInfo
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent
from nonebot.message import handle_event


async def main():
    with tempfile.TemporaryDirectory(prefix="obot-pickone-") as temporary:
        root = Path(temporary)
        empty = root / "empty.env"
        empty.write_text("", encoding="utf-8")
        nonebot.init(
            _env_file=empty,
            driver="~fastapi+~httpx+~websockets",
            qq_bots=[],
            superusers={"operator"},
            log_level="ERROR",
            command_start={"/", "!"},
        )
        from framework.config import Settings
        from framework.integration.lifecycle import install_lifecycle
        from framework.messages import Message, Text
        from framework.migrate import upgrade
        from framework.runtime import configure_services

        services = configure_services(Settings(obot_data_dir=root / "data"))
        install_lifecycle(services)
        for plugin in ("plugins.fun", "plugins.pickone", "plugins.help"):
            assert nonebot.load_plugin(plugin)
        from framework.catalog import CommandSpec
        from framework.integration.commands import command

        nonblocking = command(CommandSpec("test.observe", "test", "observe", "测试非阻断指令"), block=False)

        @nonblocking.handle()
        async def observe():
            return  # A matched command without a reply must not trigger fallback.

        driver = nonebot.get_driver()
        bot = Bot(driver._adapters["QQ"], "app", BotInfo(id="app", secret="synthetic"))
        calls, counter = [], 0

        async def send(content, **kwargs):
            calls.append(content)

        async def request(
            command, role="private", user="user", attachments=None, expect_reply=True, to_me=True
        ):
            nonlocal counter
            counter += 1
            payload = dict(
                id=f"event-{counter}",
                content=command,
                to_me=to_me,
                timestamp="2026-10-18T00:00:00+08:00",
                attachments=attachments,
            )
            if role == "private":
                payload["author"] = dict(id=user, user_openid=user)
                event = C2CMessageCreateEvent.model_validate(payload)
            else:
                payload.update(
                    group_id="room",
                    group_openid="room",
                    author=dict(id=user, member_openid=user, member_role=role, bot=False),
                )
                event = GroupAtMessageCreateEvent.model_validate(payload)
            before = len(calls)
            await handle_event(bot, event)
            result = calls[before:]
            if not expect_reply:
                assert not result, (command, result)
                return ""
            assert len(result) == 1, (command, role, result)
            return "\n".join(
                part.content
                for value in result
                for part in (value.parts if isinstance(value, Message) else (Text(value),))
                if isinstance(part, Text)
            )

        await upgrade()
        await driver._lifespan.startup()
        try:
            from PIL import Image

            store = services.curation.pickone
            store.config_path.write_text(json.dumps({"cats": {"id": "猫", "key": ["猫"]}}), encoding="utf-8")
            folder = store.lib_dir / "cats"
            folder.mkdir()
            output = io.BytesIO()
            Image.new("RGB", (16, 16), "coral").save(output, "GIF")
            data = output.getvalue()
            name = hashlib.md5(data).hexdigest() + ".gif"
            (folder / name).write_bytes(data)
            store.save_raw_parser("cats", {name: {"ocr_text": "before"}})
            with (
                patch.object(services.clients.reply, "send", AsyncMock(side_effect=send)),
                patch.object(services.clients.http, "download", AsyncMock(return_value=data)),
            ):
                for role in ("private", "member", "admin", "owner"):
                    for command in (
                        "/来只 猫 before",
                        "/pickone 猫 before",
                        "/fun.pickone.take cats before",
                        "/随机来只",
                        "/随便来只",
                    ):
                        assert "ID:" in await request(command, role)
                    for command in ("/点赞", "/like", "/love"):
                        assert "点赞成功" in await request(command, role)
                    assert "评论成功" in await request(f"/评论 nice cat {role}", role)
                    assert f"nice cat {role}" in store.get_image_stat("cats", name).comments
                    assert "权限" in await request("/审核来只", role)
                    help_text = await request("/help text fun.pickone", role)
                    assert "审核来只" not in help_text and "点赞" in help_text
                assert "已通过" in await request("/审核来只", user="operator")
                assert "审核来只" in await request("/help text fun.pickone", user="operator")
                stat = store.get_image_stat("cats", name)
                assert "点赞成功" in await request(f"/点赞来只 猫 {stat.hash_id[:6]}")
                assert "评论成功" in await request(f"/评论来只 cats {stat.hash_id} more words")
                assert "ID:" in await request(f"/预览 猫 {stat.hash_id[:6]}")
                assert "用法" in await request("/添加来只")
                assert "未识别到图片" in await request("/添加来只 猫")
                assert "重复 1" in await request(
                    "/添加来只 猫",
                    attachments=[
                        dict(
                            content_type="image/gif",
                            url="https://gchat.qpic.cn/synthetic",
                            filename="example.gif",
                            size=len(data),
                            width=16,
                            height=16,
                        )
                    ],
                )
                assert "权限" in await request("/建图库 local 本群图片", "member")
                assert "已创建本群图库" in await request("/建图库 local 本群图片", "admin")
                assert "已添加 1" in await request(
                    "/添加来只 group:local",
                    "admin",
                    attachments=[
                        dict(
                            content_type="image/gif",
                            url="https://gchat.qpic.cn/synthetic",
                            filename="sample.gif",
                            size=len(data),
                            width=16,
                            height=16,
                        )
                    ],
                )
                assert "ID:" in await request("/来只 group:local", "member")
                # Compact Chinese aliases preserve old OBot usage; explicit
                # namespaces remain optional. Longer aliases must not be eaten
                # by /添加, /预览, /评论 or /回复.
                attachment = [dict(content_type="image/gif", url="https://multimedia.nt.qq.com.cn/synthetic")]
                for alias in ("添加", "添加来只"):
                    for key in ("local", "group:local"):
                        for separator in ("", " "):
                            command = f"/{alias}{separator}{key}"
                            missing = await request(command, "member")
                            assert "本群图库「本群图片」" in missing and "未识别到图片" in missing
                            assert "/添加 local" in missing
                            assert "移动端可长按编辑框进入全屏输入模式以附加图片喵" in missing
                            uploaded = await request(command, "member", attachments=attachment)
                            assert "本群图库「本群图片」" in uploaded and "重复 1" in uploaded
                for alias in ("来只", "预览", "预览来只"):
                    command = f"/{alias}local" + (f" {stat.hash_id}" if alias != "来只" else "")
                    assert "本群图库「本群图片」" in await request(command, "member")
                for alias in ("点赞来只", "喜欢来只", "爱来只"):
                    assert "点赞成功" in await request(f"/{alias}local {stat.hash_id}", "member")
                for alias in ("评论来只", "回复来只"):
                    assert "评论成功" in await request(f"/{alias}local {stat.hash_id} {alias}", "member")
                    assert "用法" in await request(f"/{alias}local", "member")
                assert "评论成功" in await request("/评论紧凑评论", "member")
                assert "评论成功" in await request("/回复紧凑回复", "member")
                for command in (
                    "/pickonecats",
                    "/previewcats",
                    "/commenthello",
                    "/fun.pickone.addcats",
                    "/点赞猫",
                ):
                    assert "指令匹配失败" in await request(command)
                for command in ("hello", "hello /wrong", "/test.observe"):
                    await request(command, expect_reply=False)
                await request("/unknown", "member", to_me=False, expect_reply=False)
                unknown = await request("/hepl secret-argument", "member")
                assert "指令匹配失败 /hepl" in unknown and "/help —" in unknown
                assert "secret-argument" not in unknown
                assert "指令匹配失败 !hepl" in await request("!hepl")
                assert "指令匹配失败 /" in await request("/")
                typo = await request("/建图裤", "member")
                assert "/建图库" not in typo
                assert "/建图库" in await request("/建图裤", "admin")
                assert "类别不存在" in await request("/添加missing", "member")
                assert "公共图库「猫」" in await request("/添加cats")
                assert "类别不存在" in await request("/pickone missing")
                unknown = await request("/pickup")
                assert "指令匹配失败 /pickup" in unknown
                assert unknown.rstrip().endswith("使用 /help 查看可用指令。")
                malformed = await request("/添加")
                assert malformed.rstrip().endswith("使用 /help 添加来只 查看可用指令。")
                assert "没有这个图库" in await request("/来只 group:local")
                assert "权限" in await request("/审核群图库", "member")
                assert "本群已通过" in await request("/审核群图库", "owner")
                assert services.telemetry.counts.get("failed", 0) == 0
                print(json.dumps({"ok": True, "native_dispatch": True}))
        finally:
            await driver._lifespan.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
