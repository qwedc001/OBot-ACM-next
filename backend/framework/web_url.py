"""Validate the shared public origin, with HTTP allowed only on loopback for development."""

import ipaddress
from urllib.parse import urlsplit


def public_origin(value: str | None) -> str:
    if not value:
        raise ValueError("Public URL is not configured")
    parts = urlsplit(value)
    try:
        local = parts.hostname == "localhost" or ipaddress.ip_address(parts.hostname or "").is_loopback
    except ValueError:
        local = False
    if (
        (parts.scheme != "https" and not (parts.scheme == "http" and local))
        or not parts.netloc
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        raise ValueError("Public URL must be an HTTPS origin")
    try:
        _ = parts.port
    except ValueError:
        raise ValueError("Invalid public URL port") from None
    return value.rstrip("/")
