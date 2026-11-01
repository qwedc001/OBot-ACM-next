"""Importable render fixtures; independent of NoneBot/pytest setup."""

import io
import json
import os
import time
from pathlib import Path

from framework.clients.renderer import RenderedImage


class ProbeBackend:
    def render(self, model):
        if model.get("started"):
            Path(model["started"]).write_text(json.dumps({"pid": os.getpid(), "at": time.time()}))
        if model.get("release"):
            deadline = time.monotonic() + 30
            while not Path(model["release"]).exists():
                if time.monotonic() > deadline:
                    raise TimeoutError("Test gate was not released")
                time.sleep(0.01)
        if model.get("exit"):
            os._exit(17)
        if model.get("error"):
            raise ValueError("private-content-must-not-escape")
        assert "OBOT_TEST_SECRET" not in os.environ
        from PIL import Image

        output = io.BytesIO()
        width = model.get("width", 24)
        Image.new("RGBA", (width, 12), model.get("color", "red")).save(output, format="PNG")
        return RenderedImage(output.getvalue(), "image/png", width, 12)

    def render_pages(self, model):
        return (self.render(model), self.render({**model, "width": 36}))
