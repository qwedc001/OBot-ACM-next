<h1 align="center">OBot's ACM</h1>

<p align="center">
  <strong>O宝的AC梦 · 算法竞赛、训练记录与更多日常乐趣</strong>
</p>

OBot ACM 是面向算法竞赛学习、训练与社区交流的聊天机器人，旨在把做题记录、训练榜单、比赛资讯和实用工具带进日常聊天，让选手和队伍更方便地了解训练进展、安排比赛，也让竞赛群聊多一些乐趣。

## 迁移项目说明

本项目为 [OBot-ACM](https://github.com/Floating-Ocean/OBot-ACM) 的 Nonebot 版迁移项目，由于重写工作量比较大并且进行了一些二次 framework revise 所以不保证与原项目 behavior 完全相同，但是大概应该是差不多的。

目前已接入训练榜单、比赛查询、一言、竞赛账号资料卡与 PickOne 表情包，其他功能持续迁移中。

目标是做能够承受高并发，提供优质服务的 bot。

没有接入 AI 对话的打算。

本项目由 [Floating-Ocean](https://github.com/Floating-Ocean) / [qwedc001](https://github.com/qwedc001) / qwedc001 的 GPT 订阅倾情巨献。原有项目和重构项目的架构设计均由对应项目主负责人自主完成，当前项目 AI 发挥的贡献主要是代码实现和文档层面的。

本项目有个开发中但是未开放公开搜索的 instance。发布 1.0.0 版本时会开放公开搜索，欢迎届时体验。

你知道吗，OBot 的 O 是 Floating Ocean 的 O。

## 算法竞赛

项目围绕选手的日常训练展开：关注各平台的做题与提交记录，通过直观的榜单展示训练情况，汇总近期比赛与竞赛信息，为查询选手资料、选择练习题目和交流比赛表现提供便利。

希望每一次提交、每一段进步都能被看见，让个人训练与团队备赛更有方向。

使用 `/cf user handle`、`/atc user handle`、`/nk user uid` 查询三个平台的公开资料卡；`/help cp` 查看竞赛功能。字段与开发说明见 [账号资料卡](docs/profiles.md)。

使用 `/contest` 查看近日比赛图片，`/contest today` 查看今日比赛，`/contest detail 关键词` 查询详情。列表汇总 Codeforces、AtCoder、牛客与工作台已应用的手动比赛，详细用法见 [比赛查询](docs/contests.md)。

## 更多日常乐趣

除了算法竞赛，OBot ACM 也希望成为群聊中的日常助手。图片与表情包、菜谱、随机选择、小工具和互动小游戏，都是让交流更轻松、更有趣的一部分。

发送 `/来只` 查看表情包类别，`/预览来只 类别` 查看图片，使用 `/点赞` 和 `/评论 内容` 参与互动。群管可以 `/建图库 标识 名称` 创建群专属图库，内容仅在本群使用；详细用法见 [PickOne](docs/pickone.md)。

## 网页工作台

OBot 的网页工作台提供个人与群训练榜单、表情包与比赛信息维护、指令目录和运行状态。私聊机器人发送 `/dashboard`，使用一次性链接登录；可以提交图片描述和比赛信息，由管理员审核后应用。工作台与机器人共用身份和权限。

开发说明见 [框架](docs/framework.md)、[网页架构](docs/web.md) 与 [视觉设计](docs/design.md)。

## 关于

项目源于训练榜单可视化项目 [Peeper-Board-Generator](https://github.com/qwedc001/Peeper-Board-Generator) 与 [Hydro_Peeper_Module](https://github.com/Floating-Ocean/Hydro_Peeper_Module)，从便捷查看训练榜单的想法出发，服务于算法竞赛爱好者的日常需要。

感谢所有贡献者与 OBot 的使用者，欢迎分享想法、反馈问题和参与开发。

部署与更新见 [更新指南](docs/updating.md)。超管可发送 `/update` 拉取最新代码并重启，启动失败自动恢复最近可用版本。

## 许可

本项目采用 [AGPL-3.0](LICENSE) 开源协议。
