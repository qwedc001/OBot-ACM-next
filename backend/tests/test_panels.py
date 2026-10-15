"""Explicit panel API contracts; no catalogue projection or automatic publication."""

import json

import pytest
from nonebot.drivers import Response

from framework.clients.qq_menu import QQMenuClient
from framework.integration.platforms.qq_menu import QQMenuError

from .test_menus import make_bot


async def test_native_sdk_panel_pagination():
    bot, adapter = make_bot(
        [
            Response(200, content='{"records":[{"panel_id":"foreign"}],"next_cursor":"next"}'),
            Response(200, content='{"records":[{"panel_id":"second"}],"is_end":true}'),
        ]
    )
    assert [item["panel_id"] for item in await QQMenuClient().panels(bot, "c2c")] == ["foreign", "second"]
    requests = [call.args[0] for call in adapter.request.await_args_list]
    assert requests[1].url.query["cursor"] == "next"
    assert all(request.url.query["scope"] == "c2c" for request in requests)


async def test_repeated_cursor_is_a_failure():
    bot, _ = make_bot([Response(200, content='{"next_cursor":"next"}')] * 2)
    with pytest.raises(QQMenuError):
        await QQMenuClient().panels(bot, "group")


async def test_explicit_panel_create_read_update_delete_uses_native_sdk_auth():
    original = {"remark": "operator-selected", "items": [{"type": "command", "name": "/hitokoto"}]}
    updated = {"remark": "operator-selected", "items": [{"type": "command", "name": "/help"}]}
    detail = {"panel_id": "chosen", "scope": "c2c", "target_type": "all", "panel": original}
    bot, adapter = make_bot(
        [
            Response(200, content='{"panel_id":"chosen"}'),
            Response(200, content=json.dumps(detail)),
            Response(200, content='{"version":2}'),
            Response(200, content="{}"),
        ]
    )
    client = QQMenuClient()
    panel_id = await client.create_panel(bot, "c2c", original)
    assert panel_id == "chosen"
    assert await client.get_panel(bot, panel_id) == detail
    assert await client.update_panel(bot, panel_id, updated) == {"version": 2}
    assert await client.delete_panel(bot, panel_id) is None
    requests = [call.args[0] for call in adapter.request.await_args_list]
    assert [request.method for request in requests] == ["POST", "GET", "PUT", "DELETE"]
    assert requests[0].json == {"scope": "c2c", "target_type": "all", "panel": original}
    assert requests[2].json == {"panel": updated}
    assert all(request.url.path == "/v2/panels/chosen" for request in requests[1:])
    assert all(request.headers["Authorization"] == "synthetic" for request in requests)


async def test_api_success_with_missing_panel_id_is_rejected():
    bot, _ = make_bot([Response(200, content="{}")])
    with pytest.raises(QQMenuError):
        await QQMenuClient().create_panel(bot, "group", {"items": []})


@pytest.mark.parametrize("status,expected", [(400, (400, 40030006)), (404, (None, None))])
async def test_delete_failure_is_reported_without_response_text(status, expected):
    bot, _ = make_bot([Response(status, content='{"code":40030006,"message":"private platform details"}')])
    with pytest.raises(QQMenuError) as caught:
        await QQMenuClient().delete_panel(bot, "missing")
    assert (caught.value.status, caught.value.code) == expected
    assert "private platform details" not in str(caught.value)
