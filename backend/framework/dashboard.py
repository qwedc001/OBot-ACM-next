"""Web identities use the same canonical bot user and current administrator flag."""

from dataclasses import dataclass

from .contracts import Actor


class DashboardError(Exception):
    pass


@dataclass(frozen=True)
class WebAccount:
    actor: Actor
    superuser: bool
    csrf: str
    group_scope: str | None = None

    def permits_tool(self, slug: str) -> bool:
        return self.group_scope is None or slug in {"board", "pickone"}

    def profile(self) -> dict:
        return {
            "label": "群管理员" if self.group_scope else "超级管理员" if self.superuser else "QQ 用户",
            "role": "group_admin" if self.group_scope else "superuser" if self.superuser else "everyone",
            "tools": ["board", "pickone"] if self.group_scope else None,
        }
