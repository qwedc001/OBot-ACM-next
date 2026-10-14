# 命令模板与独立 Handler

所有业务 Plugin 使用 `template_command` 声明 Alconna 语法，用 `assign` 将每个子命令绑定到一个独立的异步 handler。禁止在总 handler 内通过 `shlex`、`split`、`argv[0]` 或大段 `if/elif` 选择功能。参数声明、必填与可选参数、枚举取值、引号与嵌套子命令都交给 `nonebot-plugin-alconna`。

## 注册与执行

```python
from arclet.alconna import Args, Subcommand
from framework.catalog import CommandSpec
from framework.integration.commands import template_command
from framework.integration.command_context import BotServices, CommandContext, Reply

board = template_command(
    CommandSpec(
        id="cp.board",
        plugin_id="cp",
        name="board",
        aliases=("board",),
        summary="训练榜单",
        usage="/board bind 配置名；/board today [配置名]",
    ),
    Subcommand("bind", Args["name", str]),
    Subcommand("today", Args["name", str, None]),
    priority=10,
    block=True,
)


@board.assign("bind")
async def handle_board_bind(context: CommandContext, services: BotServices, reply: Reply, name: str):
    await reply.finish(await services.boards.issue(context, name))
```

`/board bind xxxOJ` 只执行 `handle_board_bind`；`name` 由 Alconna 注入。匹配与执行仍在 NoneBot Matcher 中进行，复用其优先级、依赖注入、状态、权限钩子和 finish 控制流，不建立自己的分派引擎。`template_command` 只登记目录元数据、平台规则、上下文状态和统一参数错误处理。

根命令的无子命令入口用 `assign("$main")`，如 `/board` 展示用法。嵌套语法用嵌套 `Subcommand`，如 `user -> name` 配合 `assign("user.name")`。没有参数和子命令的指令也用同一入口和 `$main`，如 ping、whoami、status、一言。

参数默认值应同时体现在模板与 handler 签名里。枚举取值使用 `Literal`；带空格的名字用引号，如 `/board user name "Alice Smith" xxxOJ`。不将原始参数全文或解析异常直接回复、写日志，统一返回安全用法提示。模板匹配不能代替业务校验：日期合法性、配置存在性、管理范围等仍由 Service 验证。

## 职责与共享

一个 handler 负责一个功能，只获取已解析的参数、调用 Service/Client、显式 await 发送和结束。允许提取公共流程，比如图片生成进度与发送、帮助消息发送、已知业务错误处理；公共函数不重新检查 action 字符串或选择功能，也不自动发送 handler 返回值。

Service 不接收整条指令字符串，不解析 `/help text` 或 `board bind`，而是接收明确的业务参数，例如 `HelpService.reply(context, query, draw=False)`。Client 保持与指令语法无关。当前指令参数都是纯文本；消息来源扩展直接读取 NoneBot Event 正文，避免依赖平台专用的 UniMessage 事件 ID 转换器。未来添加图片等富媒体入参时须扩展消息来源与相应模板，回复能力仍由 ReplyClient 管理。业务条件、能力检查、场景与权限判断仍允许使用条件语句；需要去掉的是语法分派的条件树。

命令级权限继续由 CommandSpec 声明，帮助与执行共用检查。个人/群配置等业务范围由 Service 重新鉴权，群管身份不会获得普通私聊管理权限。关闭 Alconna 内建帮助、快捷注册及自动输出，避免出现另一套绕过权限过滤的帮助或发送方式。ReplyClient 继续发送文本、图片、Markdown 和按钮；不改用 UniMessage 自动发送。

新增功能必须同步更新当前权限可见的用法、CommandSpec 示例与公开文档，并添加实际 Matcher 测试。测试至少覆盖独立 handler 分派、可选参数、缺失与多余参数、引号、别名、权限拒绝、共享流程的有序发送。不得重新启用自动目录命令、QQ 面板或菜单发布。

## 当前模块

- board：bind、confirm、unbind、list、version、push、starred、today、yesterday、history、verdict、user.id、user.name 各有独立 handler。
- debug：根帮助、markdown、button 独立。
- help：默认图片、text、render 独立；显示参数不再在 HelpService 中解析。
- maintenance：ping、whoami、status 各自 `$main`。
- fun：hitokoto 的 `$main`；后续功能各自声明语法与 handler。

低层 `command` 仅保留 NoneBot 原生 command/prefix/keyword 注册，用于原生控制流和集成验证；业务模块统一采用 `template_command`。两者共用相同的 CommandSpec、权限、上下文和耗时记录，不引入另一套调度器。