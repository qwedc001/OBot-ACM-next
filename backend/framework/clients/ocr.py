"""Optional EasyOCR, lazy-loaded in a worker; models live in LocalStore data."""

import asyncio
import io
import threading

from .base import Client


class OcrClient(Client):
    @classmethod
    def build(cls, container):
        return cls(
            (container.data_dir or container.settings.obot_data_dir) / "ocr",
            enabled=container.settings.obot_ocr_enabled,
            gpu=container.settings.obot_ocr_gpu,
        )

    def __init__(self, directory, *, enabled=False, gpu=False):
        self.directory, self.enabled, self.gpu = directory, enabled, gpu
        self._reader = None
        self._lock = threading.Lock()

    def _read(self, data):
        import easyocr
        import numpy as np
        from PIL import Image

        from ..curation.pickone.hashing import make_thumbnail

        # Keep the lock inside the worker: cancellation cannot stop native inference.
        with self._lock:
            if self._reader is None:
                self.directory.mkdir(parents=True, exist_ok=True)
                self._reader = easyocr.Reader(
                    ["ch_sim", "en"],
                    gpu=self.gpu,
                    model_storage_directory=str(self.directory),
                    user_network_directory=str(self.directory / "user_network"),
                    verbose=False,
                )
            with Image.open(io.BytesIO(make_thumbnail(data, max_side=2048))) as image:
                image.seek(0)
                image.thumbnail((2048, 2048))
                pixels = np.array(image.convert("RGB"))
            return " ".join(self._reader.readtext(pixels, detail=0))[:512]

    async def read(self, data):
        if not self.enabled:
            return ""
        return await asyncio.to_thread(self._read, data)
