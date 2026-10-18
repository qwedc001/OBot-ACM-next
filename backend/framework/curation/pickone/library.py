"""A scene's visible stores, using qualified keys without changing the disk format.

Public files and each group's files live in separate LocalStore directories. A
view contains at most public + the current group; an arbitrary group ID is never
accepted by bot commands. Prefixes disambiguate identical category names.
"""

from dataclasses import replace

from .store import NotFoundError


class LibraryView:
    def __init__(self, public, group=None):
        self.stores = {"public": public}
        if group is not None:
            self.stores["group"] = group

    def resolve(self, key):
        prefix, separator, name = key.partition(":")
        if not separator:
            prefix, name = "public", key
        if prefix not in self.stores:
            raise NotFoundError("当前会话无法访问该图库。")
        return self.stores[prefix], name

    def list_categories(self):
        return [
            replace(cat, img_key=f"{prefix}:{cat.img_key}")
            for prefix, store in self.stores.items()
            for cat in store.list_categories()
        ]

    def category_dir(self, key, audit=False):
        store, name = self.resolve(key)
        return store.category_dir(name, audit=audit)

    def image_path(self, key, filename):
        store, name = self.resolve(key)
        return store.image_path(name, filename)

    def iter_image_stats(self, key):
        store, name = self.resolve(key)
        return store.iter_image_stats(name)

    def parser_entry(self, key, filename):
        store, name = self.resolve(key)
        return store.parser_entry(name, filename)

    def load_raw_parser(self, key):
        store, name = self.resolve(key)
        return store.load_raw_parser(name)

    def save_raw_parser(self, key, data):
        store, name = self.resolve(key)
        return store.save_raw_parser(name, data)
