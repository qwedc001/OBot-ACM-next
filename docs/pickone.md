# PickOne 表情包

机器人和 Dashboard 共用图库、描述、点赞、评论及图片审核队列。

## 聊天指令

| 指令 | 用途 |
| --- | --- |
| `/来只` | 查看当前会话可用的类别 |
| `/来只 类别 [文字]` | 随机取图，可搜索描述或评论 |
| `/随机来只`、`/随便来只` | 随机选择一个有图片的类别 |
| `/capoo`、`/咖波` | 从咖波类别取图 |
| `/预览来只 [类别] [ID前缀]` | 随机预览最多八张；指定 ID 时发送原图 |
| `/添加来只 类别` | 同一条消息附带图片上传，每次最多四张、每张 10 MiB |
| `/点赞`、`/评论 内容` | 操作本会话一天内最后成功发送的图片 |
| `/点赞来只 类别 ID前缀`、`/评论来只 类别 ID前缀 内容` | 指定图片互动 |
| `/建图库 标识 名称` | 群管或超管创建本群专属类别 |
| `/审核群图库` | 群管或超管通过本群待审图片，可先在网页逐张检查 |
| `/审核来只` | 仅超管通过公共图库待审图片 |

完整目录为 `/help fun.pickone`。以上短名是 `fun.pickone.*` 的别名，每个功能独立匹配、独立 handler。中文取图、预览、添加、评论及指定图片互动支持省略指令后的空格，例如 `/添加o`、`/添加来只o`、`/来只o`；英文别名、标准指令以及参数之间仍使用空格。类别名含空格时使用引号。

上传先确认目标图库，再检查图片；缺图时提示图库名称和同条附图的用法，成功后按目标图库报告入库、待审、重复及失败数量。图片与指令仍需在同一条消息发送，不会把后续消息自动关联为上传。

分类图和预览图使用现有 RenderClient、统一信息区与配色 #187 Decent。表情包原图原样发送，不添加信息区，GIF 保留动画。

## 公共图库和群隔离

群内只能取公共图库与本群图库的图片；私聊只能取公共图片。相同名称优先匹配本群类别；`group:cats` 明确选择本群，`public:cats` 明确选择公共类别。显式前缀不做跨图库模糊回退。预览 ID、点赞和评论也遵守该范围。

默认示例使用简短类别标识，列表和回复标注「本群图库」或「公共图库」。只有短名称会选中另一图库时，列表和预览中的指令才保留显式前缀；例如本群也有 cats 时，用 `/来只 public:cats` 取公共图片。

群管可以创建本群类别、直接上传、审核群员上传、修改描述与评论、调整类别别名、删除图片。普通群员上传进入本群待审目录。群管向公共图库上传仍需超管审核，不能修改其他群的图库。超管通过私聊登录 Dashboard 后可选择所有已登记的群图库；在群内取图仍遵守当前场景范围。

Dashboard 的「PickOne → 群图库」复用 QQ 群管理身份验证。群内申请的登录只管理发起群；私聊登录可选择已验证的管理群，超管可选择全部。每个 API 和图片请求都重新验证权限，权限过期需重新验证。公共 EP 的提交、审核和应用流程保持独立。

## 可选自动 OCR

默认关闭；不开启时不导入 EasyOCR，也不下载模型。需要时安装可选依赖：

```sh
uv sync --extra render --extra ocr
```

在运行环境配置 `OBOT_OCR_ENABLED=true`；默认 `OBOT_OCR_GPU=false` 使用 CPU。EasyOCR 会在首次识别时下载中文、英文模型，具体参数见 [官方 Reader 实现](https://github.com/JaidedAI/EasyOCR/blob/master/easyocr/easyocr.py)。首次下载较慢，识别失败不阻止上传或其他指令，后续轮次会重试。

OCR 复用现有工作队列和 APScheduler，启动、上传及审核后检查，每五分钟补扫。只处理已入库且无描述的图片，每个图库每轮最多八张；成功后写入共享 `ocr_text`。空识别结果记录为已处理，避免无限重扫；不覆盖处理中发生的人工编辑。模型加载、解码和识别在线程中进行，同一 Reader 串行复用。受现有 `OBOT_JOB_TIMEOUT` 限制，长时间下载或 CPU 推理可能超时。

使用守护进程更新时，需要先单独升级守护工具到 **0.12.0**，在其配置中设置 `extras = ["render", "ocr"]`，这样 `/update` 和 CLI 更新会保留 OCR 依赖。默认仍只安装 render。生产启用前需验证模型下载、机器内存和实际识别速度；单元测试使用替身模型验证并发和写回逻辑，不代表真实模型识别率。

## 代码与数据边界

```text
plugins/pickone.py                  原生 Alconna 指令与顺序回复
framework/services/pickone.py      场景、权限、会话引用与 OCR 编排
framework/curation/pickone/chat.py  同步领域操作，复用 PickOneStore
framework/curation/pickone/library.py  当前场景可见的公共／群图库视图
framework/curation/pickone/group.py    群管理写入与校验
framework/clients/ocr.py           可选异步 OCR Client
framework/views/pickone.py         分类与八图预览
web/content/pickone/groups.py      群管理 HTTP 边界
```

所有文件操作经 `CurationService.run` 在工作线程内执行，与 Dashboard 共用事务和写锁。保留已有 LocalStore 目录与数据格式；不引入新存储引擎。公共目录为 `curation/Pick-One`，群目录为 `curation/Pick-One-Groups/<scope的SHA256>`；各自有配置、图片、parser 和 `__AUDIT__`。机器人不接受任意群 ID 作为图库路径。

迁移 `0007` 新增 ORM 表 `bot_pickone_contexts`（按场景保存上一张图和预览）与 `bot_pickone_libraries`（群图库登记）。OCR 模型位于 LocalStore 应用数据目录下的 `ocr/`，随数据一起备份。

验证：`uv run --extra render pytest backend/tests/test_pickone.py backend/tests/test_pickone_groups.py`；网页流程见 `frontend/tests/e2e/pickone-groups.spec.js`。所有测试使用隔离数据、合成图片和空环境文件，不发送真实 QQ 消息。
