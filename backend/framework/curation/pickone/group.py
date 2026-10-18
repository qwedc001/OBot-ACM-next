"""Direct group-owner edits; public submissions retain their existing review flow."""

from dataclasses import asdict

from .chat import read_image, save_entry
from .hashing import hash_id_of, make_thumbnail
from .store import ValidationError


def images(repo, store, key, page):
    store.category_or_404(key)
    stats = list(store.iter_image_stats(key))
    return {"items": [asdict(item) for item in stats[(page - 1) * 24 : page * 24]], "total": len(stats)}


def media(repo, store, key, name, pending=False):
    store.category_or_404(key)
    if pending:
        with store.audit_image_path(key, name).open("rb") as stream:
            data = stream.read(10 * 1024 * 1024 + 1)
    else:
        data = read_image(store, key, name)
    return make_thumbnail(data)


def update_category(repo, store, key, values, actor):
    store.category_or_404(key)
    title = store.validate_category_id(values.category_id)
    keys = store.validate_alias_list(values.keys, exclude_key=key)
    raw = dict(store.load_raw_config())
    raw[key] = {**raw[key], "id": title, "key": keys}
    store.save_raw_config(raw)
    log(repo, actor, "category", key)


def update_image(repo, store, key, values, actor):
    entry = store.parser_entry(key, values.name)
    if values.ocr_text is not None:
        entry["ocr_text"] = values.ocr_text
        entry["ocr_checked"] = True  # An intentionally blank human description stays blank.
    if values.comments is not None:
        from .schemas import CommentsSubmitRequest

        try:
            entry["comments"] = CommentsSubmitRequest(name=values.name, comments=values.comments).cleaned()
        except ValueError as error:
            raise ValidationError("评论最多 20 条，每条最多 32 字。") from error
    save_entry(store, key, values.name, entry)
    log(repo, actor, "image", key + "/" + values.name)


def audit(repo, store, page):
    items, total = store.list_audit_images(page=page, page_size=24)
    return {"items": [{**asdict(item), "hash_id": hash_id_of(item.md5)} for item in items], "total": total}


def review(repo, store, key, name, approve, actor):
    store.category_or_404(key)
    if approve:
        store.approve_audit_image(key, name)
    else:
        store.reject_audit_image(key, name)
    log(repo, actor, "approve" if approve else "reject", key + "/" + name)


def delete_image(repo, store, key, name, actor):
    store.category_or_404(key)
    store.image_path(key, name).unlink()
    parser = dict(store.load_raw_parser(key))
    parser.pop(name, None)
    store.save_raw_parser(key, parser)
    log(repo, actor, "delete", key + "/" + name)


def log(repo, actor, action, detail):
    repo.add_log(
        actor_id=actor, username=actor, action="pickone.group." + action, detail=detail, is_admin=True
    )
