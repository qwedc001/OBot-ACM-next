# OBot 共享框架

## Plugin、Service 与 Client

Plugin 是 NoneBot 入口，声明命令、别名、帮助与权限，获取事件上下文，再调用异步 Service。Service 负责应用流程和业务决策，可以组合多个 Client；也可供其他插件或后续内部 API 复用。Client 是通用能力对象，例如 HTTP、渲染、图片产物或工作队列，Service 和 Plugin 都可以直接调用。

```text
backend/framework/
  services/        Service、IdentityService、HelpService、MaintenanceService
  clients/         Client、HttpClient、RenderClient、ArtifactClient、JobQueue、ReplyClient
  integration/     NoneBot 原生命令、依赖注入、权限、生命周期与内部 API
    platforms/     Platform 契约与 QQ 等平台的具体实现
  views/           Pixie 帮助布局
  messages.py      Text / Image / Markdown / Keyboard / SendTarget
  models.py        官方 ORM 应用模型
  catalog.py       指令与帮助的统一声明
  runtime.py       动态发现、依赖与生命周期
backend/plugins/
  help.py          独立 /help
  maintenance.py   仅超管的维护指令
  debug.py         仅超管的 /debug markdown /debug button
backend/migrations/ 标准 Alembic 修订
```

Service 与 Client 分别继承自己的抽象基类。最小公共 Component 仅提供 `build(container)`、`dependencies`、异步 `start()` / `close()` 契约，避免复制生命周期实现。业务方法不统一强制为某一种 handler 签名；各 Service 提供自己的明确异步接口。

运行时扫描两个包，只发现模块自己定义的具体子类，忽略导入类、抽象类、普通辅助类；每模块一个实现，文件名为容器键。访问 `services.help` 获取业务服务，访问 `services.clients.http` 获取通用 Client。跨类依赖用 `clients.http` 等显式名称；两类依赖统一构造单例、依赖先启动、消费者先关闭。循环、未知依赖、多个实现与错误工厂会显式报错；启动失败释放已经构造的对象，关闭错误不阻止其他对象清理。重新启动会构造新的实例。

新增业务服务示例，放在 `backend/framework/services/contest.py`，无需修改注册列表：

```python
from framework.services import Service


class ContestService(Service):
    dependencies = ("clients.http",)

    @classmethod
    def build(cls, container):
        return cls(container.clients.http)

    def __init__(self, http):
        self.http = http

    async def list(self, endpoint: str):
        data = await self.http.get_json(endpoint)
        return data
```

HTTP Client 不知道指令与 matcher。RenderClient 只接收 Actor、view 与普通模型，返回图片产物；帮助查询、权限过滤和回复格式由 HelpService 决定。IdentityService 提供应用账号解析与超管登记查询；ArtifactClient 注入身份授权回调，读取图片时检查所有者或真超管。

## 异步与并发

`async def` 本身不能消除同步阻塞。HTTP 使用复用的 `httpx.AsyncClient`，数据库直接使用 ORM 异步会话；JSON 解析、文件读写、摘要计算和 Pixie 绘制交给工作线程。纯内存注册和配置方法保持同步。Plugin 不应直接调用 requests、同步磁盘读取或 Pixie；已有同步业务库必须在 Client 边界转入线程，需要硬性终止的工作改用子进程。

JobQueue 限制 worker 数、排队容量和任务耗时，同 key 保序，不同 key 可以并行；简单维护指令直接执行，不进入绘图队列。Pixie 在线程内加锁，取消等待无法停止原生绘制，但不会使下一次绘制与尚未结束的调用重叠。它是通用执行 Client，不包装定时调度器。

HttpClient 按 origin 限制并发，GET/HEAD 暂时性错误有限重试，POST 不自动重试；错误不包含查询参数、响应正文或凭据。后续各平台 Client 在此之上提供领域数据与缓存。

## 现成插件与数据库

