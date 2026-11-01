"""算法竞赛列表的词汇表：提交类型、字段名、唯一性判据。

框架只存 `type` 字符串；这些常量以及「哪个类型叫什么名字」是这个工具自己的词汇表，
通过 `register_submission_types()` 告知框架。

三种类型对应三种动作，而不是「一个字段一个类型」：手动比赛是一条一条录入的，
审核员看到「新增 / 修改 / 删除」比看到「name 变了」更接近他脑子里想的事。
"""

from __future__ import annotations

# 往列表末尾追加一条新比赛
TYPE_CREATE = "contest_create"
# 就地修改第 N 条（提交单的 img_key 就是那个下标）
TYPE_UPDATE = "contest_update"
# 删除第 N 条
TYPE_DELETE = "contest_delete"

SUBMISSION_TYPES = (TYPE_CREATE, TYPE_UPDATE, TYPE_DELETE)

TYPE_LABELS = {
    TYPE_CREATE: "新增比赛",
    TYPE_UPDATE: "修改比赛",
    TYPE_DELETE: "删除比赛",
}

# 上游 src/data/data_contest_manual.py 的 ManualContest 字段名，JSON 里就用这些键。
FIELD_ORDER = ("platform", "abbr", "name", "start_time", "duration", "supplement")

# 字段的中文名。**前后端必须一致**：这些名字会出现在提交单的「类型」列、
# 冲突提示里，和编辑表单上的字段标签对不上就会让人以为说的是两回事
# （前端的同名副本在 web/src/plugins/contestlist/format.js）。
FIELD_LABELS = {
    "platform": "平台",
    "abbr": "简称",
    "name": "比赛全称",
    "start_time": "开始时间",
    "duration": "时长",
    "supplement": "比赛地点",
}

# 唯一性判据（与上游 ManualContest.__eq__ 一致）：开始时间 + 平台 + 名称相同
# 就认为是同一场比赛；duration / supplement 变化不会变成另一场比赛。
IDENTITY_FIELDS = ("start_time", "platform", "name")

# 平台是自由文本，**没有受控词表**。
#
# 上游 `ManualContest.platform` 只是个字符串（Bot 的 `/导入比赛` 直接收任意值），
# 实际列表里除了 ICPC / CCPC 还有邀请赛、校赛、AtCoder、Codeforces、洛谷……
# 把它做成下拉框等于替上游发明一套它并不认识的枚举：想录一个新平台就录不进去，
# 或者被硬塞进「其他」，而「其他」会和真实平台名一起进到渲染出来的比赛卡片上。
# 所以这里只限长度，由录入的人自己保证写法一致。
MAX_PLATFORM_LENGTH = 32
