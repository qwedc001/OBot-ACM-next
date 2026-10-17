# OBot 适配器补丁

此目录独立于 framework、plugins，存放针对上游适配器的局部补丁。入口为 `backend.bot` 在 `nonebot.init()` 后调用的 `install()`；不修改虚拟环境或上游源码。每个文件必须说明目标函数、适用版本、实际问题、行为边界和移除条件，补丁保持幂等，并使用真实脱敏样本与合成边界测试验证。

## QQ 推送诊断：qq_trace.py

诊断挂在 `nonebot.adapters.qq.Adapter.payload_to_event` 与 `nonebot.adapters.qq.Bot.handle_event` 两个边界；前者保存 Pydantic 解析前的数据，后者保存 SDK 完成引用/提及预处理后的结果。它不修正消息、不建立身份、不授予权限。

默认不启用。以服务用户在实际 data 目录下创建 `qq-diagnostics/request.json`（文件 0600、目录 0700），例如：

```json
{"expires_at": 0, "group_openid": "YOUR_GROUP_OPENID", "commands": ["/whoisthis", "/ping"]}
```

将 expires_at 设置为当前 Unix 秒数加 1800（最多一小时），随后重启应用。只有指定群中包含指定指令的事件会被记录。过期立即停止采集；收集完成后移除 request.json 并重启解除诊断挂钩。保留最多 40 个 JSON 文件（接收、处理两个阶段），单文件上限 256 KiB。磁盘写入在线程中完成，写入失败不影响机器人。Windows 测试环境不检查 Unix 文件权限，生产环境必须匹配服务用户且不可组/他人读取。

诊断保留真实用户身份和消息内容以便比对，属于私有数据，不能进入 Git、公开日志或前端；auth_token、密码、cookie 和 URL 等会被过滤。公开测试夹具必须重新匿名化，而不是直接复制采集文件。只有字段结构日志不足以判断身份在哪一步丢失时，才临时启用此诊断。
