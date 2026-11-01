"""Small outbound capability samples for the administrator debug plugin."""

from ..messages import Button, ButtonAction, ButtonModal, Keyboard, Markdown, Message
from .base import Service


class DebugService(Service):
    @classmethod
    def build(cls, container):
        return cls()

    async def markdown(self) -> Message:
        # No external resources: isolate native Markdown from URL allowlists.
        return Message(
            Markdown(
                "# Lowlight · Markdown 实验室\n\n"
                "## 文字与排版\n\n"
                "普通文字 · **加粗** · _斜体_ · ***加粗斜体*** · ~~删除线~~\n\n"
                "> 如果这里显示为引用块，说明 QQ 客户端正在解析 Markdown。\n\n"
                "## 今日实验清单\n\n"
                "1. 检查标题与文字样式\n"
                "2. 检查列表与中文换行\n"
                "3. 发送 /debug button 继续测试按钮\n\n"
                "- 私聊与群聊可以分别测试\n"
                "- 该样例没有链接或图片，不依赖资源域名配置\n\n"
                "***\n\n"
                "这是原生 Markdown 消息，不是渲染成图片的帮助。"
            )
        )

    async def button(self) -> Message:
        return Message(
            Markdown(
                "# Lowlight · 按钮游乐场\n\n"
                "- 打开平台：跳转到 QQ 开放平台网页\n"
                "- 填写按钮：只把命令或文字放入输入框，等待你手动发送\n"
                "- 直接 Ping：点击后发送 /ping，机器人回复 pong\n"
                "- 确认状态：确认后发送 /status；取消时不发送\n"
                "- 再看 Markdown：点击发送 /debug markdown\n\n"
                "> 按钮填入内容和实际消息请一起观察。"
                "填写普通文字不触发机器人命令；所有命令仍检查当前用户权限。"
            ),
            Keyboard(
                rows=(
                    (
                        Button(
                            "打开平台",
                            "https://q.qq.com/",
                            action=ButtonAction.LINK,
                            id="debug_link",
                            style=0,
                            visited_label="再次打开",
                        ),
                        Button("填写 Ping", "/ping", id="debug_fill_ping", enter=False),
                    ),
                    (
                        Button("填写帮助", "/help text", id="debug_fill_help", enter=False, style=0),
                        Button("直接 Ping", "/ping", id="debug_send_ping", visited_label="再 Ping 一次"),
                    ),
                    (
                        Button(
                            "填写一句话",
                            "Lowlight，按钮填写测试：我准备好啦！",
                            id="debug_draft",
                            enter=False,
                            style=0,
                        ),
                        Button(
                            "确认状态",
                            "/status",
                            id="debug_confirm_status",
                            modal=ButtonModal("发送 /status 查询机器人运行状态？", "发送", "取消"),
                        ),
                    ),
                    (Button("再看 Markdown", "/debug markdown", id="debug_markdown"),),
                )
            ),
        )