文件目录使用官方 LocalStore；连接池和会话使用官方 ORM；定时任务直接调用官方 APScheduler 的 `scheduler`。不存在自写 storage、scheduler 或复杂 policy 层。业务模块需要数据库时直接声明 `nonebot_plugin_orm.Model`，使用 `get_session()` 与 SQLAlchemy 事务；需要定时任务时直接 `scheduler.add_job(...)`，由插件管理启动和关闭。

默认数据目录为 OBOT_DATA_DIR，支持 LOCALSTORE_DATA_DIR；默认数据库 `bot.sqlite3`，SQLALCHEMY_DATABASE_URL 可覆盖。当前身份解析使用 SQLite 的 conflict insert，因此当前实现只支持 SQLite。模型为 bot_users、bot_identities、bot_artifacts；新业务模型必须加入标准迁移。

```sh
uv run --no-sync python -m framework.migrate
uv run --no-sync python -m backend.bot
```

保留 ORM 的 Alembic 启动检查，不使用自动 sync。0.3.0 明确 breaking change，不转换旧 records/operations、规则、角色、ACL 或 revision 数据。原数据库只作为部署备份，不进入新运行路径。

## 三类权限

| 权限 | 范围 |
| --- | --- |
| everyone | 普通私聊和群消息 |
| group_admin | 当前群群主/群管，或当前频道管理者；超管也放行 |
| superuser | 机器人管理员，所有指令与场景限制均放行 |

组合 NoneBot MESSAGE / SUPERUSER 与当前 Platform.group_admin，没有额外角色规则引擎。QQ 实现使用官方 Adapter 的 GROUP_ADMIN、GROUP_OWNER、GUILD_ADMIN、GUILD_OWNER；其他平台在自己的实现中使用原生权限或 API。群管身份不会提升私聊权限；不可用的角色不放行管理功能，事件格式由各自适配器验证。

User.superuser 持久保存机器人管理员身份。已核实属于同一个人的不同 QQ 外部身份可由可信本机操作者关联到同一 User；之后群聊/私聊共用权限，撤销立即生效。不会自动把第一个发消息的人设为管理员。NoneBot SUPERUSERS 和 OBOT_SUPERUSERS 也可配置显式管理员；配置的权限需要从配置中撤销。

```sh
uv run --no-sync python -m framework.admin link-identity qq:APP:group_member:OPENID USER_ID
uv run --no-sync python -m framework.admin superuser add USER_ID
uv run --no-sync python -m framework.admin superuser remove USER_ID
```

这些维护入口只面向具有服务器数据库访问权的操作者，不暴露为 QQ 或公开 HTTP 指令。普通用户不能运行 maintenance 获取 whoami。

## 指令与帮助

所有业务 Plugin 使用 Alconna 的声明式模板与独立 handler，统一调用 `template_command`。语法解析留在 Plugin，Service 接收明确业务参数；具体约定、示例和各模块拆分见 [命令架构指引](commands.md)。优先级、依赖注入、权限、状态和会话结束由 NoneBot Matcher 管理。

`BotServices`、`Reply`、`CommandContext` 是原生 Depends 的类型别名。Plugin 可在多个 await 之间顺序发送消息；Service 返回业务数据，handler 决定收发顺序。等待慢 Client 时其他事件继续处理。

maintenance 包含 ping、whoami、whoisthis、status；其中 whoami 为 everyone，显示普通用户、群管理员或超级管理员的实际角色，其余要求 superuser。正式路径 `/maintenance.ping` 等，短别名 `/ping` 等。help 位于独立 `backend/plugins/help.py`，正式路径 `/help`，everyone 可用，默认图片；`/help text` 显式文字。

`/help` 展示当前用户所有可执行的已注册命令，包括已授权的 hidden 项；`/help maintenance` 展开模块，`/help ping` 或 `/help maintenance.ping` 展示详情。目录由可见命令推导，不显示不可访问的模块。系统 COMMAND_SEP 默认英文句点，可切换其他标点；example_alias 允许示例使用已注册短别名，maintenance 已设置。maintainence 是帮助目录纠错别名。

