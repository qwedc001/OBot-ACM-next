import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


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
        if result.branch.startswith("-") or any(c.isspace() for c in result.branch):
            raise ValueError("Invalid branch")
        result.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(result.state_dir, 0o700)
        return result
