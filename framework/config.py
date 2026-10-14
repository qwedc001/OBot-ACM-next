from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr


class Settings(BaseModel):
    """NoneBot loads these OBOT_* settings alongside native adapter configs."""

    obot_data_dir: Path = Path("data")
    obot_platforms: tuple[str, ...] = ("qq",)
    obot_qq_transport: Literal["configured", "webhook", "websocket"] = "configured"
    obot_http_timeout: float = Field(default=15, gt=0, le=120)
    obot_http_concurrency: int = Field(default=4, ge=1, le=32)
    obot_job_workers: int = Field(default=2, ge=1, le=16)
    obot_job_capacity: int = Field(default=32, ge=1, le=1024)
    obot_job_timeout: float = Field(default=60, gt=0, le=1800)
    obot_shutdown_timeout: float = Field(default=15, gt=0, le=120)
    obot_superusers: frozenset[str] = frozenset()
    obot_internal_token: SecretStr | None = None
    obot_artifact_ttl: float = Field(default=3600, gt=0)
    obot_artifact_max_bytes: int = Field(default=16 * 1024 * 1024, gt=0)
    obot_public_base_url: str | None = None
    obot_board_bind_ttl: int = Field(default=900, ge=60, le=3600)
    obot_board_timezone: str = "Asia/Shanghai"