帮助图沿用原版 OBot 的深色 Pixie 样式、模块标题分隔线和参数标记。按实际折行测量高度，长模块优先放到最短栏，支持四栏及更多栏；同时选择正文栏宽，优先接近公共 4:3 目标。整模块能放下时保持同栏，否则按完整卡片续栏并重复标题，不裁剪或遗漏指令。图高不超过 5120，宽度和像素总量受公共布局限制；仅超限时分页。排队期间权限变化时重新生成文字；渲染依赖缺失或渲染失败时回退完整过滤后的文字。

OBot 自行渲染的图片复用 `views.footer.CardFooter`，包含 `OBot's Next ACM Bot`、公共项目地址、`© OBot-Next-Dev-Team`、当前 `framework.__version__` 与配色，分页图每页保留。信息区参与布局测量；深浅画布只调整文字颜色，规范见 [视觉设计](design.md)。第三方工具（如 Peeper）通过官方 CLI 生成图片，保留其原有布局与署名，不追加 OBot 信息区；尺寸合规时原字节交付，只在超限时等比例缩小。新增自绘 View 接入同一信息区组件，并复用 `views.layout` 的目标比例、边长和像素预算；以内容重排接近 4:3，避免缩小字体或拉伸图片。

图片随机 ID、大小限制、摘要和 TTL 由 ArtifactClient 管理；只有请求者或真超管能读取。lease 避免清理正在发送的文件，cleanup 删除过期产物。目前没有自动清理定时任务、孤儿文件恢复或跨进程 lease。文件与数据库不是一个原子事务，崩溃可能留下孤儿文件；dashboard 不应公开产物目录。不支持图片的平台直接使用完整的过滤后文字帮助。

## 异步 ReplyClient

`services.clients.reply` 提供 `send(content, *, bot=None, event=None, at_sender=None)`、`finish(content=None, ...)` 和 `send_to(content, target, *, bot=None)`。send 发送后继续；finish 成功发送后抛出原生 FinishedException，结束当前 matcher，也可以不发送直接结束。send/finish 默认使用当前 matcher 的 bot/event；异步任务或业务层可显式传入。send_to 不依赖 matcher，也不伪造入站事件；可指定 bot，或按目标 adapter/bot_id 选择唯一连接机器人，多机器人歧义时报错。at_sender=None 使用平台默认行为，True/False 显式覆盖。

```python
from framework.messages import (
    Button,
    Keyboard,
    Markdown,
    Message,
    Image,
    SendTarget,
)

reply = services.clients.reply
await reply.send("正在处理")
await reply.send(Message("结果", Image(image_bytes)))
await reply.send_to("比赛提醒", SendTarget("QQ", "group", group_openid, bot_id=app_id))
await reply.finish(
    Message(
        Markdown("# 操作菜单"),
        Keyboard(rows=((Button("查看帮助", "/help", id="help"),),)),
    )
)
```

消息数据与 NoneBot Adapter 无关：Text 或字符串为文本；Image 接受 bytes、HTTP(S) URL、Path，本地路径在线程读取；Markdown 接受原始内容或 `Markdown.template("模板ID", title="标题", items=["a", "b"])` 的自定义模板参数。Keyboard 使用 `template_id` 或二维 rows，两者互斥。Button 支持指令（默认）、链接、回调和原生跳转动作，点击后文字、样式、自动发送 enter、reply、分组、二次确认 modal 与不支持提示。ButtonPermission 的 EVERYONE/ADMIN/USERS/ROLES 是 QQ 点击者限制，需要对应 QQ 原生用户/身份组 ID；不能传框架 User ID，也不会替代指令的 everyone/group_admin/superuser 鉴权。回调按钮需唯一 id，业务 Plugin 自行处理官方适配器的互动事件，ReplyClient 不注册业务回调。

