"""Chat operations on the same PickOneStore used by Dashboard.

Called only through CurationService.run: file work is off-thread and shares the
Dashboard lock. No second config, parser, image directory or database engine.
"""

import difflib
import os
import random
import tempfile
from dataclasses import asdict

from .hashing import ThumbnailError, make_thumbnail, md5_of_bytes
from .store import MAX_COMMENT_LENGTH, MAX_COMMENTS_PER_IMAGE, StoreError, ValidationError

MAX_IMAGE_BYTES = 10 * 1024 * 1024


def create_category(repo, store, key, title, actor, aliases=None):
    key = store.validate_new_key(key)
    title = store.validate_category_id(title)
    keys = store.validate_alias_list(aliases or [title])
    raw = dict(store.load_raw_config())
    if key in raw:
        raise ValidationError("本群已经有这个类别，请使用其他标识。")
    raw[key] = {"id": title, "key": keys}
    store.save_raw_config(raw)
    store.ensure_category_dir(key)
    repo.add_log(actor_id=actor, username=actor, action="pickone.group.create", detail=key, is_admin=True)


def categories(repo, store):
    return [asdict(category) for category in store.list_categories()]


def category(store, keyword, *, fuzzy=True):
    choices = store.list_categories()
    if keyword is None or keyword.casefold() in {"rand", "随机", "随便"}:
        populated = [item for item in choices if item.image_count]
        if not populated:
            raise StoreError("还没有可用的表情包，请先在 Dashboard 配置类别并上传图片。")
        return random.choice(populated)
    keyword = keyword.casefold()
    keys = {item.img_key.casefold(): item for item in choices}
    if keyword in keys:
        return keys[keyword]
    # An explicit public:/group: selector never falls back to a fuzzy match in
    # another library. For a short name, the current group's category wins.
    if ":" in keyword:
        raise StoreError("当前会话没有这个图库，请发送 /来只 查看可用类别。")
    for prefix in ("group:", "public:"):
        if prefix + keyword in keys:
            return keys[prefix + keyword]
    aliases = {}
    for item in choices:
        for name in (item.id, *item.keys):
            aliases.setdefault(name.casefold(), []).append(item)
    matches = aliases.get(keyword, [])
    if not matches and fuzzy:
        near = difflib.get_close_matches(keyword, aliases, n=1, cutoff=0.6)
        matches = aliases[near[0]] if near else []
    matches = {item.img_key: item for item in matches}
    local = {key: value for key, value in matches.items() if key.startswith("group:")}
    if local:
        matches = local
    if len(matches) == 1:
        return next(iter(matches.values()))
    raise StoreError("类别不存在或别名不唯一，请发送 /来只 查看类别标识。")


def resolve_id(stats, prefix, preview=()):
    # Full IDs remain exact. Otherwise prefer the last preview, as in original OBot.
    exact = [item for item in stats if item.hash_id == prefix]
    if exact:
        return exact[0]
    for candidates in ([item for item in stats if item.hash_id in preview], stats):
        matches = [item for item in candidates if item.hash_id.startswith(prefix)]
        if not matches:
            matches = [item for item in candidates if item.hash_id.casefold().startswith(prefix.casefold())]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise StoreError("ID 前缀匹配到多张图片，请提供更长的 ID。")
    raise StoreError("没有找到该 ID，请先预览类别中的图片。")


def select(repo, store, keyword, query="", prefix=None, preview=()):
    cat = category(store, keyword)
    stats = list(store.iter_image_stats(cat.img_key))
    if not stats:
        raise StoreError("这个类别还没有表情包，请发送 /添加来只 类别 并附带图片。")
    if prefix:
        if isinstance(preview, dict):
            preview = preview.get("ids", ()) if preview.get("category") == cat.img_key else ()
        picked = resolve_id(stats, prefix, preview)
    else:
        if query:
            texts = {item.name: (item.ocr_text + " " + " ".join(item.comments)).casefold() for item in stats}
            matches = [item for item in stats if query.casefold() in texts[item.name]]
            if not matches:
                near = difflib.get_close_matches(query.casefold(), texts.values(), n=1, cutoff=0.6)
                matches = [item for item in stats if texts[item.name] in near]
            stats = matches
        if not stats:
            raise StoreError("没有找到符合文字描述的图片，可在 Dashboard 补充描述。")
        picked = random.choice(stats)
    return cat, picked, read_image(store, cat.img_key, picked.name)


