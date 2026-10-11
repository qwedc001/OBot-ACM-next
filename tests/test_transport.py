from unittest.mock import AsyncMock, patch

import pytest
from nonebot.adapters.qq import Adapter
from nonebot.adapters.qq.config import Config as QQConfig
from nonebot.drivers import Request, Response
from pydantic import ValidationError

from framework.config import Settings
from framework.integration.platforms.qq_transport import QQAdapter


@pytest.mark.parametrize("mode", ["configured", "webhook", "websocket"])
def test_transport_keeps_credentials_and_other_intents(mode):
    adapter = QQAdapter.__new__(QQAdapter)
    adapter.qq_config = QQConfig(
        qq_bots=[
            {
                "id": "synthetic-id",
                "secret": "synthetic-secret",
                "use_websocket": False,
                "intent": {"direct_message": True, "guild_messages": True},
            }
        ]
    )
    with (
        patch(
            "framework.integration.platforms.qq_transport.get_plugin_config",
            return_value=Settings(obot_qq_transport=mode),
        ),
        patch.object(Adapter, "setup") as native_setup,
    ):
        adapter.setup()
    native_setup.assert_called_once()
    info = adapter.qq_config.qq_bots[0]
    assert info.id == "synthetic-id" and info.secret == "synthetic-secret"
    assert info.intent.direct_message and info.intent.guild_messages
    assert info.use_websocket == (mode == "websocket")
    assert info.intent.c2c_group_at_messages == (mode == "websocket")


async def test_websocket_does_not_dispatch_incoming_webhook():
    adapter = QQAdapter.__new__(QQAdapter)
    adapter.receive_mode = "websocket"
    request = Request("POST", "https://example.test/qq/webhook", content="synthetic")
    with patch.object(Adapter, "_handle_http", AsyncMock()) as native_http:
        response = await adapter._handle_http(request)
    assert response.status_code == 409
    native_http.assert_not_awaited()


@pytest.mark.parametrize("mode", ["configured", "webhook"])
async def test_other_modes_keep_native_webhook_signature_check(mode):
    adapter = QQAdapter.__new__(QQAdapter)
    adapter.receive_mode = mode
    request = Request("POST", "https://example.test/qq/webhook", content="synthetic")
    with patch.object(Adapter, "_handle_http", AsyncMock(return_value=Response(403))) as native_http:
        response = await adapter._handle_http(request)
    assert response.status_code == 403
    native_http.assert_awaited_once_with(request)


def test_invalid_transport_cannot_silently_fall_back():
    with pytest.raises(ValidationError):
        Settings(obot_qq_transport="typo")
