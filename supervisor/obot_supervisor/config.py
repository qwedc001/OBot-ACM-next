import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


def validate_branch(branch):
    """Accept plain remote branch names, never revisions, options or refspecs."""
    if (
        not isinstance(branch, str)
        or not 1 <= len(branch) <= 200
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch)
        or ".." in branch
        or branch == "HEAD"
        or any(
            not part or part.startswith(".") or part.endswith((".", ".lock")) for part in branch.split("/")
        )
    ):
        raise ValueError("Invalid branch name")
    return branch


@dataclass(frozen=True)
class Config:
    state_dir: Path
    data_dir: Path
    env_file: Path
    repository: str
    branch: str = "master"
    uv: str = "uv"
    pnpm: str = "pnpm"
    startup_timeout: float = 90
    stable_seconds: float = 5
    command_timeout: float = 900
    stop_timeout: float = 30

    @property
    def socket(self):
        return self.state_dir / "control.sock"

    @classmethod
    def load(cls, path):
        with Path(path).expanduser().open("rb") as stream:
            raw = tomllib.load(stream)
        for name in ("state_dir", "data_dir", "env_file"):
            raw[name] = Path(raw[name]).expanduser().resolve()
        result = cls(**raw)
        if not result.env_file.is_file() or not result.data_dir.is_dir():
            raise ValueError("Existing environment file and data directory required")
        if result.data_dir == result.state_dir or result.state_dir.is_relative_to(result.data_dir):
            raise ValueError("State directory must be outside runtime data")
        if not result.repository.startswith(("https://", "ssh://", "git@", "/")):
            raise ValueError("Use an explicit repository URL or absolute local test repository")
        validate_branch(result.branch)
        result.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(result.state_dir, 0o700)
        return result
