import asyncio
import json
import struct
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest

from framework.clients.peeper import SOURCE, generator_config
from framework.integration.https_tunnel import pinned_https_proxy
from framework.integration.peeper_cli import (
    PeeperCLIError,
    cli_environment,
    cli_workspace,
    prepare_workspace,
    run_cli,
)


@pytest.mark.parametrize("verdict", ["AC", "WA"])
async def test_official_today_cli_logs_in_fetches_and_renders(tmp_path, verdict):
    calls = []

    class Hydro(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            values = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            calls.append((self.path, values))
            self.send_response(200)
            self.send_header("Set-Cookie", "sid=fixture; Path=/")
            self.send_header("Set-Cookie", "sid.sig=fixture; Path=/")
            self.end_headers()
            self.wfile.write(b"{}")

        def do_GET(self):
            calls.append((self.path, self.headers.get("Cookie")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"rdocs": [], "udict": {}, "pdict": {}}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Hydro)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        config = generator_config(
            {
                "name": "CLI 测试",
                "host": f"http://127.0.0.1:{server.server_port}/",
                "credentials": {"uname": "fixture-admin", "password": "test-secret"},
                # Windows cannot convert upstream's naive 1970-01-01 in a
                # positive UTC offset. Use a normal test cutoff, no clock patch.
                "exclude_before": "2000-01-01",
            }
        )
        snapshots = [
            {
                "day": (now.date() - timedelta(days=1)).isoformat(),
                "captured_at": now.timestamp(),
                "payload": {"submissions": [], "rankings": []},
            }
        ]
        prepare_workspace(SOURCE, tmp_path, config, snapshots)
        output = tmp_path / "board.png"
        await run_cli(
            tmp_path,
            ["--now", "--verdict", verdict, "--id", "board", "--separate_cols", "--output", str(output)],
            timezone="Asia/Shanghai",
        )
        assert struct.unpack(">II", output.read_bytes()[16:24])[0] > 1000
        assert calls == [
            ("/login", {"uname": ["fixture-admin"], "password": ["test-secret"]}),
            ("/record?all=1&page=1", "sid=fixture;sid.sig=fixture;"),
        ]
        cached = json.loads(
            (tmp_path / "data" / f"board-{now.date().isoformat()}.json").read_text(encoding="utf-8")
        )
        assert cached == {"submissions": [], "rankings": []}
        assert (tmp_path / "main.py").read_bytes() == (SOURCE / "main.py").read_bytes()
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "script,reason",
    [
        ("raise SystemExit(2)", "cli_exit"),
        ("pass", "missing_status"),
        (
            "from pathlib import Path; Path('last_traceback.log').write_text('sensitive upstream error')",
            "cli_failed",
        ),
    ],
)
async def test_cli_does_not_accept_zero_exit_or_stale_success_or_expose_logs(tmp_path, script, reason):
    (tmp_path / "main.py").write_text(script)
    (tmp_path / "last_traceback.log").write_text("ok")
    with pytest.raises(PeeperCLIError) as error:
        await run_cli(tmp_path, [], timezone="UTC")
    assert str(error.value) == reason


@pytest.mark.parametrize("cancel", [False, True])
async def test_cli_timeout_or_cancellation_reaps_process(tmp_path, cancel):
    marker = tmp_path / "late.txt"
    (tmp_path / "main.py").write_text(
        "import time; from pathlib import Path; time.sleep(1); Path('late.txt').write_text('alive')"
    )
    task = asyncio.create_task(run_cli(tmp_path, [], timezone="UTC", timeout=0.1 if not cancel else 10))
    if cancel:
        await asyncio.sleep(0.15)
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    await asyncio.sleep(1.1)
    assert not marker.exists()


def test_cli_does_not_inherit_bot_credentials_or_proxy_bypass(monkeypatch):
    monkeypatch.setenv("QQ_BOTS", "private")
    monkeypatch.setenv("HTTP_PROXY", "http://private.invalid")
    monkeypatch.setenv("NO_PROXY", "*")
    environment = cli_environment("Asia/Shanghai")
    assert not {"QQ_BOTS", "HTTP_PROXY", "NO_PROXY"} & environment.keys()


async def test_workspace_copies_new_upstream_assets_and_cleans_private_files(tmp_path):
    source = tmp_path / "source"
    (source / "new-assets").mkdir(parents=True)
    (source / "new-assets" / "font.txt").write_text("new resource")
    (source / "main.py").write_text("pass")
    (source / "config.json").write_text("do not copy")
    (source / "info.log").write_text("do not copy")
    config = generator_config({"name": "test"})
    async with cli_workspace(source, config) as work:
        assert (work / "new-assets" / "font.txt").read_text() == "new resource"
        assert not (work / "info.log").exists()
        assert json.loads((work / "config.json").read_text()) == [config]
        (work / "info.log").write_text("temporary upstream output")
    assert not work.exists()
    assert (source / "config.json").read_text() == "do not copy"


async def test_tunnel_rejects_private_addresses_and_other_hosts():
    with pytest.raises(ValueError):
        async with pinned_https_proxy("oj.example.com", ["127.0.0.1"]):
            pytest.fail("private connection permitted")
    async with pinned_https_proxy("oj.example.com", ["8.8.8.8"]) as proxy:
        address = urlsplit(proxy["HTTPS_PROXY"])
        reader, writer = await asyncio.open_connection(address.hostname, address.port)
        writer.write(b"CONNECT internal.example:443 HTTP/1.1\r\n\r\n")
        await writer.drain()
        assert (await reader.read()).startswith(b"HTTP/1.1 403")
        writer.close()
        await writer.wait_closed()


async def test_tunnel_connects_to_validated_ip_and_transfers_bytes(monkeypatch):
    original = asyncio.open_connection
    connections = []

    async def echo(reader, writer):
        writer.write(await reader.read(4))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def connect(host, target_port):
        if target_port == 443:
            connections.append((host, target_port))
            return await original("127.0.0.1", port)
        return await original(host, target_port)

    monkeypatch.setattr(asyncio, "open_connection", connect)
    try:
        async with pinned_https_proxy("oj.example.com", ["8.8.8.8"]) as proxy:
            address = urlsplit(proxy["HTTPS_PROXY"])
            reader, writer = await original(address.hostname, address.port)
            writer.write(b"CONNECT oj.example.com:443 HTTP/1.1\r\n\r\n")
            await writer.drain()
            assert b"200" in await reader.readuntil(b"\r\n\r\n")
            writer.write(b"test")
            await writer.drain()
            assert await reader.readexactly(4) == b"test"
            writer.close()
            await writer.wait_closed()
        assert connections == [("8.8.8.8", 443)]
    finally:
        server.close()
        await server.wait_closed()