QQ 转换和主动目标路由位于 `framework/integration/platforms/qq_messages.py`；Client 使用已注册的异步转换/发送函数，扩展其他平台无需修改消息数据和 Client。`supports(adapter_name, part_type)` 查询平台声明的类型能力；不支持的段抛 UnsupportedContent，不静默删掉 Markdown 或按钮。类型支持不保证组合、模板权限或按钮特性都可用，这些限制由平台实现进一步检查。QQ 主动 kind 为 c2c（用户 openid）、group（群 openid）、channel（频道 ID）、dms（已有频道私信会话 guild_id）。频道私信会话创建与 QQ 主动消息的权限、额度仍由平台管理。

当前 QQ Keyboard 必须与 Markdown 同发，自定义布局最多五行、每行五个按钮；单消息最多一个 Markdown/Keyboard 或一张媒体图片。官方 Adapter 会选择最后一个同类段；转换层提前拒绝多图、多 Markdown、多 Keyboard 及图片与 Markdown/Keyboard 组合，防止内容丢失。图文混排可在 Markdown 内引用公网图片链接；多张上传图片则拆为多个 Message。模板和自定义 Markdown/按钮能否被腾讯接受取决于机器人平台已开通的能力，不自动降级丢弃内容。

官方 Bot.send 管理被动回复的 msg_id 和递增 msg_seq；send_to 使用官方 send_to_*，由 Adapter 管理图片上传与消息类型，不维护额外发送锁或上传协议。SDK 发送异常转换为脱敏 DeliveryFailed，取消信号原样传播；构建参数错误显式报错。QQ 默认群/频道 at_sender=True、私聊False。群普通回复由平台自动标明发送者，Markdown 则需要正文标签；适配器 1.7.3 的局部补偿独立放在 [backend/patches](../backend/patches/README.md)，不改变 ReplyClient 公共接口。ReplyClient 也支持普通 GroupMessageCreateEvent 的被动回复，这只表示发送实现支持该事件，不改变 Adapter 的事件订阅。

HelpService 返回字符串或 tuple[Message, ...]，读取图片前校验访问权限并持有 lease，读取后再次检查帮助权限。合成测试执行真实 matcher、Bot.send 和 send_to_*，只 mock 最终 API，覆盖文本、图片上传、Markdown 参数、按钮、主动四场景、普通群消息、并发回复序号和 finish 控制流。SAA 不再作为依赖；这里复用其消息构建/被动与主动发送分离的思路。

## 添加其他平台

`Event` 表示入站事件，`Message` 表示消息内容，二者不能互相替换。公共 handler 使用 `nonebot.adapters.Bot/Event/Message`；`MessageEvent` 这类具体事件通常由各 Adapter 定义，并不是统一的跨平台基类。需要读取特殊字段时留在对应平台实现或专用 Plugin。

`framework/integration/platforms/base.py` 的 Platform 是跨平台接入契约：

| 接口 | 责任 |
| --- | --- |
| adapter_type | 声明实际 NoneBot Adapter，加载器注册到 Driver |
| normalize(bot, event) | 返回 Incoming：带 provider 的 ExternalIdentity、SceneRef 和稳定事件 ID |
| group_admin(bot, event) | 当前群/频道原生管理权限；无法判断时返回 False |
| supported_segments | 平台支持的 Text/Image/Markdown/Keyboard 类型集合 |
| build_message(recipient, message, at_sender) | 异步构建该平台原生 Message，校验不支持的内容和组合 |
| send_to(bot, target, message) | 主动目标路由和原生 SDK 发送 |

新增平台的步骤：

1. 使用 uv 添加对应 NoneBot Adapter 依赖，配置它需要的驱动和账号。
2. 在 `framework/integration/platforms/<name>.py` 定义一个具体 Platform 子类。事件字段、SDK MessageSegment、上传 API 和目标 kind 均留在这里；较长的实现可拆为同包辅助文件。
3. 将 name 加入 `OBOT_PLATFORMS=["qq", "<name>"]`。加载器只加载明确配置的模块，发现其唯一具体子类；启动时自动注册原生 Adapter 和 ReplyClient 转换器，不需要在 bot.py 或 Client 里新增分支。
4. 为平台补充入站事件、权限、图文转换及主动目标的合成测试，随后验证真实平台收发。测试账号使用临时空配置，避免读取真实凭据。

