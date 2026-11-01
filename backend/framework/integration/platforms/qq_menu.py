"""New QQ menu API using the installed adapter's auth and request machinery."""

from nonebot.adapters import Bot
from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq.exception import ActionFailed, ApiNotAvailable, NetworkError
from nonebot.drivers import Request


class QQMenuError(Exception):
    def __init__(self, *, status: int | None = None, code: int | None = None):
        self.status, self.code = status, code
        super().__init__(f"QQ menu request failed (status={status}, code={code})")


async def request_menu(bot: Bot, *, menu: dict | None = None) -> dict:
    result = await request_discovery(
        bot,
        "GET" if menu is None else "PUT",
        ("v2", "menu"),
        payload=None if menu is None else {"menu": menu},
    )
    if menu is None:
        if "menu" not in result or result["menu"] is not None and not isinstance(result["menu"], dict):
            raise QQMenuError()
    elif type(result.get("version")) is not int:
        raise QQMenuError()
    return result


async def request_discovery(
    bot: Bot, method: str, path: tuple[str, ...], *, payload: dict | None = None, params: dict | None = None
) -> dict:
    if not isinstance(bot, QQBot):
        raise ValueError("QQ menus require a QQ bot")
    request = Request(
        method,
        bot.adapter.get_api_base().joinpath(*path),
        **({} if payload is None else {"json": payload}),
        params=params,
        timeout=15,
    )
    try:
        result = await bot._request(request)
    except ActionFailed as error:
        raise QQMenuError(status=error.status_code, code=error.code) from None
    except (ApiNotAvailable, NetworkError):
        raise QQMenuError() from None
    if not isinstance(result, dict):
        raise QQMenuError()
    return result
