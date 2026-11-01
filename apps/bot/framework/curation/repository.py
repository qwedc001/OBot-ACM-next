"""EP submission state machine over the application's existing ORM transaction.

Methods are synchronous because the original file-based domain algorithms are synchronous.
They run in AnyIO workers; SQL returns to the async ORM loop through from_thread.run.
This object never opens a database, creates tables, manages users or commits independently.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from anyio import from_thread

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_APPLIED = "applied"
# 审核通过后、应用时发现磁盘上的原值已被改动：挂起来等管理员裁定
STATUS_CONFLICT = "conflict"

# 还占着「同一目标只有一条在途提交」名额的状态
OPEN_STATUSES = (STATUS_PENDING, STATUS_APPROVED)
# 已经走完流程、不再占用名额的状态
CLOSED_STATUSES = (STATUS_REJECTED, STATUS_APPLIED)
ALL_STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_CONFLICT, STATUS_REJECTED, STATUS_APPLIED)

# 可选参数的「没传」哨兵：base_value 允许显式传 None（新增类提交本来就没有原值），
# 所以不能用 None 表示「这次不改它」。
_UNSET: Any = object()

# ---- 提交类型注册表（由插件在启动时填充）----
# 框架只存 `type` 字符串，具体有哪些类型、中文名是什么，是插件自己的事。
# 领域模块导入时注册，不另建插件生命周期。
_TYPE_LABELS: dict[str, dict[str, str]] = {}


def register_submission_types(plugin: str, labels: dict[str, str]) -> None:
    """声明某个插件的提交类型及其中文名。重复注册同名插件会直接报错。"""
    if plugin in _TYPE_LABELS:
        raise ValueError(f"提交类型重复注册: {plugin}")
    _TYPE_LABELS[plugin] = dict(labels)


def known_submission_types(plugin: str) -> tuple[str, ...]:
    return tuple(_TYPE_LABELS.get(plugin, ()))


def type_label_for(plugin: str, type_: str) -> str:
    return _TYPE_LABELS.get(plugin, {}).get(type_, type_)


@dataclass
class Submission:
    id: int
    plugin: str
    type: str
    img_key: str
    target: str
    base_value: Any
    submitted_value: Any
    note: str
    status: str
    author_id: str
    author_name: str
    reviewer_id: str | None
    reviewer_name: str | None
    review_comment: str
    created_at: float
    reviewed_at: float | None
    applied_at: float | None
    applied_value: Any
    conflict_detail: dict[str, Any] | None = None
    conflict_at: float | None = None

    @property
    def type_label(self) -> str:
        return type_label_for(self.plugin, self.type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "plugin": self.plugin,
            "type": self.type,
            "type_label": self.type_label,
            "img_key": self.img_key,
            "target": self.target,
            "base_value": self.base_value,
            "submitted_value": self.submitted_value,
            "note": self.note,
            "status": self.status,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "reviewer_id": self.reviewer_id,
            "reviewer_name": self.reviewer_name,
            "review_comment": self.review_comment,
            "created_at": self.created_at,
            "reviewed_at": self.reviewed_at,
            "applied_at": self.applied_at,
            "applied_value": self.applied_value,
            "conflict_detail": self.conflict_detail,
            "conflict_at": self.conflict_at,
        }


class ConflictError(Exception):
    """并发或状态冲突。"""


class _Connection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, parameters=()):
        result = from_thread.run(self.connection.exec_driver_sql, sql, tuple(parameters))
        return result.mappings() if result.returns_rows else result


class Repository:
    def __init__(self, connection):
        self._conn = _Connection(connection)
        self._lock = threading.RLock()

    def _execute(self, sql, params=()):
        return self._conn.execute(sql, params)

    def _query(self, sql, params=()):
        return list(self._conn.execute(sql, params).fetchall())

    def _query_one(self, sql, params=()):
        return self._conn.execute(sql, params).fetchone()

    # ---------- 提交单 ----------

    @staticmethod
    def _row_to_submission(row: dict) -> Submission:
        return Submission(
            id=int(row["id"]),
            plugin=str(row["plugin"] or ""),
            type=str(row["type"]),
            img_key=str(row["img_key"]),
            target=str(row["target"] or ""),
            base_value=_loads(row["base_value"]),
            submitted_value=_loads(row["submitted_value"]),
            note=str(row["note"] or ""),
            status=str(row["status"]),
            author_id=str(row["author_id"]),
            author_name=str(row["author_name"] or row["author_username"] or ""),
            reviewer_id=str(row["reviewer_id"]) if row["reviewer_id"] else None,
            reviewer_name=str(row["reviewer_name"] or "") if row["reviewer_id"] else None,
            review_comment=str(row["review_comment"] or ""),
            created_at=float(row["created_at"]),
            reviewed_at=float(row["reviewed_at"]) if row["reviewed_at"] else None,
            applied_at=float(row["applied_at"]) if row["applied_at"] else None,
            applied_value=_loads(row["applied_value"]),
            conflict_detail=_loads(row["conflict_detail"]),
            conflict_at=float(row["conflict_at"]) if row["conflict_at"] else None,
        )

    _SUBMISSION_SELECT = """
        SELECT s.*,
               author.id                                AS author_username,
               author.id AS author_name,
               reviewer.id AS reviewer_name
        FROM bot_content_submissions s
        JOIN bot_users author   ON author.id = s.author_id
        LEFT JOIN bot_users reviewer ON reviewer.id = s.reviewer_id
    """

    def upsert_submission(
        self,
        *,
        plugin: str,
        type: str,
        img_key: str,
        target: str,
        submitted_value: Any,
        note: str,
        author_id: str,
        base_value: Any = None,
    ) -> tuple[Submission, bool]:
        """新建或覆盖同一目标的待处理提交单，返回 (提交单, 是否新建)。

        同一 (plugin, type, img_key, target, author_id) 只会有一条 pending/approved
        记录，因此用户反复修改同一字段不会把审核队列刷爆；每次操作仍会写审计日志。
        """
        if type not in _TYPE_LABELS.get(plugin, ()):
            raise ValueError(f"未知提交类型: {plugin}/{type}")

        payload = _dumps(submitted_value)
        now = time.time()

        with self._lock:
            # conflict 也算「在途」：用户重新提交同一目标时应该覆盖掉冲突单
            existing = self._conn.execute(
                "SELECT * FROM bot_content_submissions WHERE plugin = ? AND type = ? AND img_key = ?"
                " AND target = ? AND author_id = ? AND status IN (?, ?, ?)",
                (plugin, type, img_key, target, author_id, *OPEN_STATUSES, STATUS_CONFLICT),
            ).fetchone()

            if existing is not None:
                self._conn.execute(
                    "UPDATE bot_content_submissions SET submitted_value = ?, note = ?, author_id = ?,"
                    " status = ?, created_at = ?, base_value = COALESCE(base_value, ?),"
                    " conflict_detail = NULL, conflict_at = NULL, reviewer_id = NULL,"
                    " reviewed_at = NULL, review_comment = ''"
                    " WHERE id = ?",
                    (payload, note, author_id, STATUS_PENDING, now, _dumps(base_value), int(existing["id"])),
                )
                row = self._fetch_submission(int(existing["id"]))
                assert row is not None
                return row, False

            cursor = self._conn.execute(
                "INSERT INTO bot_content_submissions (plugin, type, img_key, target, base_value,"
                " submitted_value, note, status, author_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    plugin,
                    type,
                    img_key,
                    target,
                    _dumps(base_value),
                    payload,
                    note,
                    STATUS_PENDING,
                    author_id,
                    now,
                ),
            )

        row = self._fetch_submission(int(cursor.lastrowid))
        assert row is not None
        return row, True

    def _fetch_submission(self, submission_id: int) -> Submission | None:
        row = self._query_one(self._SUBMISSION_SELECT + " WHERE s.id = ?", (submission_id,))
        return self._row_to_submission(row) if row is not None else None

    def get_submission(self, submission_id: int) -> Submission | None:
        return self._fetch_submission(submission_id)

    def list_submissions(
        self,
        *,
        plugin: str | None = None,
        status: str | None = None,
        statuses: Iterable[str] | None = None,
        author_id: str | None = None,
        img_key: str | None = None,
        type: str | None = None,
        types: Iterable[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Submission], int]:
        where: list[str] = []
        params: list[Any] = []
        if plugin:
            where.append("s.plugin = ?")
            params.append(plugin)
        if status:
            where.append("s.status = ?")
            params.append(status)
        elif statuses:
            values = list(statuses)
            if values:
                where.append(f"s.status IN ({', '.join('?' for _ in values)})")
                params.extend(values)
        if author_id is not None:
            where.append("s.author_id = ?")
            params.append(author_id)
        if img_key:
            where.append("s.img_key = ?")
            params.append(img_key)
        if type:
            where.append("s.type = ?")
            params.append(type)
        elif types:
            values = list(types)
            if values:
                where.append(f"s.type IN ({', '.join('?' for _ in values)})")
                params.extend(values)

        clause = f" WHERE {' AND '.join(where)}" if where else ""
        total_row = self._query_one("SELECT COUNT(*) AS n FROM bot_content_submissions s" + clause, params)
        total = int(total_row["n"]) if total_row else 0

        page_size = 100000 if limit is None else max(1, min(limit, 200))
        rows = self._query(
            self._SUBMISSION_SELECT + clause + " ORDER BY s.created_at DESC LIMIT ? OFFSET ?",
            [*params, page_size, _safe_offset(offset)],
        )
        return [self._row_to_submission(row) for row in rows], total

    def count_by_status(self, plugin: str | None = None) -> dict[str, int]:
        if plugin:
            rows = self._query(
                "SELECT status, COUNT(*) AS n FROM bot_content_submissions WHERE plugin = ? GROUP BY status",
                (plugin,),
            )
        else:
            rows = self._query("SELECT status, COUNT(*) AS n FROM bot_content_submissions GROUP BY status")
        counts = dict.fromkeys(ALL_STATUSES, 0)
        for row in rows:
            counts[str(row["status"])] = int(row["n"])
        return counts

    def count_by_status_for_author(self, author_id: str, *, plugin: str) -> dict[str, int]:
        """某个用户在**某个工具里**的提交按状态计数。

        「我的提交」页面的统计卡必须用这个，否则管理员看到的是全站数字，
        跟普通用户看到的没区别。

        `plugin` 是必填的：提交单表是**所有工具共用**的，少了这个过滤条件，
        每个工具的「我的提交」都会把自己和别的工具的提交加在一起，
        页面上就会出现「已生效 12」而本工具其实只有 3 条这种读不懂的数字。
        """
        rows = self._query(
            "SELECT status, COUNT(*) AS n FROM bot_content_submissions"
            " WHERE author_id = ? AND plugin = ? GROUP BY status",
            (author_id, plugin),
        )
        counts = dict.fromkeys(ALL_STATUSES, 0)
        for row in rows:
            counts[str(row["status"])] = int(row["n"])
        return counts

    def list_open_submissions_for_author(self, author_id: str) -> list[Submission]:
        rows = self._query(
            self._SUBMISSION_SELECT
            + " WHERE s.author_id = ? AND s.status IN (?, ?) ORDER BY s.created_at DESC",
            (author_id, *OPEN_STATUSES),
        )
        return [self._row_to_submission(row) for row in rows]

    def list_approved_submissions(self, plugin: str) -> list[Submission]:
        """某个插件待应用的提交单，按 id 升序保证可预期地覆盖。"""
        rows = self._query(
            self._SUBMISSION_SELECT + " WHERE s.status = ? AND s.plugin = ? ORDER BY s.id ASC",
            (STATUS_APPROVED, plugin),
        )
        return [self._row_to_submission(row) for row in rows]

    def open_submissions_for_target(
        self,
        plugin: str,
        type: str,
        img_key: str,
        target: str,
        author_id: str | None = None,
    ) -> list[Submission]:
        """某个目标上还没落盘的提交。给了 author_id 就只看这个人的。"""
        sql = (
            self._SUBMISSION_SELECT + " WHERE s.plugin = ? AND s.type = ? AND s.img_key = ? AND s.target = ?"
            " AND s.status IN (?, ?)"
        )
        params: list[Any] = [plugin, type, img_key, target, *OPEN_STATUSES]
        if author_id is not None:
            sql += " AND s.author_id = ?"
            params.append(author_id)
        return [self._row_to_submission(row) for row in self._query(sql + " ORDER BY s.id ASC", params)]

    def review_submission(
        self, submission_id: int, *, approve: bool, reviewer_id: str, comment: str = ""
    ) -> Submission:
        row = self._query_one("SELECT * FROM bot_content_submissions WHERE id = ?", (submission_id,))
        if row is None:
            raise KeyError(submission_id)

        current_status = str(row["status"])
        if current_status == STATUS_CONFLICT:
            raise ConflictError("该提交存在冲突，请在「冲突处理」中裁定")
        if current_status != STATUS_PENDING:
            raise ConflictError(f"提交单当前状态为 {current_status}，无法再次审核")

        self._execute(
            "UPDATE bot_content_submissions SET status = ?, reviewer_id = ?, review_comment = ?,"
            " reviewed_at = ? WHERE id = ?",
            (
                STATUS_APPROVED if approve else STATUS_REJECTED,
                reviewer_id,
                comment.strip(),
                time.time(),
                submission_id,
            ),
        )
        result = self._fetch_submission(submission_id)
        assert result is not None
        return result

    def unreview_submission(self, submission_id: int) -> Submission:
        """撤回审核：把「已通过待下发」的提交退回「待审核」。

        只有还没写盘的 approved 能退 —— applied 已经落到数据文件里了，
        conflict 要走冲突裁定，两者都不该从这里改状态。
        """
        row = self._query_one("SELECT status FROM bot_content_submissions WHERE id = ?", (submission_id,))
        if row is None:
            raise KeyError(submission_id)

        current_status = str(row["status"])
        if current_status != STATUS_APPROVED:
            raise ConflictError("只有「已通过待下发」的提交可以撤回审核")

        self._execute(
            "UPDATE bot_content_submissions SET status = ?, reviewer_id = NULL, review_comment = '',"
            " reviewed_at = NULL WHERE id = ?",
            (STATUS_PENDING, submission_id),
        )
        result = self._fetch_submission(submission_id)
        assert result is not None
        return result

    def resolve_submission_conflict(
        self,
        submission_id: int,
        *,
        keep_new: bool,
        reviewer_id: str,
        base_value: Any = _UNSET,
    ) -> Submission:
        row = self._query_one("SELECT status FROM bot_content_submissions WHERE id = ?", (submission_id,))
        if row is None:
            raise KeyError(submission_id)
        if str(row["status"]) != STATUS_CONFLICT:
            raise ConflictError("该提交当前不在冲突状态")

        self.resolve_conflict(
            submission_id, keep_new=keep_new, reviewer_id=reviewer_id, base_value=base_value
        )
        result = self._fetch_submission(submission_id)
        assert result is not None
        return result

    def supersede_submission(
        self, submission_id: int, *, reviewer_id: str, comment: str
    ) -> Submission | None:
        """把一条「已通过待下发」的提交改成「已驳回」（冲突裁定里的交换）。

        只动 approved：它还没写盘，被另一条提交取代时就该驳回，否则它会一直占着
        「待下发」，下一次下发又被判成冲突。已经 applied 的不动 —— 那条改动确实写过盘，
        改写它的状态等于篡改审计记录。
        返回改过的提交；没有命中可改的行时返回 None。
        """
        cursor = self._execute(
            "UPDATE bot_content_submissions SET status = ?, reviewer_id = ?, review_comment = ?,"
            " reviewed_at = ? WHERE id = ? AND status = ?",
            (STATUS_REJECTED, reviewer_id, comment, time.time(), submission_id, STATUS_APPROVED),
        )
        if cursor.rowcount <= 0:
            return None
        return self._fetch_submission(submission_id)

    def restate_submission(self, submission_id: int, submitted_value: Any) -> Submission:
        """改写一条提交「要写什么」，状态和时间都不动。

        冲突裁定里管理员确认过的新值可能要先清洗（例如别名被别的类别占用，得先摘掉
        那几个），清洗结果必须留在提交单上：裁定之后真正写盘的是下一次批量下发，
        它只看 submitted_value，无从知道裁定里做过什么。
        """
        self._execute(
            "UPDATE bot_content_submissions SET submitted_value = ? WHERE id = ?",
            (_dumps(submitted_value), submission_id),
        )
        result = self._fetch_submission(submission_id)
        if result is None:
            raise KeyError(submission_id)
        return result

    def mark_applied(self, submission_id: int, applied_value: Any) -> None:
        self._execute(
            "UPDATE bot_content_submissions SET status = ?, applied_at = ?, applied_value = ?,"
            " conflict_detail = NULL, conflict_at = NULL WHERE id = ?",
            (STATUS_APPLIED, time.time(), _dumps(applied_value), submission_id),
        )

    def mark_conflict(self, submission_id: int, detail: dict[str, Any]) -> None:
        """下发前发现这条改动已经不能直接写盘：挂到 conflict 状态等待裁定。

        两种触发点：审核台体检（过审那一刻就分出同一处的胜负），以及真正写盘时。
        只对 approved 生效，所以反复调用是幂等的。
        """
        self._execute(
            "UPDATE bot_content_submissions SET status = ?, conflict_detail = ?, conflict_at = ?"
            " WHERE id = ? AND status = ?",
            (STATUS_CONFLICT, _dumps(detail), time.time(), submission_id, STATUS_APPROVED),
        )

    def refresh_conflict_detail(self, submission_id: int, detail: dict[str, Any]) -> None:
        """改写一条**已经在 conflict 里**的提交的三方对比。

        对手会换人：裁定保留 #2 时 #1 被驳回、#2 排回待下发，同一处上还挂着的 #3
        该对照的就是 #2，而不是已经作废的 #1。体检时用它把展示信息拉回现状。
        状态与时间都不动 —— 回不回「待下发」是管理员裁定的事。
        """
        self._execute(
            "UPDATE bot_content_submissions SET conflict_detail = ? WHERE id = ? AND status = ?",
            (_dumps(detail), submission_id, STATUS_CONFLICT),
        )

    def resolve_conflict(
        self,
        submission_id: int,
        *,
        keep_new: bool,
        reviewer_id: str,
        base_value: Any = _UNSET,
    ) -> None:
        """裁定冲突。

        keep_new=True  -> 改回 approved，下一次应用会覆盖磁盘上的当前值
        keep_new=False -> 丢弃这条提交（rejected），磁盘保持现状

        `base_value` 是裁定后这条提交要改记的「提交时看到的原值」。管理员既然在
        知情的前提下决定用提交值覆盖磁盘现值，比对基准就该跟着挪到现值上；不挪的话
        下一次应用会拿旧基准再判一次冲突，裁定永远走不出去。不传则保持原样。
        """
        now = time.time()
        if not keep_new:
            self._execute(
                "UPDATE bot_content_submissions SET status = ?, reviewer_id = ?, reviewed_at = ?,"
                " conflict_detail = NULL, conflict_at = NULL, review_comment = ?"
                " WHERE id = ? AND status = ?",
                (
                    STATUS_REJECTED,
                    reviewer_id,
                    now,
                    "冲突裁定：丢弃提交，保留磁盘当前值",
                    submission_id,
                    STATUS_CONFLICT,
                ),
            )
            return

        assignments = [
            "status = ?",
            "reviewer_id = ?",
            "reviewed_at = ?",
            "conflict_detail = NULL",
            "conflict_at = NULL",
            "review_comment = ?",
        ]
        params: list[Any] = [
            STATUS_APPROVED,
            reviewer_id,
            now,
            "冲突裁定：保留提交的新值，已排回「待下发」，下发时覆盖磁盘当前值",
        ]
        if base_value is not _UNSET:
            assignments.append("base_value = ?")
            params.append(_dumps(base_value))
        params.extend([submission_id, STATUS_CONFLICT])
        self._execute(
            f"UPDATE bot_content_submissions SET {', '.join(assignments)} WHERE id = ? AND status = ?",
            params,
        )

    def withdraw_submission(self, submission_id: int) -> None:
        row = self._query_one("SELECT status FROM bot_content_submissions WHERE id = ?", (submission_id,))
        if row is None:
            raise KeyError(submission_id)
        if str(row["status"]) != STATUS_PENDING:
            raise ConflictError("只有待审核的提交可以撤回")
        self._execute("DELETE FROM bot_content_submissions WHERE id = ?", (submission_id,))

    def has_open_submission(self, plugin: str, type: str, img_key: str, target: str) -> bool:
        row = self._query_one(
            "SELECT 1 AS x FROM bot_content_submissions WHERE plugin = ? AND type = ? AND img_key = ?"
            " AND target = ? AND status IN (?, ?) LIMIT 1",
            (plugin, type, img_key, target, *OPEN_STATUSES),
        )
        return row is not None

    # ---------- 审计日志 ----------

    def add_log(
        self,
        *,
        actor_id: str | None,
        username: str,
        action: str,
        detail: str = "",
        is_admin: bool = False,
    ) -> None:
        self._execute(
            "INSERT INTO bot_content_logs (actor_id, username, action, detail, created_at, is_admin)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (actor_id, username, action, detail, time.time(), 1 if is_admin else 0),
        )

    def list_logs(self, *, limit: int = 100, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        total_row = self._query_one("SELECT COUNT(*) AS n FROM bot_content_logs")
        total = int(total_row["n"]) if total_row else 0
        rows = self._query(
            "SELECT * FROM bot_content_logs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (max(1, min(limit, 500)), _safe_offset(offset)),
        )
        return [
            {
                "id": int(row["id"]),
                "actor_id": row["actor_id"],
                "username": str(row["username"] or ""),
                "action": str(row["action"]),
                "detail": str(row["detail"] or ""),
                "created_at": float(row["created_at"]),
                "is_admin": bool(row["is_admin"]),
            }
            for row in rows
        ], total


def _safe_offset(offset: Any) -> int:
    """把 offset 收进 SQLite INTEGER 的范围。

    `(page - 1) * page_size` 由请求参数算出，一个巨大的 page 会让 Python 整数
    超出 64 位，SQLite 直接抛 OverflowError（本该是 400 的请求变成 500）。
    """
    try:
        value = int(offset)
    except (TypeError, ValueError):
        return 0
    if value < 0:
        return 0
    return min(value, 2**63 - 1)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw
