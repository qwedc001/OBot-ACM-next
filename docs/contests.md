# 比赛查询

所有用户均可查询。私聊直接发送指令，群聊按平台要求 @机器人；比赛列表与单场详情默认生成图片，不支持图片或渲染失败时返回文字。

| 指令 | 功能 |
| --- | --- |
| `/contest` | 近日比赛，等同 `/contest recent` |
| `/contest today` | 与今天有时间交集的比赛 |
| `/contest recent cf` | 仅查看 Codeforces 近日比赛 |
| `/contest today manual` | 仅查看工作台维护的今日比赛 |
| `/contest detail ABC` | 按名称、简称或查询标识检索详情 |
| `/contest detail cf:1234` | 按图片中的标识精确查询 |
| `/help contest` | 查看当前用法 |

平台参数可用 `all`、`cf`、`atc`、`nk`、`manual`，省略时为 `all`。`/比赛`、`/近日比赛`、`/最近的比赛`、`/contests` 是 `/contest` 的别名；`/今日比赛`、`/今天比赛`、`/今天的比赛`、`/今日的比赛` 直接进入今日列表，也可附平台参数。详情通过 `detail` 子指令查询，名称可含空格。

## 数据与时间

- Codeforces：官方 `contest.list`，不含 Gym。
- AtCoder：比赛页的进行中、即将开始和最近结束列表，不含常驻练习。
- 牛客：竞赛页的牛客系列赛和高校比赛，按比赛 ID 去重。
- 手动比赛：复用 Dashboard 的 `ContestStore`，只读取管理员已应用的数据；待审和已批准但未应用的提交不可见。应用修改或删除后，下次查询立即生效。

所有时间显示为北京时间。今日列表采用时间区间交集：跨午夜且仍在今天进行的比赛计入，恰好在今天零点结束的比赛不计入。

supplement 沿用原项目内容：Codeforces 为赛制；AtCoder 和牛客为“不计分”“为所有人计分”“为 0-1599 计分”等计分范围；线下比赛使用 Dashboard 录入的地点。不额外拼接 `Rated`、原网页限制说明或其他解释，保留原字号与换行规则。

近日列表展示正在进行和未来 31 天窗口内的比赛（截至今天零点起第 31 天，不含该边界）。已结束比赛按来源保留今天的比赛；若当天没有，则保留该来源最近结束的一场。Codeforces 的系统测试状态会单独标注，最多延续至计划结束后七天。

详情查询限于当前拉取到的目录；AtCoder 和牛客页面没有列出的历史比赛不保证可查。优先匹配完整标识、简称、名称，再做包含和近似匹配；多候选时返回文字和查询标识，由用户选择。匹配单场时返回固定宽度的详情卡片，展示开始与结束时间、时长、状态、补充信息和查询标识；近似匹配会明确标注。在线比赛的官方链接同时附在消息文字中，方便点击。

在线数据缓存 60 秒，并合并并发请求；筛选和比赛状态每次重新计算，手动数据不缓存。某一来源失败时继续显示其他来源，并明确标注缺失来源，不把请求失败当作没有比赛。

列表按进行中、即将开始、已结束分组，各组上下排列。沿用原 OBot 的数量规则：取最大状态组的场数，1–5 场一栏、6–12 场两栏、13 场以上三栏；每组保持顺序，按实际文字高度均衡分栏，固定栏宽，不为比例缩小字号或重排分组。整份列表优先生成一张图片，取消固定每页 12 场的限制，仅在公共边长／像素预算不足时按完整比赛条目分页。一次最多展示 60 场，超出时提示按平台筛选或查详情。单张图不再附加“1/1”页码。图片直接移植原项目的绘制类，包括字号、间距、图标、按状态分组编号、时间、比赛进度和渐变背景，并使用统一信息区。

## 实现边界

```text
plugins/contests.py            原生 Alconna 模板、独立 handler、有序发送
framework/services/contests.py 聚合、缓存、手动数据读取与查询回复
framework/contests.py          比赛对象、时段筛选、详情匹配
framework/clients/_contests.py 平台响应解析
framework/views/contests.py    图片排版与分页
```

线上请求复用现有三个平台 Client 和 HttpClient，包括 Codeforces API 限速；解析和模糊匹配在工作线程执行。手动数据通过 `CurationService.run` 读取，与 Dashboard 共用锁、存储和事务入口。图片使用现有 RenderClient 工作队列与资源存储，不添加数据库表、调度器或第二套存储。

本模块只提供查询；比赛推送、订阅和聊天内维护手动比赛不在本次范围。

## 验证

```sh
uv run --extra render pytest backend/tests/test_contests.py
uv run ruff check backend supervisor
```

测试包含公开响应夹具、时区和午夜边界、去重与歧义查询、请求合并和失败重试、Dashboard 提交至应用及删除后的可见性、渲染与文字降级，以及真实 NoneBot Matcher 的私聊/群聊指令分派。网络均使用替身，数据目录与运行环境隔离。

数据源：[Codeforces API](https://codeforces.com/apiHelp/methods#contest.list)、[AtCoder 比赛列表](https://atcoder.jp/contests/?lang=en)、[牛客比赛列表](https://ac.nowcoder.com/acm/contest/vip-index)。
