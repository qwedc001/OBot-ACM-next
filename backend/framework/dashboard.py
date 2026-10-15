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

    def profile(self) -> dict:
        return {"label": "QQ 用户", "role": "superuser" if self.superuser else "everyone"}
