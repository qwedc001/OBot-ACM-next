"""Board domain inputs shared by the client, service, web and plugin."""

import re
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit


class BoardError(Exception):
    pass


def board_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[\w-]{1,32}", value):
        raise BoardError("配置名称请使用 1～32 个字母、数字、汉字、下划线或短横线。")
    return value.casefold()


def hydro_host(value: str) -> str:
    if not isinstance(value, str) or len(value) > 512:
        raise BoardError("请填写有效的 HydroOJ HTTPS 地址。")
    try:
        parts = urlsplit(value.strip())
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.port not in (None, 443)
            or not re.fullmatch(r"(?:/d/[\w-]+)?/?", parts.path)
        ):
            raise ValueError
        hostname = parts.hostname.encode("idna").decode("ascii")
        if ":" in hostname:
            hostname = f"[{hostname}]"
        return urlunsplit(("https", hostname, parts.path.rstrip("/") + "/", "", ""))
    except (ValueError, UnicodeError):
        raise BoardError(
            "请使用公网 HTTPS 地址，可包含 /d/域名 路径，不要包含账号、参数或其他路径。"
        ) from None


def day_string(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
        if parsed != value:
            raise ValueError
        return parsed
    except ValueError:
        raise BoardError("日期格式应为 YYYY-MM-DD。") from None
