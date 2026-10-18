OBot ACM 算法竞赛与群聊机器人；Python 3.12/uv/NoneBot2，Vue3/Element Plus。

- backend/plugins 为原生入口，backend/framework/services 为异步业务，clients 为通用能力；Service/Client 继承基类、自动发现。backend/web 承接 HTTP；frontend/src/plugins 自动发现路由与 API。
- 指令用 Alconna 模板/assign，每功能独立 handler；显式 await ReplyClient.send/finish/send_to，不发送返回值。公共消息使用通用 Bot/Event/Message；平台差异归 integration/platforms，SDK补丁独立放 backend/patches，耗时同步工作进线程/进程。
- 权限 everyone/group_admin/superuser，help 与执行共用鉴权。help 独立、默认图片；whoami 公开，其余 maintenance/debug 仅超管。/dashboard 私聊登录；群管可在群内申请、私聊收链接，会话仅管理发起群榜单。群权限短时有效，凭据不出后端。
- 不自动注册 QQ 面板；短名用别名。复用 LocalStore/ORM/APScheduler。自绘图复用 CardFooter；第三方图片不加 OBot 信息区。见 docs/design.md。
- third-party/peeper-board-generator 为固定 submodule，仅经官方 CLI／配置／缓存文件交互，不导入内部模块、不做布局或时钟 patch；个人/群榜单隔离、凭据加密，群绑定需原群确认。
- 迁移期 0.x.y：功能升 x、修复升 y；完成迁移才 1.0.0，可有 alpha/beta。
- 公开文件仅示例 endpoint。不读、输出或提交 .env.dev，允许运行时加载。未经授权不提交；.codex 必须忽略，详细笔记放其 reference-docs。

新功能在 codex/ 分支开发，测试后合并 master；稳定基线及分支更新见 docs/updating.md。环境、数据、密钥不入 Git；测试隔离。
