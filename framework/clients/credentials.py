"""Authenticated encryption with a persistent key outside the database."""

import asyncio
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet

from .base import Client


class CredentialsClient(Client):
    @classmethod
    def build(cls, container):
        root = container.data_dir or container.settings.obot_data_dir
        return cls(root / "keys" / "credentials.key")

    def __init__(self, path: Path):
        self.path = path
        self._cipher = None
        self._lock = asyncio.Lock()

    async def _get_cipher(self):
        async with self._lock:
            if self._cipher is None:

                def load():
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    except FileExistsError:
                        return Fernet(self.path.read_bytes())
                    with os.fdopen(descriptor, "wb") as file:
                        key = Fernet.generate_key()
                        file.write(key)
                    return Fernet(key)

                self._cipher = await asyncio.to_thread(load)
            return self._cipher

    async def encrypt(self, values: dict) -> str:
        cipher = await self._get_cipher()
        return await asyncio.to_thread(lambda: cipher.encrypt(json.dumps(values).encode()).decode())

    async def decrypt(self, value: str) -> dict:
        cipher = await self._get_cipher()
        return await asyncio.to_thread(lambda: json.loads(cipher.decrypt(value.encode())))