公共身份、场景、去重键、连接状态和回复按平台隔离；同一个人的跨平台账号必须可信关联，不能仅凭相同数字 ID 自动合并。当前实际实现仅 QQ，不继承 SAA 的整套平台覆盖；`tests/platform_probe.py` 的非 QQ 与纯文字 Adapter 验证公共维护/帮助插件、群管权限、主动发送及能力回退，无外网调用。

## 发送能力调试

`plugins/debug.py` 注册根 `/debug`，只允许 superuser；CommandArg 的 markdown/button 参数选择两个样例，未知参数或无参数返回目录生成的文字用法。`/help debug` 可查同一份说明，非超管的帮助不显示入口。纯文字平台明确提示未实现该能力。

样例由自动发现的 `framework/services/debug.py` 构建，handler 显式调用 ReplyClient，不在 Service 中直接调用 QQ SDK。Markdown 样例覆盖标题、加粗/斜体/删除线、引用、有序/无序列表和分隔线，没有外部资源，避免 URL 配置影响基础 Markdown 测试。

按钮样例为原生 Markdown 加自定义 Keyboard，七个按钮排列四行：打开 QQ 开放平台链接；填入 /ping、/help text 或一句中文且 enter=False；直接发送 /ping；二次确认后发送 /status；发送 /debug markdown。填入普通文字不触发任何机器人指令，命令按钮触发后继续按发消息人的权限执行。没有注册虚假的回调按钮。SDK 被动回复继续管理 msg_seq；发送拒绝时同一事件再发送明确文字诊断，不静默假装成功。

