"""Async Hydro sessions, bounded pagination and public-address pinned HTTPS."""

import asyncio
import base64
import ipaddress
import math
import re
import socket
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx
from dateutil.parser import isoparse
from lxml import etree
from nonebot.log import logger

from ..board import BoardError, hydro_host
from .base import Client

VERDICTS = {
    1: "Accepted",
    2: "Wrong Answer",
    3: "Time Limit Exceeded",
    4: "Memory Limit Exceeded",
    5: "Output Limit Exceeded",
    6: "Runtime Error",
    7: "Compile Error",
    8: "System Error",
    11: "Hacked",
    31: "Format Error",
}


async def public_addresses(host: str) -> list[str]:
    try:
        records = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(record[4][0] for record in records))
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError
        return addresses
    except (OSError, ValueError):
        raise BoardError("HydroOJ 地址必须解析到公网地址。") from None


class PublicTransport(httpx.AsyncBaseTransport):
    """DNS validation and actual connections use the same IP, with original TLS SNI."""

    def __init__(self, delegate=None, resolver=public_addresses):
        self.delegate = delegate or httpx.AsyncHTTPTransport(retries=0)
        self.resolver = resolver

    async def handle_async_request(self, request):
        addresses = await self.resolver(request.url.host)
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise BoardError("HydroOJ 地址必须解析到公网地址。")
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=addresses[0]),
            headers=request.headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": request.url.host},
        )
        return await self.delegate.handle_async_request(pinned)

    async def aclose(self):
        await self.delegate.aclose()


def _user(doc: dict) -> dict:
    uid = str(doc.get("_id", doc.get("uid", "")))
    if not re.fullmatch(r"-?\d{1,16}", uid):
        raise BoardError("HydroOJ 用户数据格式无效。")
    name = str(doc.get("uname", ""))
    if doc.get("displayName"):
        name = f"{doc['displayName']} ({name})"
    return {
        "name": name[:96],
        "uid": uid,
        "register_at": int(isoparse(doc["regat"]).timestamp()) if doc.get("regat") else 0,
    }


class HydroClient(Client):
    @classmethod
    def build(cls, container):
        return cls(timeout=container.settings.obot_http_timeout)

    def __init__(self, timeout=15, transport_factory=PublicTransport):
        self.timeout, self.transport_factory = timeout, transport_factory
        self._budget = asyncio.Semaphore(4)
        self._refresh_lock = asyncio.Lock()
        self._refreshed = {}

    @asynccontextmanager
    async def session(self, host, credentials):
        host = hydro_host(host)
        async with (
            self._budget,
            httpx.AsyncClient(
                transport=self.transport_factory(),
                timeout=self.timeout,
                follow_redirects=False,
                trust_env=False,
                headers={"User-Agent": "OBot-ACM", "Accept": "application/json"},
            ) as client,
        ):
            session = _HydroSession(client, host, credentials)
            try:
                response = await session.request("POST", "login", data=credentials)
                if "application/json" in response.headers.get("content-type", "") and response.json().get(
                    "error"
                ):
                    raise BoardError("HydroOJ 登录失败，请检查管理员账号和密码。")
                if not client.cookies.get("sid") or not client.cookies.get("sid.sig"):
                    raise BoardError("HydroOJ 登录失败，请检查管理员账号、密码和站点地址。")
                yield session
            except (httpx.HTTPError, ValueError, KeyError, TypeError, etree.XMLSyntaxError):
                raise BoardError("HydroOJ 请求失败，请检查站点、账号权限或稍后重试。") from None

    async def validate(self, host, credentials):
        async with asyncio.timeout(45), self.session(host, credentials) as session:
            result = await session.json("GET", "manage/script")
            if "scripts" not in result:
                raise BoardError("此账号没有 HydroOJ 管理脚本权限。")
            await session.json("GET", "record?all=1&page=1")

    async def daily(self, host, credentials, day: str, timezone: str, *, refresh=False):
        async with asyncio.timeout(180), self.session(host, credentials) as session:
            if refresh:
                async with self._refresh_lock:
                    stamp = datetime.now(ZoneInfo(timezone)).date().isoformat()
                    if self._refreshed.get(host) != stamp:
                        await session.refresh_stats()
                        self._refreshed[host] = stamp
            submissions = await session.submissions(day, timezone)
            rankings = await session.rankings()
            return {"submissions": submissions, "rankings": rankings}

    async def user(self, host, credentials, uid):
        if not str(uid).isdigit() or len(str(uid)) > 9:
            raise BoardError("用户 ID 应为不超过 9 位的正整数。")
        async with asyncio.timeout(45), self.session(host, credentials) as session:
            response = await session.request(
                "GET", f"user/{uid}", headers={"Accept": "text/html"}, allow_404=True
            )
            if response.status_code == 404:
                return "未找到该用户。"

            def parse(response=response):
                html = etree.HTML(response.text)
                name = "".join(html.xpath('//div[contains(@class,"profile-header__main")]/h1/text()')).strip()
                fields = []
                for label, tooltip in (("邮箱", "复制电子邮件"), ("QQ号", "复制QQ号")):
                    data = "".join(html.xpath(f'//a[@data-tooltip="{tooltip}"]/@data-copy'))
                    if data:
                        fields.append(label + "：" + base64.b64decode(data).decode())
                description = "".join(
                    html.xpath('//div[contains(@class,"profile-header__main")]/p//text()')
                ).strip()
                bio = "".join(html.xpath('//div[contains(@class,"richmedia")]//text()')).strip()[:2048]
                return "\n".join([f"用户 {name} (UID {uid})", *fields, description, bio]).strip()

            return await asyncio.to_thread(parse)


