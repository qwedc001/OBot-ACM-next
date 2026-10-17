# 竞赛账号资料卡

迁移 Codeforces、AtCoder、牛客三个平台的公开资料卡。无需绑定账号或提供凭据，everyone 均可在私聊和群聊查询。卡片包含头像、最近比赛与提交等原版资料；独立的比赛成绩、提交查询、账号绑定与对决指令另行迁移。

## 指令与职责

| 指令 | 别名 | 数据来源 |
| --- | --- | --- |
| `/cp.cf user handle` | `/cf`、`/codeforces` | 官方 `user.info`、`user.rating`、`user.status` |
| `/cp.atc user handle` | `/atc`、`/atcoder` | 英文公开主页、Algorithm 比赛历史 JSON |
| `/cp.nk user uid` | `/nk`、`/nc`、`/nowcoder` | ACM 公开主页、比赛／团队公开接口 |

三条指令的 `user` 子指令均保留 `identity`、`id`、`card`、`info` 别名，与旧指令兼容。参数缺失或格式不正确返回用法；牛客仅接受数字 UID。`/help cp` 与 dashboard 指令目录自动使用同一份声明。不自动发布 QQ 菜单。

- `plugins/profiles.py`：声明三个模板，每个平台一个 handler，负责收发消息，不含抓取或渲染逻辑。
- `framework/profiles.py`：不可变资料模型、公开查询错误与文字展示。字段不依赖 NoneBot、QQ 或 pixie。
- `framework/clients/{codeforces,atcoder,nowcoder}.py`：各自校验标识、访问固定官方站点、解析为统一模型；不互相调用。HTML 解析放到线程，避免阻塞事件循环。
- `framework/clients/_cache.py`：资料与头像复用的有限成功缓存、同键请求合并、超时和生命周期；不存数据库、不缓存失败。`_profiles.py` 仅保留资料查询基类与解析工具。Codeforces 对所有 API 调用共享频率限制。
- `framework/clients/avatars.py`：异步下载白名单站点的头像，并在线程中解码、裁切与标准化；与资料查询、绘图互不耦合。
- `framework/services/profiles.py`：查询、图片/文字选择、渲染故障降级。通过动态发现接入，网页未来可复用其查询接口。
- `framework/views/profile.py`：模型到原版配色的转换；`_profile.py` 在绘图线程加载 Pixie，移植原版头像、Rating 色带、统一字号指标格与详情分节；`footer.py` 提供统一信息区。

## 字段与取舍

CF 展示头像、当前／最高 Rating、段位、贡献、粉丝、姓名、地区、组织、注册／在线日期、Rated 场次、最近比赛和最近三次提交。通过题数、今日／本周过题数从完整提交历史按题目去重计算，不能用最近若干条推算累计值；日期采用 Asia/Shanghai。

AtCoder 展示头像、当前／最高 Rating、最高段位与晋级提示、位次、Rated 场次、地区／出生年份／组织、最近参赛时间、关联账号，以及最近 Rated Algorithm 比赛的位次、Performance、Inner Performance 和分数变化。关联账号仅展示公开标识，不额外跨平台抓取。

牛客展示头像、名称、UID、Rating、位次、Rated 场次、关注／粉丝、简介、教育资料、最近比赛和前五个团队；团队主页展示前五名成员、队长及成员 Rating。只取比赛首页，场次使用 `pageInfo.totalCount`，不把首页长度当总数；成员基础资料查询最多五个，不递归查询其团队或历史。

可选接口失败时仍返回主要资料并说明缺失内容；未知统计省略，成功获取的空列表才显示零或未参赛。统一模型不持有绘图配色，色值由 View 按原版段位规则选择。头像与其余数据均在绘图前取回，绘图本身不发网络请求。图片采用受限长度的展示字段，公开主页链接随文字回复提供，原始资料不作为模板或 HTML 执行。

## 并发与失败

成功资料缓存 60 秒、每平台最多 128 项；同账号并发只请求一次，不同账号可并行，CF 全部 API 共享至少 2.1 秒的请求间隔。每平台最多 32 个未完成查询、单次资料总超时 30 秒、补充接口最多 15 秒；取消某个等待者不取消其他等待者的查询，关闭 Client 时清理全部后台任务。缓存仅在当前进程中存在。

头像成功缓存一小时、最多 128 项，单次超时 8 秒；仅允许配置在代码中的平台公共图片域名、HTTPS 和标准端口，不携带凭据、不跟随重定向。下载以流式累计限制 2 MiB，解码限制 1600 万像素，在线程中生成 176×176 PNG。头像下载、解码失败不影响其余资料；Pixie 绘图继续由 RenderClient 串行工作线程执行，避免原生库并发访问。

不存在、输入错误、平台不可用分别反馈；反爬页、登录页、结构变更不能误报为不存在。图片能力缺失、绘图依赖不可用或队列繁忙时返回同一份文字资料。HTTP 错误日志不包含完整响应、凭据或用户查询参数。

## 验收计划

1. 离线 HTTP 样本覆盖三个平台、未参赛、零分、缺失字段、团队、404、不存在、反爬和无效响应。
2. 测试缓存过期/容量、同键合并、异键并行、失败重试、取消隔离、CF 间隔与关闭清理。
3. 测试去重统计、额外接口降级、分页总数、成员查询上限、原版颜色边界、头像缓存／下载限制／线程解码，以及 Service 图片／文字降级；真实 PNG 检查头像、长文本、中英文排版与统一信息区。
4. 隔离 NoneBot 分发测试旧别名、子指令别名、参数错误、everyone 权限与帮助目录；不读取开发凭据。
5. 运行现有回归检查。真实平台公开账号只用于单独的手动联网验证，不作为单元测试依赖。

参考：[CF API 与频率限制](https://codeforces.com/apiHelp)、[AtCoder 公开主页](https://atcoder.jp/users/tourist?lang=en)、[牛客公开主页](https://ac.nowcoder.com/acm/contest/profile/329687984)；旧项目 `src/platform/online/` 与 `src/render/pixie/render_user_card.py`。
