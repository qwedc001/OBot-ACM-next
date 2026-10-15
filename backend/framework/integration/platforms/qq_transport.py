"""Select an official QQ transport without rewriting credential configuration."""

from nonebot import get_plugin_config
from nonebot.adapters.qq import Adapter
from nonebot.drivers import Request, Response

from ...config import Settings


class QQAdapter(Adapter):
    def setup(self) -> None:
        self.receive_mode = get_plugin_config(Settings).obot_qq_transport
        if self.receive_mode != "configured":
            websocket = self.receive_mode == "websocket"
            for bot in self.qq_config.qq_bots:
                bot.use_websocket = websocket
                if websocket:
                    # This intent carries both private and group @ message events.
                    # Preserve other configured intents and identity/credentials.
                    bot.intent.c2c_group_at_messages = True
        super().setup()

    async def _handle_http(self, request: Request) -> Response:
        if self.receive_mode == "websocket":
            # The public callback may remain registered during the experiment.
            # Never label a webhook-delivered response as a websocket measurement.
            return Response(409, content="WebSocket transport selected")
        return await super()._handle_http(request)
