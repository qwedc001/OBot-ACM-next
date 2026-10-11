# 基础上下文

OBot ACM 面向算法竞赛与群聊日常。业务待接入；dashboard 参考 Floating-Ocean/OBot-EP。

## 开发约定

- Python 3.12、uv、NoneBot2，依赖锁定于 uv.lock。
- plugins 为原生入口；framework/services 为异步业务；framework/clients 为通用能力。Service/Client 继承基类、自动发现。
- 原生 on_command/handle/CommandArg/优先级；handler 显式 await ReplyClient.send/finish/send_to，不解释返回值。
- 公共代码用通用 Bot/Event/Message；平台差异归 framework/integration/platforms。
- 权限 everyone/group_admin/superuser，help 与执行共用鉴权。help 独立公开、默认图片、最多三栏；maintenance/debug 仅超管。命令按模块组织，短名用别名。
- 复用官方 LocalStore/ORM/APScheduler，耗时同步工作移入线程。

## 验证与部署

测试：uv run --extra render pytest -q；检查：uv run ruff check . 和 uv run ruff format --check .。测试隔离配置与数据库，不代替真实 QQ 验收。


## 约束与资料

不读取、输出或提交 .env.dev 凭据；允许运行时加载。不要擅自提交。.codex 必须 Git 忽略；笔记放 .codex/reference-docs。

设计 docs/framework.md；部署 deploy/README.md；研究 docs/migration-analysis.md。以源码为准。