腾讯文档区分主动和被动 Markdown 授权，当前测试是被动原生 Markdown，不使用模板；按钮的链接域名 q.qq.com 需按开放平台要求配置。参考 [Markdown 官方说明](https://github.com/tencent-connect/bot-docs/blob/main/docs/develop/api-v2/server-inter/message/type/markdown.md)、[消息发送与 URL 配置](https://github.com/tencent-connect/bot-docs/blob/main/docs/develop/api-v2/server-inter/message/send-receive/send.md)、[按钮与二次确认定义](https://github.com/tencent-connect/botgo/blob/master/dto/keyboard/keyboard.go)。测试仅检查原生命令、权限和最终 API 载荷，真实展示、跳转、填写与确认仍需 QQ 验收。

## QQ 与 Dashboard

### 私聊底部菜单

腾讯官方新接口 [GET /v2/menu](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_menu.get.html) 与 [PUT /v2/menu](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_menu.put.html) 支持两级自定义菜单，仅 C2C 私聊生效，所有用户共享。QQ Adapter 1.7.3 尚未封装这两个 API；平台接入层使用其原有 `_request`，保留 SDK 鉴权、token 刷新和请求驱动，不增加另一套鉴权。

`CommandSpec.menu_label` 声明菜单短说明；QQMenuClient 调用 API，MenuService 从同一 Catalog 生成模块和命令按钮。一级最多十项、名称最多十个字符，二级最多五项、名称最多十四个字符，中文按两个字符计。每模块预留“指令说明”打开 `/help text 模块`；菜单本身没有 desc 字段，完整说明和例子由权限过滤后的帮助展示。大模块拆为多个同级菜单，不静默遗漏命令；总数超过 API 容量时拒绝发布。

可信服务器操作者可以运行以下 CLI。默认仅展示 everyone、当前私聊场景可用且非 hidden 的功能；`--include-admin` 会公开带权限标记的管理员入口，不是按账号隐藏。菜单点击会直接发送消息，因此只设置命令本身（优先已声明的短别名），不填入运行范例或示例参数；帮助中的范例仍按声明展示。发送后仍执行正常鉴权。

```sh
uv run python -m framework.menu
uv run python -m framework.menu --apply
```

同步前备份完整菜单，未变化时不写；修改后 GET 读回比对，仅忽略 QQ 自动补充的 icon 字段。不在每次启动或重连时覆盖配置，不改指令面板或群聊菜单。默认备份在 LocalStore 的 menu-backups 目录；`--backup` 可指定路径，已有备份拒绝覆盖。修改频率上限五次/分钟，CLI 正常发布一次需 GET/PUT/GET。

### 按需调用的指令面板 API

[指令面板 API](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels.post.html) 支持私聊和群聊注册可发现的命令。目前不按 Catalog 自动注册一级命令或生成面板，不在启动、重连时发布。模块帮助继续使用 `/help fun`，`/fun`、`/maintenance` 这类目录入口没有 Matcher。已有业务命令和别名仍由 Plugin 显式声明。

`services.clients.qq_menu` 保留异步 `panels(bot, scope)`、`create_panel(bot, scope, panel)`、`get_panel(bot, panel_id)`、`update_panel(bot, panel_id, panel)`、`delete_panel(bot, panel_id)`，供以后确定内容后显式调用。创建接口面向指定 scope 的全局 all 面板，panel 的 remark/items 由调用者提供，不猜测注册内容；查询处理分页。复用 QQ Adapter 的鉴权、token 刷新和原有请求入口，不单独维护另一套 HTTP 或鉴权。

```python
# 由可信操作者明确选择内容后调用；这段代码不会在启动时执行。
client = services.clients.qq_menu
panel_id = await client.create_panel(
    bot,
    "c2c",
    {"remark": "chosen-commands", "items": [{"type": "command", "name": "/hitokoto", "desc": "随机一言"}]},
)
panel = await client.get_panel(bot, panel_id)
await client.delete_panel(bot, panel_id)
```

面板负责平台展示，不会创建 NoneBot 的指令处理函数，也不替代执行权限检查。原先本项目发布的两个一级目录面板已撤下；私聊底部菜单是独立接口，仍通过前述显式 CLI 管理。面板原始创建、修改、删除接口的约束由 [腾讯官方文档](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels_panel_id.delete.html) 定义。

OBOT_QQ_TRANSPORT 的 configured 模式使用 QQ_BOTS 原设置，websocket/webhook 显式覆盖接收方式。当前服务器使用 WebSocket 接收，HTTP API 发消息不变；WS 模式 webhook POST 返回409。需要检查 Bot connected，而不是只检查 service active。

网页工作台已接入 EP 的插件与主题体系，见 [网页架构](web.md) 与 [视觉设计](design.md)。登录使用私聊 `/dashboard` 一次性链接，业务 API 与机器人共享 Service、账号及权限。内部 /internal/v1/health 与 /internal/v1/commands 默认404；开启后需要实际回环连接与 Bearer 令牌，不信任 X-Forwarded-For。机器令牌不冒充 QQ 群管理者，commands 只返回 everyone 可用且非 hidden 的指令。返回正式 path、directory、别名、权限、separator、用法和格式化示例，可用于同一棵帮助树。

health 提供版本、就绪、指令数、队列、连接状态、最近收发及最近100条处理结果与耗时，不保留消息正文。计时从 matcher 首个上下文 handler 开始，包含身份、处理和平台发送等待，不包含平台分发或客户端实际送达时间。并行发送等待按时间区间并集统计，不重复累计。发送成功只表示平台接受请求。重复事件缓存是有界进程内去重，不是业务 exactly-once 保证。

Caddy 将网页、/api/* 与 /qq/* 代理到同一后端，并阻止公网 /internal/*；内部机器令牌不能交给前端。网页群榜单基于原生群权限的短时记录授权，跨场景身份可用群内一次性验证关联；凭据不传到浏览器，运行状态仅超管可见。后续工具沿用前后端插件清单接入共享 Service。根 AGENT.md 只保留千字以内基础指引；详细记录放在不提交的 .codex/reference-docs/。

## 竞赛账号资料卡

竞赛账号查询的 Client、Service、统一模型、指令与绘图拆分见 [账号资料卡](profiles.md)。平台差异只在各自 Client，公共卡片不获取网络数据；新增账号平台沿用这一边界。

## 趣味工具：一言

`backend/plugins/fun.py` 提供 everyone 可用的 `/fun.hitokoto`，短别名 `/hitokoto`、`/一言`、`/来句`、`/来一句`、`/来句话`、`/来一句话`。`/help fun`、`/help hitokoto` 与全局帮助自动展示同一份命令声明。

HitokotoClient 通过共享 HttpClient 异步访问 [一言语句接口](https://developer.hitokoto.cn/sentence/)，校验正文/作者/出处。成功结果缓存两秒，合并同时请求，限制公益接口请求突发；等待和网络总耗时最多八秒，不缓存失败。HitokotoService 保留原有 `[Hitokoto]` 与作者/出处格式；缺少作者时不显示 None，缺少出处显示“未知出处”，请求失败提示稍后重试。Plugin 使用 ReplyClient 显式回复，各平台复用这条业务链，无数据库或新依赖。

## 验证

Hydro 训练榜单、一次性网页绑定和固定提交的生成器 submodule 见 [榜单说明](boards.md)。`/board` 是本次明确注册的业务别名；仍不自动注册目录或 QQ 指令面板。

`uv run --extra render pytest -q` 使用临时空环境文件和数据库，测试 Service/Client 自动发现、跨类依赖、逆序释放、失败清理、原生三类权限、超管两场景一致、默认图片/显式文字、分栏分页、并发与原生绘图取消。合成 probe 执行真实 NoneBot 分发、原生命令优先级/block、参数/别名/分隔符、等待 Client 期间多事件并发、顺序多回复、finish 和会话续接重新授权，并运行非 QQ 平台与官方 ORM/LocalStore/APScheduler 生命周期。不连接外部平台，不能代替真实聊天验收。

版本编号与发布规则见 [版本说明](versions.md)。


### 查询他人主体

超管在群内发送 `@机器人 /whoisthis @某人`，或 `/maintenance.whoisthis @某人`，查询目标的主体、外部身份、当前场景与超管登记状态。一次只接受一位用户；重复提及同一人会去重，机器人和全体成员不作为目标。普通用户与群管不能查询，该指令不会授予或撤销权限。

QQ 接入层优先使用官方提及元数据的 `member_openid`，不把显示用的 `id` 当作群成员身份。对于未登记用户，完整元数据允许建立默认无超管标记的身份记录，后续该用户真实消息沿用同一主体。旧消息仅有提及文本 ID 时，只查询已有记录，不能据此猜测和创建新身份。

部分群消息实测在平台推送时已没有目标提及文本与 `mentions`。此时可以尝试引用回复目标用户的消息，发送 `@机器人 /whoisthis`；接入层通过 `Platform.reply_author` 读取原生适配器已按引用索引匹配的作者，只接受群作者的 `member_openid` 或频道作者的稳定 ID。引用缺作者、仅有显示 ID、索引不匹配时明确返回无法识别，不按昵称猜测。已有明确提及时以提及为准。2026-10-17 Android QQ 实测引用消息也没有提供作者；这一备用路径仅在平台确实提供作者时可用。原始推送与 SDK 处理的区别、上游 issue 和补丁边界见 [适配器补丁说明](../backend/patches/README.md)。

查询后仍由可信服务器操作者使用 `python -m framework.admin superuser add 主体ID` 登记超管。群身份与私聊身份仅在可信关联后共享主体，不按相同显示 ID 或昵称自动合并。通用 Plugin 使用 Bot/Event，提及映射由 Platform.mentioned_users 提供，引用作者由 Platform.reply_author 提供，其他平台可以独立实现；未实现时不猜测目标。隔离测试从 QQ Dispatch 进入原生 Bot.handle_event，覆盖引用预处理与 NoneBot 分发。
