"""Pick-One 数据访问层。

数据布局（与 OBot-ACM 完全一致）：

    lib/Pick-One/config.json          {img_key: {"id": str, "key": [str, ...]}}
    lib/Pick-One/<img_key>/*.gif      表情包本体，文件名即内容 MD5
    lib/Pick-One/<img_key>/parser.json
        { "<md5>.gif": {"ocr_text": str, "add_time": float,
                        "likes": int, "comments": [str], "pickup_times": int} }
        旧数据里值可能直接是字符串（只存过 ocr_text），本模块统一归一化。

写回策略：临时文件 + os.replace 原子替换，与 Bot 的保存方式一致，
因此 Web 端写入不会产生半截文件。
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 错误类型必须来自框架：核心已经给它们装好了异常处理器，
# 插件自己再定义一套只会得到 500 而不是 400/404。
from framework.curation.errors import NotFoundError, StoreError, ValidationError

from .hashing import GIF_SUFFIX, hash_id_of, is_md5, new_legacy_entry

PARSER_FILENAME = "parser.json"
CONFIG_FILENAME = "config.json"
AUDIT_DIRNAME = "__AUDIT__"

MAX_OCR_TEXT_LENGTH = 512
MAX_COMMENT_LENGTH = 32
MAX_COMMENTS_PER_IMAGE = 20
# 单次提交最多能加多少赞（对应 Bot 一条 /点赞 指令加 1 个）。
# 提交单里的点赞值是「增量」，应用时加在磁盘现值上，所以这不是「一张图最多 10 个赞」。
MAX_LIKES_PER_REQUEST = 10
MAX_ALIAS_LENGTH = 64
MAX_ALIASES = 64
MAX_CATEGORY_ID_LENGTH = 64

__all__ = [
    "NotFoundError",
    "StoreError",
    "ValidationError",
    "PARSER_FILENAME",
    "CONFIG_FILENAME",
    "AUDIT_DIRNAME",
    "MAX_OCR_TEXT_LENGTH",
    "MAX_COMMENT_LENGTH",
    "MAX_COMMENTS_PER_IMAGE",
    "MAX_LIKES_PER_REQUEST",
    "MAX_ALIAS_LENGTH",
    "MAX_ALIASES",
    "MAX_CATEGORY_ID_LENGTH",
    "Category",
    "ImageStat",
    "PickOneStore",
]


@dataclass
class Category:
    img_key: str
    id: str
    keys: list[str]
    image_count: int = 0


@dataclass
class ImageStat:
    name: str
    md5: str
    hash_id: str
    ocr_text: str
    add_time: float
    likes: int
    comments: list[str]
    pickup_times: int
    legacy: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_ocr(self) -> bool:
        return not self.ocr_text.strip()


@dataclass
class AuditImage:
    """一张来自 OBot-ACM ``__AUDIT__`` 目录的待审图片。"""

    img_key: str
    category_id: str
    name: str
    md5: str
    hash_id: str
    add_time: float
    size: int


def _atomic_write_json(path: Path, data: Any) -> None:
    """先写临时文件再原子替换，最后清掉临时文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=4)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        # 成功时 os.replace 已经把临时文件搬走了，unlink 必然失败；这里只是兜底清理
        with contextlib.suppress(OSError):
            tmp_path.unlink()


def _read_json(path: Path, default: Any) -> Any:
    """Missing files start empty; unreadable or corrupt data must never be overwritten."""
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as error:
        raise StoreError("无法读取表情包数据，请联系管理员检查文件权限。") from error

    text = text.strip()
    try:
        return json.loads(text)
    except ValueError as error:
        raise StoreError("表情包数据格式损坏，请修复后再提交修改。") from error


