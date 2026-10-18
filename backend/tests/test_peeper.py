import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from framework.clients.artifacts import ArtifactClient
from framework.clients.jobs import JobQueue
from framework.clients.peeper import PeeperClient
from framework.contracts import AccessDenied
from framework.integration.image_output_worker import MAX_OUTPUT_EDGE, MAX_OUTPUT_PIXELS

from .conftest import ROOT, USER


async def test_parallel_boards_use_private_cli_workspaces_and_artifacts(database, tmp_path, monkeypatch):
    from PIL import Image

    from framework.clients import peeper

    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text("pass")
    monkeypatch.setattr(peeper, "SOURCE", source)
    entered, release = [], asyncio.Event()

    async def cli(work, arguments, **kwargs):
        config = json.loads((work / "config.json").read_text())[0]
        entered.append((work, config["board_name"]))
        if len(entered) == 2:
            release.set()
        # A serial key would prevent the second job reaching this barrier.
        await asyncio.wait_for(release.wait(), 5)
        Image.new("RGB", (31 if config["board_name"] == "one" else 47, 20)).save(work / "board.png")

    monkeypatch.setattr(peeper, "run_cli", cli)
    artifacts = ArtifactClient(tmp_path / "artifacts", database, AsyncMock(return_value=False))
    jobs = JobQueue(workers=2)
    await jobs.start()
    client = PeeperClient(jobs, artifacts)
    model = {"mode": "full", "day": "2026-10-17", "snapshots": [], "timezone": "UTC"}
    try:
        first, second = await asyncio.gather(
            client.render(USER, {**model, "name": "one"}),
            client.render(ROOT, {**model, "name": "two"}),
        )
        assert (first.width, second.width) == (31, 47)
        assert first.id != second.id
        assert len({work for work, _ in entered}) == 2
        assert all(not work.exists() for work, _ in entered)
        assert await artifacts.read(USER, first.id)
        assert await artifacts.read(ROOT, second.id)
        with pytest.raises(AccessDenied):
            await artifacts.read(USER, second.id)
        assert not (source / "config.json").exists()
    finally:
        await jobs.close()


@pytest.mark.parametrize(
    "mode,verdict,offset,rank_count",
    [
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
