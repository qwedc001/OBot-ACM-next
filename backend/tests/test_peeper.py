from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest

from framework.clients.artifacts import ArtifactClient
from framework.clients.jobs import JobQueue
from framework.clients.peeper import PeeperClient
from framework.integration.peeper_worker import (
    MAX_OUTPUT_EDGE,
    MAX_OUTPUT_PIXELS,
    RenderTooLarge,
    render_image,
)

from .conftest import USER


@pytest.mark.parametrize(
    "mode,verdict,offset,rank_count",
    [
        ("now", "Accepted", 0, 1),
        ("now", "Wrong Answer", 0, 1),
        ("full", "Accepted", 1, 1),
        ("full", "Accepted", 3, 1),
        ("full", "Accepted", 1, 200),
    ],
)
async def test_real_submodule_renderer_produces_scoped_png_without_writing_source(
    database, tmp_path, mode, verdict, offset, rank_count
):
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    day = now.date() - timedelta(days=offset)
    user = {"name": "测试同学", "uid": "2", "register_at": 0}
    payload = {
        "submissions": [
            {
                "user": user,
                "score": 100,
                "verdict": "Accepted",
                "problem_id": "1",
                "problem_name": "A + B",
                "at": int(now.timestamp()),
            },
            {
                "user": user,
                "score": 0,
                "verdict": "Wrong Answer",
                "problem_id": "2",
                "problem_name": "训练题",
                "at": int(now.timestamp()),
            },
        ],
        "rankings": [
            {
                "user": {**user, "uid": str(index + 2), "name": f"测试同学{index + 1}"},
                "accepted": "3",
                "rank": "1",
                "unrated": False,
            }
            for index in range(rank_count)
        ],
    }
    snapshots = [
        {"day": day.isoformat(), "captured_at": now.timestamp(), "payload": payload},
        {"day": (day + timedelta(days=1)).isoformat(), "captured_at": now.timestamp(), "payload": payload},
    ]
    artifacts = ArtifactClient(tmp_path / "artifacts", database, AsyncMock(return_value=False))
    jobs = JobQueue(timeout=60)
    await jobs.start()
    client = PeeperClient(jobs, artifacts)
    try:
        artifact = await client.render(
            USER,
            {
                "name": "测试 OJ",
                "mode": mode,
                "day": day.isoformat(),
                "verdict": verdict,
                "snapshots": snapshots,
                "timezone": "Asia/Shanghai",
            },
        )
        data = await artifacts.read(USER, artifact.id)
        assert data.startswith(b"\x89PNG\r\n\x1a\n") and artifact.width > 1000 and artifact.height > 1000
        assert artifact.width * artifact.height <= MAX_OUTPUT_PIXELS
        assert max(artifact.width, artifact.height) <= MAX_OUTPUT_EDGE
        assert await client.version() == "v1.2.5"
        (tmp_path / "sample.png").write_bytes(data)
    finally:
        await jobs.close()


def test_oversized_canvas_is_rejected_before_allocating_or_rendering(monkeypatch):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[2] / "third-party/peeper-board-generator")
    )
    section = SimpleNamespace(get_columns=lambda: 3, get_height=lambda: 100_000)
    generator = SimpleNamespace(
        section_title=section, section_content=section, section_copyright=section, render=Mock()
    )
    with pytest.raises(RenderTooLarge):
        render_image(generator)
    generator.render.assert_not_called()
