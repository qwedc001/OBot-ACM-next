# Dashboard 内容管理

工作台恢复 [OBot-EP](https://github.com/Floating-Ocean/OBot-EP) 的两项业务：PickOne 表情包和手动比赛列表。页面、字段定义、预览与冲突算法沿用原项目；接入现有 QQ 登录、Service 和 ORM，不保留 EP 的独立密码账号系统。参考版本为 `3e417697c31934008fd32ac7ca06f923b31ffd98`。

## 功能与权限

| 模块 | 普通用户 | 超级管理员 |
| --- | --- | --- |
| PickOne | 分类与图片浏览、搜索、原图与缩略图；申请类别、修改描述、别名、点赞与评论；查看及撤回自己的待审提交 | 审核、应用、冲突裁定；审核 `__AUDIT__` 中的图片 |
| 比赛列表 | 浏览、搜索、筛选；新增与修改比赛；查看及撤回自己的待审提交 | 审核、应用、冲突裁定；提交删除比赛 |
| 内容操作日志 | 不可访问 | 查看提交、撤回、审核、应用与图片审核记录 |

群内申请的 Dashboard 会话仍仅允许管理发起群的榜单，不能访问上述模块；需要私聊登录。后端每次请求重新检查账号权限，前端隐藏入口不是权限边界。写操作验证 Origin 和 CSRF。

修改先进入 `pending`。审核通过成为 `approved`，此时数据文件不变；管理员查看应用预览后确认写入，成为 `applied`。已通过的审核可以撤回，待审提交可由作者撤回。文件原值或其他已通过提交发生冲突时，提交进入 `conflict`，经裁定重新排队或驳回。预览不修改状态和文件。

比赛字段为平台、简称、全称、开始时间、时长、地点；修改按字段拆分提交，原文件的未知字段保留。比赛列表是手动维护的数据，不包含在线平台比赛聚合。

## 代码边界

```text
framework/services/curation.py    共享数据目录、异步工作流与 ORM 事务
framework/curation/repository.py  提交状态机及操作日志
framework/curation/pickone/       数据格式、校验、冲突与应用算法
framework/curation/contestlist/   比赛数据、校验、冲突与应用算法
web/content/                     请求模型适配、会话依赖、资源路由
web/plugins/                     现有 WebPlugin 注册入口
frontend/src/plugins/            对应页面、组件、API 与导航清单
```

原 EP 的文件处理算法保持同步，FastAPI 将同步资源处理放入 AnyIO 工作线程。Repository 只借用当前 ORM 事务，通过 `anyio.from_thread.run` 在原事件循环执行 SQL；不创建另一个连接池、数据库或用户表。新增机器人调用方可以 `await services.curation.run(operation, ...)`，`operation` 第一个参数为 Repository。不要从事件循环直接调用这些同步领域函数。

`CurationService` 的异步锁覆盖一次比较与写入流程，避免本进程两个应用操作相互覆盖。文件通过临时文件和原子替换保存，数据库由请求事务提交；文件和 SQLite 不构成跨资源原子事务，因此运维仍需备份整个数据目录，不能在应用过程中由其他进程同时写入。外部改动在下次应用前检查。

## 数据位置

在现有 LocalStore 应用数据目录内：

```text
curation/
  Pick-One/
    config.json
    <类别>/parser.json
    <类别>/<md5>.gif
    __AUDIT__/<类别>/<md5>.gif
    .thumbnails/
  Contest-List-Renderer/manual_contests.json
```

目录首次使用自动建立；没有数据时显示空列表。类别与比赛审核应用后创建相应 JSON 文件。图片管理沿用原格式，可管理放入这些目录的现有资源；此次不添加 QQ 上传、随机图片指令，也不自动复制原项目的私人数据。

数据库迁移 `0006_content` 新增 `bot_content_submissions`、`bot_content_logs`，作者直接引用已有 `bot_users`。内容数据与榜单等现有数据一起备份。损坏或不可读的 PickOne JSON 会阻止操作，不会作为空数据覆盖。

## 验证

```sh
uv run --extra render pytest backend/tests/test_curation.py backend/tests/test_dashboard.py
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend test:e2e
```

浏览器测试使用临时目录、空环境文件和合成图片，覆盖真实 HTTP 登录、浏览、提交、审核、预览与应用，截图与运行产物写入被忽略的 `.codex/`。