class _FileCache:
    """按 (mtime, size) 失效的小缓存，避免每张图都读一遍 parser.json。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, tuple[tuple[int, int], Any]] = {}

    def get(self, path: Path, loader):
        try:
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return loader()

        key = str(path)
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None and cached[0] == stamp:
                return cached[1]

        value = loader()
        with self._lock:
            self._entries[key] = (stamp, value)
        return value

    def invalidate(self, path: Path | None = None) -> None:
        with self._lock:
            if path is None:
                self._entries.clear()
            else:
                self._entries.pop(str(path), None)


class PickOneStore:
    """Pick-One 数据的读写入口。"""

    def __init__(self, lib_dir: Path) -> None:
        self.lib_dir = Path(lib_dir)
        self._cache = _FileCache()
        self._audit_lock = threading.RLock()

    # ---------- 基础路径 ----------

    @property
    def config_path(self) -> Path:
        return self.lib_dir / CONFIG_FILENAME

    def category_dir(self, img_key: str, audit: bool = False) -> Path:
        if not self.is_valid_key(img_key):
            raise ValidationError(f"非法的类别标识: {img_key!r}")
        base = self.lib_dir / AUDIT_DIRNAME if audit else self.lib_dir
        return base / img_key

    def parser_path(self, img_key: str) -> Path:
        return self.category_dir(img_key) / PARSER_FILENAME

    def image_path(self, img_key: str, name: str) -> Path:
        """解析图片绝对路径，并保证结果不会逃出类别目录。"""
        if not self.is_valid_image_name(name):
            raise ValidationError(f"非法的图片名: {name!r}")

        directory = self.category_dir(img_key).resolve()
        target = (directory / name).resolve()
        if target.parent != directory:
            raise ValidationError("图片路径越界")
        if not target.is_file():
            raise NotFoundError(f"图片不存在: {img_key}/{name}")
        return target

    def audit_image_path(self, img_key: str, name: str) -> Path:
        """解析 ``__AUDIT__`` 下的图片路径，不能访问正式数据目录。"""
        if not self.is_valid_image_name(name):
            raise ValidationError(f"非法的图片名: {name!r}")

        directory = self.category_dir(img_key, audit=True).resolve()
        target = (directory / name).resolve()
        if target.parent != directory:
            raise ValidationError("图片路径越界")
        if not target.is_file():
            raise NotFoundError(f"待审核图片不存在: {img_key}/{name}")
        return target

    @staticmethod
    def is_valid_key(img_key: str) -> bool:
        """类别标识：用于拼路径，因此限制为保守的字符集。

        以双下划线开头的名字（如 ``__AUDIT__``）是模块内部保留目录，不允许作为类别。
        """
        if not img_key or len(img_key) > 64:
            return False
        if img_key.startswith("__"):
            return False
        return all(char.isalnum() or char in "_-" for char in img_key)

    @staticmethod
    def is_valid_image_name(name: str) -> bool:
        if not name.endswith(GIF_SUFFIX):
            return False
        return is_md5(name[: -len(GIF_SUFFIX)])

    # ---------- 配置 ----------

    def load_raw_config(self) -> dict[str, Any]:
        raw = _read_json(self.config_path, {})
        if not isinstance(raw, dict):
            raise StoreError("config.json 结构异常，应为对象")
        return raw

    def load_categories(self) -> dict[str, Category]:
        """读取 config.json 并归一化，兼容 id/key 缺失等脏数据。"""
        result: dict[str, Category] = {}
        for img_key, value in self.load_raw_config().items():
            if not isinstance(value, dict):
                continue
            raw_id = value.get("id")
            raw_keys = value.get("key")
            keys = [item for item in raw_keys if isinstance(item, str)] if isinstance(raw_keys, list) else []
            result[str(img_key)] = Category(
                img_key=str(img_key),
                id=raw_id if isinstance(raw_id, str) and raw_id else str(img_key),
                keys=keys,
            )
        return result

    def list_categories(self) -> list[Category]:
        """列出所有类别，附带磁盘上的图片数量，按数量降序。"""
        categories = self.load_categories()
        for category in categories.values():
            category.image_count = len(self.list_images(category.img_key))
        return sorted(categories.values(), key=lambda item: (-item.image_count, item.img_key))

    def load_category_counts(self) -> dict[str, dict[str, int]]:
        """每个类别的 {total, missing_ocr}，每个 parser.json 只读一次。

        浏览页用「缺 OCR」筛选、类别列表要显示待补数量，都靠这个。
        """
        counts: dict[str, dict[str, int]] = {}
        for category in self.list_categories():
            total = 0
            missing = 0
            for stat in self.iter_image_stats(category.img_key):
                total += 1
                if stat.needs_ocr:
                    missing += 1
            counts[category.img_key] = {"total": total, "missing_ocr": missing}
        return counts

    def category_or_404(self, img_key: str) -> Category:
        category = self.load_categories().get(img_key)
        if category is None:
            raise NotFoundError(f"类别不存在: {img_key}")
        return category

    def save_raw_config(self, raw: dict[str, Any]) -> None:
        _atomic_write_json(self.config_path, raw)
        self._cache.invalidate(self.config_path)

    # ---------- parser.json ----------

    def load_raw_parser(self, img_key: str) -> dict[str, Any]:
        path = self.parser_path(img_key)
        raw = self._cache.get(path, lambda: _read_json(path, {}))
        if not isinstance(raw, dict):
            raise StoreError(f"{img_key}/parser.json 结构异常，应为对象")
        return raw

    def save_raw_parser(self, img_key: str, raw: dict[str, Any]) -> None:
        path = self.parser_path(img_key)
        _atomic_write_json(path, raw)
        self._cache.invalidate(path)

    def list_images(self, img_key: str) -> list[str]:
        """列出类别下的 .gif 文件名（已排序），目录不存在时返回空表。

        只认「文件名即内容 MD5」的图：Bot 之外留下的杂项 .gif（截图、说明图）
        如果混进来，`hash_id_of` 会因为文件名不是 32 位十六进制而抛错，
        整个类别列表跟着 500。
        """
        directory = self.category_dir(img_key)
        try:
            names = [
                entry.name
                for entry in os.scandir(directory)
                if entry.is_file()
                and entry.name.endswith(GIF_SUFFIX)
                and is_md5(entry.name[: -len(GIF_SUFFIX)])
            ]
        except OSError:
            return []
        names.sort()
        return names

    def list_audit_images(
        self, img_key: str | None = None, *, page: int = 1, page_size: int = 60
    ) -> tuple[list[AuditImage], int]:
        """列出上游 Bot 放入 ``__AUDIT__`` 的图片，不读写提交单。"""
        categories = self.load_categories()
        keys = [img_key] if img_key else sorted(categories)
        items: list[AuditImage] = []
        for key in keys:
            category = categories.get(key)
            if category is None:
                if img_key:
                    raise NotFoundError(f"类别不存在: {img_key}")
                continue
            try:
                directory = self.category_dir(key, audit=True)
                entries = list(os.scandir(directory))
            except OSError:
                continue
            for entry in entries:
                if not entry.is_file() or not entry.name.endswith(GIF_SUFFIX):
                    continue
                md5 = entry.name[: -len(GIF_SUFFIX)]
                if not is_md5(md5):
                    continue
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                items.append(
                    AuditImage(
                        img_key=key,
                        category_id=category.id,
                        name=entry.name,
                        md5=md5,
                        hash_id=hash_id_of(md5),
                        add_time=stat.st_mtime,
                        size=stat.st_size,
                    )
                )

        items.sort(key=lambda item: (-item.add_time, item.img_key, item.name))
        total = len(items)
        start = (page - 1) * page_size
        return items[start : start + page_size], total

    def audit_count(self) -> int:
        return self.list_audit_images(page=1, page_size=1_000_000)[1]

    def approve_audit_image(self, img_key: str, name: str) -> str:
        """把待审图片原子移动到正式类别，返回 ``approved`` 或 ``duplicate``。"""
        with self._audit_lock:
            source = self.audit_image_path(img_key, name)
            target_dir = self.category_dir(img_key)
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / name
            if target.exists():
                source.unlink()
                return "duplicate"
            os.replace(source, target)
            return "approved"

    def reject_audit_image(self, img_key: str, name: str) -> None:
        """删除一张被管理员驳回的上游待审图片。"""
        with self._audit_lock:
            self.audit_image_path(img_key, name).unlink()

    def _merge_entry(self, name: str, raw_value: Any, directory: Path) -> ImageStat:
        """把 parser.json 里的一条记录归一化成 ImageStat。"""
        md5 = name[: -len(GIF_SUFFIX)]
        if isinstance(raw_value, str):
            add_time = self._fallback_mtime(directory / name)
            return ImageStat(
                name=name,
                md5=md5,
                hash_id=hash_id_of(md5),
                ocr_text=raw_value,
                add_time=add_time,
                likes=0,
                comments=[],
                pickup_times=0,
                legacy=True,
            )

        if not isinstance(raw_value, dict):
            add_time = self._fallback_mtime(directory / name)
            return ImageStat(
                name=name,
                md5=md5,
                hash_id=hash_id_of(md5),
                ocr_text="",
                add_time=add_time,
                likes=0,
                comments=[],
                pickup_times=0,
                legacy=True,
            )

        comments_raw = raw_value.get("comments")
        comments = [str(item) for item in comments_raw] if isinstance(comments_raw, list) else []
        known = {"ocr_text", "add_time", "likes", "comments", "pickup_times"}
        return ImageStat(
            name=name,
            md5=md5,
            hash_id=hash_id_of(md5),
            ocr_text=str(raw_value.get("ocr_text") or ""),
            add_time=float(raw_value.get("add_time") or 0.0),
            likes=int(raw_value.get("likes") or 0),
            comments=comments,
            pickup_times=int(raw_value.get("pickup_times") or 0),
            legacy=False,
            extra={key: value for key, value in raw_value.items() if key not in known},
        )

    @staticmethod
    def _fallback_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    @staticmethod
    def fallback_add_time(path: Path) -> float:
        """图片不在 parser.json 里时，用文件修改时间兜底。"""
        return PickOneStore._fallback_mtime(path)

    def iter_image_stats(self, img_key: str) -> Iterator[ImageStat]:
        """遍历一个类别下所有图片的归一化信息。"""
        raw = self.load_raw_parser(img_key)
        directory = self.category_dir(img_key)
        for name in self.list_images(img_key):
            yield self._merge_entry(name, raw.get(name), directory)

    def get_image_stat(self, img_key: str, name: str) -> ImageStat:
        if not self.is_valid_image_name(name):
            raise ValidationError(f"非法的图片名: {name!r}")

        directory = self.category_dir(img_key)
        exists = (directory / name).is_file()
        raw = self.load_raw_parser(img_key)
        if name not in raw and not exists:
            raise NotFoundError(f"图片不存在: {img_key}/{name}")
        return self._merge_entry(name, raw.get(name), directory)

    def parser_entry(self, img_key: str, name: str) -> dict[str, Any]:
        """取出（必要时就地升级为）字典形式的记录。

        图片存在但 parser.json 里还没有条目时，返回按 Bot 旧版结构补好的占位条目，
        这样「给缺 parser 的图片补 OCR / 点赞」也能走同一套流程。
        """
        if not self.is_valid_image_name(name):
            raise ValidationError(f"非法的图片名: {name!r}")

        path = self.category_dir(img_key) / name
        if not path.is_file():
            raise NotFoundError(f"图片不存在: {img_key}/{name}")

        raw = self.load_raw_parser(img_key)
        value = raw.get(name)
        if isinstance(value, dict):
            entry = dict(value)
            entry.setdefault("ocr_text", "")
            entry.setdefault("likes", 0)
            entry.setdefault("comments", [])
            entry.setdefault("pickup_times", 0)
            entry.setdefault("add_time", self._fallback_mtime(path))
            return entry

        text = value if isinstance(value, str) else ""
        return new_legacy_entry(text, self._fallback_mtime(path))

    # ---------- 统计 ----------

    def summary(self) -> dict[str, Any]:
        """全局概览：类别数、图片数、待补 OCR 数量等。

        刻意不含 lib_dir：这个结构会发给任何登录用户，而绝对路径属于服务端
        内部信息（需要路径的地方只有管理台的 /admin/overview）。
        """
        categories = self.list_categories()
        total_images = 0
        missing_ocr = 0
        for category in categories:
            total_images += category.image_count
            for stat in self.iter_image_stats(category.img_key):
                if stat.needs_ocr:
                    missing_ocr += 1
        return {
            "category_count": len(categories),
            "image_count": total_images,
            "missing_ocr": missing_ocr,
            "lib_available": self.lib_dir.is_dir(),
        }

    def check_integrity(self) -> dict[str, Any]:
        """只读体检：图片/parser.json 是否双向对齐。"""
        report: list[dict[str, Any]] = []
        for category in self.list_categories():
            files = set(self.list_images(category.img_key))
            keys = {key for key in self.load_raw_parser(category.img_key) if key.endswith(GIF_SUFFIX)}
            orphan_keys = sorted(keys - files)
            unparsed = sorted(files - keys)
            report.append(
                {
                    "img_key": category.img_key,
                    "id": category.id,
                    "images": len(files),
                    "parser_keys": len(keys),
                    "parser_without_image": len(orphan_keys),
                    "image_without_parser": len(unparsed),
                    "parser_without_image_sample": orphan_keys[:10],
                    "image_without_parser_sample": unparsed[:10],
                }
            )
        return {"lib_dir": str(self.lib_dir), "categories": report}

    # ---------- 写入 ----------

    def apply_image_changes(self, changes: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
        """把已审核的修改落到 parser.json。

        `changes` 形如 {img_key: [{"name":..., "field":..., "value":...}, ...]}，
        同一图片的多个字段会在一次写入里合并，减少对 Bot 的干扰。

        likes 的 value 是增量，加在磁盘现值上；其余字段是目标值，直接覆盖。
        """
        applied = 0
        for img_key, items in changes.items():
            if not items:
                continue

            raw = self.load_raw_parser(img_key)
            dirty = False

            for item in items:
                name = item["name"]
                if not self.is_valid_image_name(name):
                    raise ValidationError(f"非法的图片名: {name!r}")

                entry = raw.get(name)
                if not isinstance(entry, dict):
                    text = entry if isinstance(entry, str) else ""
                    entry = new_legacy_entry(text, self._fallback_mtime(self.category_dir(img_key) / name))

                field_name = item["field"]
                value = item["value"]

                if field_name == "ocr_text":
                    text = str(value or "")
                    if len(text) > MAX_OCR_TEXT_LENGTH:
                        raise ValidationError(f"描述文字过长（上限 {MAX_OCR_TEXT_LENGTH}）")
                    entry["ocr_text"] = text
                elif field_name == "likes":
                    entry["likes"] = int(entry.get("likes") or 0) + int(value)
                elif field_name == "comments":
                    comments = [str(comment) for comment in (value or [])]
                    if len(comments) > MAX_COMMENTS_PER_IMAGE:
                        raise ValidationError(f"评论过多（上限 {MAX_COMMENTS_PER_IMAGE} 条）")
                    for comment in comments:
                        if len(comment) > MAX_COMMENT_LENGTH:
                            raise ValidationError(f"单条评论过长（上限 {MAX_COMMENT_LENGTH} 字）")
                    entry["comments"] = comments
                else:
                    raise ValidationError(f"不支持的字段: {field_name}")

                raw[name] = entry
                dirty = True
                applied += 1

            if dirty:
                self.save_raw_parser(img_key, raw)

        return {"applied": applied}

    def apply_category_entries(self, entries: dict[str, dict[str, Any]]) -> dict[str, int]:
        """把已审核的类别增改落到 config.json。"""
        raw = self.load_raw_config()
        for img_key, value in entries.items():
            raw[img_key] = value
        self.save_raw_config(raw)
        return {"applied": len(entries)}

    def ensure_category_dir(self, img_key: str) -> Path:
        directory = self.category_dir(img_key)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    # ---------- 校验 ----------

    @staticmethod
    def normalize_alias(alias: str) -> str:
        return " ".join(str(alias).split())

    def validate_alias_list(self, keys: list[str], *, exclude_key: str | None = None) -> list[str]:
        """规范化别名列表：去空、去重（大小写不敏感）、限制长度。"""
        cleaned: list[str] = []
        seen: set[str] = set()
        for alias in keys:
            text = self.normalize_alias(alias)
            if not text:
                continue
            if len(text) > MAX_ALIAS_LENGTH:
                raise ValidationError(f"别名过长（上限 {MAX_ALIAS_LENGTH} 字）: {text[:20]}…")
            folded = text.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            cleaned.append(text)

        if not cleaned:
            raise ValidationError("至少需要保留一个别名")

        if len(cleaned) > MAX_ALIASES:
            raise ValidationError(f"别名过多（上限 {MAX_ALIASES} 个）")

        # 别名冲突检查：同一个别名不能指向两个类别
        owner: dict[str, str] = {}
        for category in self.load_categories().values():
            if category.img_key == exclude_key:
                continue
            for alias in category.keys:
                owner[self.normalize_alias(alias).casefold()] = category.img_key

        conflicts = sorted({owner[alias.casefold()] for alias in cleaned if alias.casefold() in owner})
        if conflicts:
            raise ValidationError(f"别名与已有类别冲突: {', '.join(conflicts)}")

        return cleaned

    def validate_category_id(self, category_id: str) -> str:
        text = self.normalize_alias(category_id)
        if not text:
            raise ValidationError("显示名不能为空")
        if len(text) > MAX_CATEGORY_ID_LENGTH:
            raise ValidationError(f"显示名过长（上限 {MAX_CATEGORY_ID_LENGTH} 字）")
        return text

    def validate_new_key(self, img_key: str) -> str:
        text = str(img_key or "").strip()
        if not text:
            raise ValidationError("类别标识不能为空")
        if not self.is_valid_key(text):
            raise ValidationError("类别标识只能包含字母、数字、下划线和短横线，且不能以双下划线开头")
        if text in (AUDIT_DIRNAME, PARSER_FILENAME, CONFIG_FILENAME):
            raise ValidationError(f"类别标识不能是保留名 {text}")
        # 类别标识同时也是目录名，而 Windows / macOS 的文件系统不区分大小写：
        # 「PICKONE」和已有的「PickOne」会落到同一个目录，config.json 里就会出现
        # 两个指向同一份数据的键。所以这里按大小写无关比较，和别名的规则一致。
        folded = text.casefold()
        for existing in self.load_categories():
            if existing.casefold() == folded:
                raise ValidationError(f"类别标识已存在（大小写不同）: {existing}")
        # Windows 保留设备名
        reserved = {
            "con",
            "prn",
            "aux",
            "nul",
            *(f"com{index}" for index in range(1, 10)),
            *(f"lpt{index}" for index in range(1, 10)),
        }
        if folded in reserved:
            raise ValidationError(f"类别标识不能是系统保留名: {text}")
        return text

    def parse_config_text(self, text: str) -> tuple[str, str]:
        """把用户输入的「类别标识:显示名」文本解析成二元组。"""
        raw = str(text or "").strip()
        if not raw:
            raise ValidationError("请输入类别标识")
        if ":" in raw:
            img_key, category_id = raw.split(":", 1)
        elif "：" in raw:
            img_key, category_id = raw.split("：", 1)
        else:
            img_key = category_id = raw
        return self.validate_new_key(img_key.strip()), (
            self.validate_category_id(category_id) if category_id.strip() else img_key.strip()
        )

    @staticmethod
    def now() -> float:
        return time.time()
