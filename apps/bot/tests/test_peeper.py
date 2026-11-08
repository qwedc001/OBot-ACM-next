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
    artifacts = ArtifactClient(tmp_path / "artifacts", database)
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
    artifacts = ArtifactClient(tmp_path / "artifacts", database)
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
        assert data.startswith(b"\x89PNG\r\n\x1a\n") and artifact.width > 300 and artifact.height > 1000
        assert artifact.width * artifact.height <= MAX_OUTPUT_PIXELS
        assert max(artifact.width, artifact.height) <= MAX_OUTPUT_EDGE
        assert await client.version() == "v1.2.5"
        (tmp_path / "sample.png").write_bytes(data)
    finally:
        await jobs.close()


async def test_peeper_client_render_user_card(database, tmp_path, monkeypatch):
    from PIL import Image

    from framework.clients import peeper

    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text("pass")
    monkeypatch.setattr(peeper, "SOURCE", source)

    async def cli(work, arguments, **kwargs):
        assert "--query_uid" in arguments or "--query_name" in arguments
        Image.new("RGB", (400, 600)).save(work / "board.png")

    monkeypatch.setattr(peeper, "run_cli", cli)
    monkeypatch.setattr(peeper, "public_addresses", AsyncMock(return_value=["8.8.8.8"]))
    artifacts = ArtifactClient(tmp_path / "artifacts", database)
    jobs = JobQueue(workers=1)
    await jobs.start()
    client = PeeperClient(jobs, artifacts)
    try:
        model = {
            "mode": "user",
            "name": "测试 OJ",
            "kind": "id",
            "value": "1",
            "host": "https://oj.example.com",
            "credentials": {"uname": "admin", "password": "pw"},
            "snapshots": [],
            "timezone": "Asia/Shanghai",
        }
        artifact = await client.render(USER, model)
        assert (artifact.width, artifact.height) == (400, 600)
        assert await artifacts.read(USER, artifact.id)
    finally:
        await jobs.close()


@pytest.mark.parametrize("limit", [5, 20, 100])
async def test_rank_forwards_official_cli_with_isolated_pinned_proxy(database, tmp_path, monkeypatch, limit):
    from contextlib import asynccontextmanager

    from PIL import Image

    from framework.clients import peeper
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text("pass")
    monkeypatch.setattr(peeper, "SOURCE", source)
    calls = []
    @asynccontextmanager
    async def proxy(host, addresses):
        assert host == "oj.example.com" and addresses == ["93.184.216.34"]
        yield "http://pinned.invalid:1234"
    async def cli(work, arguments, **kwargs):
        config = json.loads((work / "config.json").read_text())[0]
        assert config["exclude_uid"] == [3] and config["show_unrated"] is False
        assert arguments[-2:] == ["--rank", str(limit)]
        assert kwargs == {"timezone": "UTC", "proxy": "http://pinned.invalid:1234"}
        calls.append(work)
        Image.new("RGB", (30, 40)).save(work / "board.png")
    monkeypatch.setattr(peeper, "pinned_https_proxy", proxy)
    monkeypatch.setattr(peeper, "public_addresses", AsyncMock(return_value=["93.184.216.34"]))
    monkeypatch.setattr(peeper, "run_cli", cli)
    artifacts = ArtifactClient(tmp_path / "artifacts", database)
    jobs = JobQueue()
    await jobs.start()
    try:
        artifact = await PeeperClient(jobs, artifacts).render(USER, {
            "name": "synthetic", "mode": "rank", "limit": limit, "snapshots": [], "timezone": "UTC",
            "host": "https://oj.example.com/", "exclude_uid": [3], "show_unrated": False,
        })
        assert await artifacts.read(USER, artifact.id)
        assert all(not work.exists() for work in calls)
        assert not (source / "config.json").exists()
    finally:
        await jobs.close()


async def test_official_rank_cli_refresh_and_render_offline(tmp_path):
    """No internal Peeper imports/patches: public CLI + cache + loopback fake Hydro."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from PIL import Image

    from framework.clients.peeper import SOURCE, generator_config
    from framework.integration.peeper_cli import cli_workspace, run_cli
    requests = []
    class Hydro(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            requests.append(self.path)
            assert self.path == "/login"
            self.send_response(200)
            self.send_header("Set-Cookie", "sid=synthetic; Path=/")
            self.send_header("Set-Cookie", "sid.sig=synthetic; Path=/")
            self.end_headers()
            self.wfile.write(b"{}")
        def do_GET(self):
            requests.append(self.path)
            assert self.path == "/record?all=1&page=1", self.path
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"rdocs": [], "udict": {}, "pdict": {}}')
    server = ThreadingHTTPServer(("127.0.0.1", 0), Hydro)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yesterday = datetime.now(ZoneInfo("UTC")).date() - timedelta(days=1)
    model = {"name": "Synthetic Rank", "host": f"http://127.0.0.1:{server.server_port}/",
             "credentials": {"uname": "synthetic", "password": "synthetic"}}
    snapshot = {"day": yesterday.isoformat(), "captured_at": datetime.now().timestamp(),
                "payload": {"submissions": [], "rankings": [
                    {"user": {"name": f"User {i}", "uid": str(i), "register_at": 0},
                     "accepted": str(100 - i), "rank": str(i), "unrated": False} for i in range(1, 7)]}}
    try:
        async with cli_workspace(SOURCE, generator_config(model), [snapshot]) as work:
            output = work / "rank.png"
            await run_cli(work, ["--id", "board", "--rank", "5", "--output", str(output)], timezone="UTC")
            with Image.open(output) as image:
                assert image.width > 300 and image.height > 500
            (tmp_path / "rank-sample.png").write_bytes(output.read_bytes())
        assert requests == ["/login", "/record?all=1&page=1"]
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=5)
