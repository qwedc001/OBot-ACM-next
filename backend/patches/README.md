# OBot 适配器补丁

此目录独立于 framework、plugins，存放针对上游适配器的局部补丁。入口为 `backend.bot` 在 `nonebot.init()` 后调用的 `install()`；不修改虚拟环境或上游源码。每个文件必须说明目标函数、适用版本、实际问题、行为边界和移除条件，补丁保持幂等，并使用真实脱敏样本与合成边界测试验证。

## 适用版本与上游进展

功能补丁只对 `nonebot-adapter-qq==1.7.3` 安装。升级适配器后会警告并跳过，必须重新检查上游实现和本目录的回归测试，再决定移除或适配补丁。诊断功能独立、默认关闭。安装顺序为接收兼容、群回复、可选诊断；诊断包在最外层，接收快照仍是原始数据。

2026-10-17 核对的相关上游：

- [#220 新版 mention 格式](https://github.com/nonebot/adapter-qq/issues/220)：讨论确认普通文本回复已有平台自动提及，Markdown 需自行携带标签。
- [PR #223](https://github.com/nonebot/adapter-qq/pull/223)：尚未合并；核对 head `570d61703174e0a38f105d741a97e8679d02806e`，主要修改 mention 字符串序列化及全体成员解析。它没有补回推送中缺失的目标身份。本项目参考其讨论和新标签格式，没有整体移植其全局序列化变更，也不移除旧格式解析。
- [#225 Markdown 混排](https://github.com/nonebot/adapter-qq/issues/225)：仍未关闭，分离的文本/提及不会自动进入 Markdown 正文。补丁只处理本项目使用的原始 Markdown 与独立提及，不提供通用混排引擎。
- [PR #222](https://github.com/nonebot/adapter-qq/pull/222)：已合并，当前适配器已包含引用索引与命令解析修复，无需重复移植。

## 接收兼容：qq_mentions.py

挂钩 `Adapter.payload_to_event`，在 SDK 的 Pydantic 验证前，对群事件实际提供的 `mentions[]` 做复制后兼容。官方 [群 @ 消息 User 结构](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_at_message_create.html) 没有要求 SDK 模型强制需要的 `scope/is_you`；合成该文档结构可复现整个事件被拒绝。只有记录自带非空 `member_openid` 时才补默认值，已有字段一律保留。缺省 `is_you=False`，不能把其他机器人当作自身。

这个路径经原生 SDK 和命令鉴权测试验证；真实手机测试中平台未提供目标列表，不能把此兼容路径当成已经恢复了真实 @ 用户身份。禁止通过昵称、QQ 号、缓存或认证令牌猜测身份。上游模型接受官方 User 结构后可移除。

## 群回复兼容：qq_group_reply.py

挂钩 `Bot.send` 和 `Bot.send_to_group`，保留 SDK 的上传、消息序号、被动消息 ID、引用 ID 及路由逻辑：

- 普通文本/图片被动群回复：仅移除首段与本次已认证发送者一致、且后面还有正文的提及，以及其后一个分隔空格。QQ 自身仍会标明回复对象，避免额外出现字面量 `<@id>`。其他用户提及、仅提及消息及正文缩进均保留。
- 原始 Markdown 群消息：把独立提及转为 `<qqbot-at-user id="..." />` 放入复制后的 Markdown 正文，移除独立提及和空白分隔文本，避免同时发送非空 `content`。不修改调用方的消息模型。
- 自定义模板没有通用插槽，不猜测参数；多 Markdown 或包含有意义 Text 的混排保持上游行为，调用者应显式构造完整 Markdown。不会把普通文本隐式转换为 Markdown。

此补丁限群发送，不修改频道/私信方法，不改全局 `MentionUser.__str__`。上游支持群消息的正确提及组装后应重新测试并移除。真实设备仅确认显示效果，未验证提及是否产生额外通知。

## 手机实测：接收身份的边界

Android QQ 原生选择目标用户 @ 后，送达适配器之前的推送可简化为：

```json
{"content": " /whoisthis  ", "message_type": 0, "author": {"member_openid": "SENDER"}, "group_openid": "GROUP"}
```

原始数据没有 `mentions`，正文也没有目标标签或名称。引用人类消息时能收到 `message_type=103`、`ref_msg_idx`、`msg_elements` 和 `parallel_message`，但这次引用元素没有作者字段。这不是命令解析器吞掉了目标，也不能仅靠 SDK 补丁生成可靠身份。`/whoisthis` 在缺少身份时仍明确报告无法识别。

以上仅是本次机器人、群和账号的实测结果，不代表所有 QQ 推送均如此。临时切换 Webhook 的对照没有收到事件，结论不充分；已恢复 WebSocket，不据此判定传输方式影响身份字段。真实 ID、消息和完整私有采集文件不提交。

## QQ 推送诊断：qq_trace.py

诊断挂在 `nonebot.adapters.qq.Adapter.payload_to_event` 与 `nonebot.adapters.qq.Bot.handle_event` 两个边界；前者保存 Pydantic 解析前的数据，后者保存 SDK 完成引用/提及预处理后的结果。它不修正消息、不建立身份、不授予权限。

默认不启用。以服务用户在实际 data 目录下创建 `qq-diagnostics/request.json`（文件 0600、目录 0700），例如：

```json
{"expires_at": 0, "group_openid": "YOUR_GROUP_OPENID", "commands": ["/whoisthis", "/ping"]}
```

将 expires_at 设置为当前 Unix 秒数加 1800（最多一小时），随后重启应用。只有指定群中包含指定指令的事件会被记录。过期立即停止采集；收集完成后移除 request.json 并重启解除诊断挂钩。保留最多 40 个 JSON 文件（接收、处理两个阶段），单文件上限 256 KiB。磁盘写入在线程中完成，写入失败不影响机器人。Windows 测试环境不检查 Unix 文件权限，生产环境必须匹配服务用户且不可组/他人读取。

诊断保留真实用户身份和消息内容以便比对，属于私有数据，不能进入 Git、公开日志或前端；auth_token、密码、cookie 和 URL 等会被过滤。公开测试夹具必须重新匿名化，而不是直接复制采集文件。只有字段结构日志不足以判断身份在哪一步丢失时，才临时启用此诊断。
