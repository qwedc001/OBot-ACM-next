# 基础上下文

OBot ACM 面向算法竞赛与群聊日常；Python 3.12、uv、NoneBot2。

- plugins 为原生入口，framework/services 为异步业务，framework/clients 为通用能力；Service/Client 继承基类、自动发现。
- 指令统一 Alconna 模板/assign，每个功能独立 handler。Plugin 解析，Service 接收业务参数；handler 显式 await ReplyClient.send/finish/send_to，不发送返回值。
- 公共代码用通用 Bot/Event/Message，平台差异归 framework/integration/platforms；同步耗时工作移入线程或进程。
- 权限 everyone/group_admin/superuser；help 与执行共用鉴权。help 独立公开、默认图片、最多三栏；maintenance/debug 仅超管。
- 不自动注册目录或 QQ 面板；短名用别名，菜单不填运行例子。复用 LocalStore/ORM/APScheduler。
- Peeper 用 third-party/peeper-board-generator 固定 submodule，不改上游；个人/群配置隔离，网页一次性绑定，凭据加密，群绑定由发起群管确认。
- 版本见 docs/versions.md：迁移期 0.x.y，功能升 x，修复升 y；原功能迁移完成后才 1.0.0，可用 alpha/beta。
- 公开文件只用示例 endpoint，不写个人部署地址或凭据。
- 不读取、输出或提交 .env.dev；允许运行时加载。未经授权不提交。.codex 必须忽略，笔记放 .codex/reference-docs。

验证：uv run --extra render pytest -q；uv run ruff check .；uv run ruff format --check .。测试隔离配置与数据库，真实 QQ 另行验收。

资料：docs/framework.md、docs/commands.md、docs/boards.md、docs/migration-analysis.md；部署 deploy/README.md。