def read_image(store, key, name):
    path = store.image_path(key, name)
    with path.open("rb") as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise StoreError("图片超过 10 MiB，暂时无法发送。")
    return data


def preview(repo, store, keyword):
    cat = category(store, keyword)
    stats = list(store.iter_image_stats(cat.img_key))
    if not stats:
        raise StoreError("这个类别还没有表情包。")
    picked = random.sample(stats, min(8, len(stats)))
    images = []
    for item in picked:
        try:
            thumbnail = make_thumbnail(read_image(store, cat.img_key, item.name), max_side=400)
        except (OSError, ValueError, ThumbnailError):
            thumbnail = None
        images.append({**asdict(item), "thumbnail": thumbnail})
    return cat, images


def save_entry(store, key, name, entry):
    parser = dict(store.load_raw_parser(key))
    parser[name] = entry
    store.save_raw_parser(key, parser)


def picked(repo, store, key, name):
    entry = store.parser_entry(key, name)
    entry["pickup_times"] += 1
    save_entry(store, key, name, entry)


def like(repo, store, key, name):
    entry = store.parser_entry(key, name)
    entry["likes"] += 1
    save_entry(store, key, name, entry)
    return entry["likes"]


def comment(repo, store, key, name, text):
    text = text.strip()
    if not text or len(text) > MAX_COMMENT_LENGTH:
        raise ValidationError(f"评论需为 1–{MAX_COMMENT_LENGTH} 个字符。")
    entry = store.parser_entry(key, name)
    comments = list(entry["comments"])
    if text in comments:
        raise ValidationError("评论重复，添加失败。")
    if len(comments) >= MAX_COMMENTS_PER_IMAGE:
        raise ValidationError("该图片评论已满，请在 Dashboard 管理已有评论。")
    entry["comments"] = [*comments, text]
    save_entry(store, key, name, entry)
    return len(entry["comments"])


def upload(repo, store, keyword, data, actor, admin):
    cat = category(store, keyword, fuzzy=False)
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValidationError("图片需在 10 MiB 以内。")
    try:
        make_thumbnail(data)  # Same bounded decoder as Dashboard; never trust the attachment MIME alone.
    except (OSError, ValueError, ThumbnailError) as error:
        raise ValidationError("无法解码图片，或图片像素数超过限制。") from error
    name = md5_of_bytes(data) + ".gif"
    if any((store.category_dir(cat.img_key, audit=audit) / name).exists() for audit in (False, True)):
        return "duplicate", cat.img_key, name
    directory = store.category_dir(cat.img_key, audit=not admin)
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
        temporary = stream.name
        stream.write(data)
    try:
        os.replace(temporary, directory / name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    repo.add_log(
        actor_id=actor,
        username=actor,
        action="pickone.upload",
        detail=f"{cat.img_key}/{name}",
        is_admin=admin,
    )
    return ("added" if admin else "pending"), cat.img_key, name


def approve(repo, store, actor):
    items, _ = store.list_audit_images(page_size=1_000_000)
    counts = {"approved": 0, "duplicate": 0}
    for item in items:
        counts[store.approve_audit_image(item.img_key, item.name)] += 1
    repo.add_log(
        actor_id=actor, username=actor, action="pickone.audit_batch", detail=str(counts), is_admin=True
    )
    return counts


def missing_text(repo, store):
    pending = []
    for cat in store.list_categories():
        for stat in store.iter_image_stats(cat.img_key):
            if stat.needs_ocr and not stat.extra.get("ocr_checked"):
                pending.append((cat.img_key, stat.name))
                if len(pending) == 8:
                    return pending
    return pending


def apply_ocr(repo, store, key, name, text):
    entry = store.parser_entry(key, name)
    # The OCR job runs outside the file lock; a human may have edited the description meanwhile.
    if not entry["ocr_text"].strip():
        entry["ocr_text"] = text[:512]
        entry["ocr_checked"] = True
        save_entry(store, key, name, entry)