class _HydroSession:
    def __init__(self, client, host, credentials):
        self.client, self.host, self.credentials = client, host, credentials

    async def request(self, method, path, *, allow_404=False, **kwargs):
        url = urljoin(self.host, path)
        for _ in range(4):
            chunks, size = [], 0
            async with self.client.stream(method, url, **kwargs) as streamed:
                async for chunk in streamed.aiter_bytes():
                    size += len(chunk)
                    if size > 8 * 1024 * 1024:
                        raise BoardError("HydroOJ 返回的数据过大。")
                    chunks.append(chunk)
                headers = streamed.headers.copy()
                # aiter_bytes has already decoded gzip/br; do not decode it again.
                headers.pop("content-encoding", None)
                headers.pop("content-length", None)
                response = httpx.Response(
                    streamed.status_code,
                    headers=headers,
                    content=b"".join(chunks),
                    request=streamed.request,
                )
            if response.status_code in {301, 302, 303, 307, 308}:
                target = httpx.URL(urljoin(url, response.headers.get("location", "")))
                base = httpx.URL(self.host)
                if (
                    target.scheme != base.scheme
                    or target.host != base.host
                    or target.port != base.port
                    or target.userinfo
                ):
                    raise BoardError("HydroOJ 返回了跨站重定向，已停止访问。")
                url = str(target)
                if response.status_code in {301, 302, 303}:
                    method = "GET"
                    kwargs.pop("data", None)
                    kwargs.pop("json", None)
                continue
            if response.status_code == 404 and allow_404:
                return response
            if response.status_code not in {200, 201}:
                endpoint = path.split("?", 1)[0]
                stage = {
                    "login": "登录",
                    "user/sudo": "密码确认",
                    "manage/script": "管理脚本",
                    "record": "提交记录",
                    "ranking": "用户排名",
                }.get(endpoint, "数据查询")
                logger.warning("HydroOJ request rejected at {}: HTTP {}", stage, response.status_code)
                raise BoardError(
                    f"HydroOJ 在{stage}步骤拒绝访问（HTTP {response.status_code}），请检查账号权限和站点配置。"
                )
            return response
        raise BoardError("HydroOJ 重定向次数过多。")

    async def refresh_stats(self):
        for script in ("problemStat", "rp"):
            result = await self.json("POST", "manage/script", json={"id": script, "args": "{}"})
            record_id = str(result.get("rid", ""))
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", record_id):
                raise BoardError("HydroOJ 未能启动统计刷新。")
            for _ in range(60):
                status = (await self.json("GET", f"record/{record_id}"))["rdoc"]["status"]
                if status == 1:
                    break
                if status not in {0, 20, 21, 22}:
                    raise BoardError("HydroOJ 统计刷新失败，请检查管理脚本。")
                await asyncio.sleep(1)
            else:
                raise BoardError("HydroOJ 统计刷新超时。")

    async def json(self, method, path, *, retry_sudo=True, **kwargs):
        result = (await self.request(method, path, **kwargs)).json()
        if not isinstance(result, dict) or result.get("error"):
            raise BoardError("HydroOJ 登录信息或管理员权限无效。")
        redirect = result.get("url")
        if isinstance(redirect, str):
            target, base = httpx.URL(urljoin(self.host, redirect)), httpx.URL(self.host)
            if target.path.rstrip("/").endswith("/user/sudo"):
                if (
                    target.scheme != base.scheme
                    or target.host != base.host
                    or target.port != base.port
                    or target.userinfo
                ):
                    raise BoardError("HydroOJ 返回了跨站密码确认地址，已停止访问。")
                if not retry_sudo or path.split("?", 1)[0] != "manage/script":
                    raise BoardError("HydroOJ 密码确认未通过，请检查站点的管理权限。")
                # requireSudo records the original request before allowing this POST.
                # Replay our own request, never arguments supplied by the remote page.
                await self.json(
                    "POST", "user/sudo", data={"password": self.credentials["password"]}, retry_sudo=False
                )
                return await self.json(method, path, retry_sudo=False, **kwargs)
        return result

    async def submissions(self, day, timezone):
        start = datetime.combine(datetime.fromisoformat(day).date(), time(), ZoneInfo(timezone))
        lower, upper = start.timestamp(), (start + timedelta(days=1)).timestamp()
        result, seen = [], set()
        for page in range(1, 201):
            data = await self.json("GET", f"record?all=1&page={page}")
            docs = data["rdocs"]
            if not docs:
                return result
            for doc in docs:
                if (
                    doc.get("lang") == "-"
                    or doc.get("contest") == "0" * 24
                    or "hackTarget" in doc
                    or not doc.get("judgeAt")
                ):
                    continue
                at = isoparse(doc["judgeAt"]).timestamp()
                if at < lower:
                    return result
                if at >= upper or doc.get("status") not in VERDICTS:
                    continue
                uid, pid = str(doc["uid"]), str(doc["pid"])
                key = str(doc.get("_id", (uid, pid, at)))
                if len(key) > 128 or len(pid) > 64:
                    raise BoardError("HydroOJ 提交数据格式无效。")
                if key in seen:
                    raise BoardError("HydroOJ 提交记录分页重复，已停止查询。")
                seen.add(key)
                user = _user({**data["udict"][uid], "uid": uid})
                score = float(doc.get("score") or 0)
                if not math.isfinite(score):
                    raise BoardError("HydroOJ 提交分数无效。")
                result.append(
                    {
                        "user": user,
                        "score": score,
                        "verdict": VERDICTS[doc["status"]],
                        "problem_id": pid,
                        "problem_name": str(data.get("pdict", {}).get(pid, {}).get("title", pid))[:160],
                        "at": int(at),
                    }
                )
                if len(result) > 20000:
                    raise BoardError("当天提交记录过多，请联系管理员调整榜单范围。")
        raise BoardError("HydroOJ 提交记录超过分页上限。")

    async def rankings(self):
        result, seen = [], set()
        for page in range(1, 101):
            docs = (await self.json("GET", f"ranking?page={page}"))["udocs"]
            if not docs:
                return result
            response = await self.request("GET", f"ranking?page={page}", headers={"Accept": "text/html"})

            def parse(response=response):
                html = etree.HTML(response.text)
                ranks = {}
                for row in html.xpath('//table[contains(@class,"data-table")]/tbody/tr'):
                    links = row.xpath('.//a[contains(@class,"user-profile-name")]/@href')
                    if links:
                        uid = links[0].rstrip("/").rsplit("/", 1)[-1]
                        text = "".join(row.xpath('./td[contains(@class,"col--ac")]/text()')).strip()
                        ranks[uid] = int(text.replace(",", "") or 0)
                return ranks

            accepted = await asyncio.to_thread(parse)
            new = 0
            for doc in docs:
                uid = str(doc["_id"])
                if uid in seen:
                    continue  # Hydro can repeat the logged-in account at the top of each page.
                seen.add(uid)
                new += 1
                result.append(
                    {
                        "user": _user(doc),
                        "accepted": str(accepted.get(uid, doc.get("nAccept", 0))),
                        "rank": str(len(result) + 1),
                        "unrated": False,
                    }
                )
                if len(result) > 5000:
                    raise BoardError("榜单用户过多，请联系管理员调整榜单范围。")
            if not new:
                raise BoardError("HydroOJ 排名分页重复，已停止查询。")
        raise BoardError("HydroOJ 排名超过分页上限。")
