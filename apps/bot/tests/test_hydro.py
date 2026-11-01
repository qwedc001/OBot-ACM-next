import asyncio
import gzip
import json
import socket
from unittest.mock import AsyncMock

import httpx
import pytest

from framework.board import BoardError
from framework.clients.hydro import HydroClient, PublicTransport, _HydroSession, public_addresses


async def test_dns_rejects_private_and_mixed_results(monkeypatch):
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop,
        "getaddrinfo",
        AsyncMock(
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
            ]
        ),
    )
    with pytest.raises(BoardError):
        await public_addresses("example.com")


async def test_transport_connects_to_checked_ip_keeps_original_host_and_sni():
    def handle(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "oj.example.com"
        assert request.extensions["sni_hostname"] == "oj.example.com"
        return httpx.Response(200, json={})

    transport = PublicTransport(
        httpx.MockTransport(handle), resolver=AsyncMock(return_value=["93.184.216.34"])
    )
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("https://oj.example.com/login")


async def test_validate_login_sudo_admin_and_cookies_are_isolated():
    seen = []
    pending, confirmed = False, False

    def handle(request):
        nonlocal pending, confirmed
        seen.append(request)
        if request.url.path == "/login":
            pending = confirmed = False
            assert "cookie" not in request.headers
            return httpx.Response(
                200,
                headers=[("Set-Cookie", "sid=session; Path=/"), ("Set-Cookie", "sid.sig=signature; Path=/")],
                json={},
            )
        assert "sid=session" in request.headers["cookie"]
        if request.url.path == "/user/sudo":
            assert pending, "Hydro refuses unsolicited password confirmation"
            confirmed = True
            return httpx.Response(200, json={})
        if request.url.path == "/manage/script":
            if not confirmed:
                pending = True
                return httpx.Response(200, json={"url": "/user/sudo"})
            return httpx.Response(200, json={"scripts": {"rp": {}, "problemStat": {}}})
        return httpx.Response(200, json={"rdocs": []})

    client = HydroClient(transport_factory=lambda: httpx.MockTransport(handle))
    for _ in range(2):
        await client.validate("https://oj.example.com", {"uname": "admin", "password": "password"})
    assert len(seen) == 10
    assert [request.url.path for request in seen[:5]] == [
        "/login",
        "/manage/script",
        "/user/sudo",
        "/manage/script",
        "/record",
    ]


async def test_login_redirect_to_private_or_other_origin_is_refused_before_following():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://127.0.0.1/secret"})

    client = HydroClient(transport_factory=lambda: httpx.MockTransport(handle))
    with pytest.raises(BoardError):
        await client.validate("https://oj.example.com", {"uname": "admin", "password": "password"})
    assert len(seen) == 1


async def test_gzip_json_is_decoded_once_with_session_cookies_preserved():
    def handle(request):
        data = {"scripts": {}} if request.url.path == "/manage/script" else {}
        headers = [("Content-Encoding", "gzip"), ("Content-Type", "application/json")]
        if request.url.path == "/login":
            headers += [("Set-Cookie", "sid=session; Path=/"), ("Set-Cookie", "sid.sig=signature; Path=/")]
        else:
            assert "sid=session" in request.headers["cookie"]
        return httpx.Response(200, headers=headers, content=gzip.compress(json.dumps(data).encode()))

    client = HydroClient(transport_factory=lambda: httpx.MockTransport(handle))
    await client.validate("https://oj.example.com", {"uname": "admin", "password": "password"})


async def test_daily_collects_only_target_day_and_refreshes_scripts_once():
    scripts = []
    pending, confirmed = False, False
    user = {"_id": 2, "uname": "Alice", "regat": "2026-01-01T00:00:00+08:00"}

    def handle(request):
        nonlocal pending, confirmed
        path = request.url.path
        if path == "/login":
            pending = confirmed = False
            return httpx.Response(
                200,
                headers=[("Set-Cookie", "sid=session; Path=/"), ("Set-Cookie", "sid.sig=signature; Path=/")],
                json={},
            )
        if path == "/user/sudo":
            assert pending
            confirmed = True
            return httpx.Response(200, json={})
        if path == "/manage/script":
            if not confirmed:
                pending = True
                return httpx.Response(200, json={"url": "/user/sudo"})
            scripts.append(json.loads(request.content)["id"])
            return httpx.Response(200, json={"rid": "record-id"})
        if path == "/record/record-id":
            return httpx.Response(200, json={"rdoc": {"status": 1}})
        if path == "/record":
            base = {"uid": 2, "pid": 1, "lang": "cpp", "status": 1, "score": 100}
            docs = [
                {**base, "_id": "future", "judgeAt": "2026-10-14T00:01:00+08:00"},
                {**base, "_id": "accepted", "judgeAt": "2026-10-13T12:01:00+08:00"},
                {**base, "_id": "old", "judgeAt": "2026-10-12T23:59:00+08:00"},
            ]
            return httpx.Response(
                200, json={"rdocs": docs, "udict": {"2": user}, "pdict": {"1": {"title": "A + B"}}}
            )
        if path == "/ranking":
            if request.headers["accept"] == "application/json":
                return httpx.Response(
                    200, json={"udocs": [user] if request.url.params["page"] == "1" else []}
                )
            return httpx.Response(
                200,
                text='<table class="data-table"><tbody><tr><td class="col--ac">5</td><td><a class="user-profile-name" href="/user/2">Alice</a></td></tr></tbody></table>',
            )
        raise AssertionError(path)

    client = HydroClient(transport_factory=lambda: httpx.MockTransport(handle))
    for _ in range(2):
        result = await client.daily(
            "https://oj.example.com",
            {"uname": "admin", "password": "password"},
            "2026-10-13",
            "Asia/Shanghai",
            refresh=True,
        )
        assert len(result["submissions"]) == 1 and result["submissions"][0]["problem_name"] == "A + B"
        assert result["rankings"][0]["accepted"] == "5"
    assert scripts == ["problemStat", "rp"]


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_sudo_replays_original_scoped_request_once(method):
    seen = []
    payload = {"id": "problemStat", "args": "{}"}

    def handle(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(200, json={"url": "/d/team/user/sudo"})
        if len(seen) == 2:
            assert request.url.path == "/d/team/user/sudo" and request.method == "POST"
            assert request.content == b"password=dummy-password"
            return httpx.Response(200, json={"url": "/d/team/manage/script"})
        assert request.method == method and request.url.path == "/d/team/manage/script"
        assert request.content == seen[0].content
        return httpx.Response(200, json={"scripts": {}} if method == "GET" else {"rid": "record-id"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        session = _HydroSession(client, "https://oj.example.com/d/team/", {"password": "dummy-password"})
        result = await session.json(
            method, "manage/script", **({"json": payload} if method == "POST" else {})
        )
    assert "url" not in result and len(seen) == 3


@pytest.mark.parametrize("redirect", ["/user/sudo", "https://other.example/user/sudo"])
async def test_sudo_challenge_cannot_loop_or_send_password_cross_origin(redirect):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"url": redirect})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        session = _HydroSession(client, "https://oj.example.com/", {"password": "dummy-password"})
        with pytest.raises(BoardError):
            await session.json("GET", "manage/script")
    assert len(seen) == (2 if redirect == "/user/sudo" else 1)


async def test_rejection_identifies_stage_and_status_without_echoing_remote_body():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(403, text="secret-account-and-password"))
    ) as client:
        session = _HydroSession(client, "https://oj.example.com/", {})
        with pytest.raises(BoardError, match="密码确认.*HTTP 403") as error:
            await session.request("POST", "user/sudo")
    assert "secret-account" not in str(error.value)
