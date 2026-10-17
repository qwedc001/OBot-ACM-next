"""Atomic state and a bounded, sanitized startup log."""

import json
import os
import re
from collections import deque
from pathlib import Path


def sanitize(value):
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(value))
    value = re.sub(r"https?://[^\s\"'<>]+", "[endpoint]", value, flags=re.I)
    value = re.sub(r"(?i)(?:bearer\s+)\S+", "Bearer [redacted]", value)
    value = re.sub(
        r"(?i)((?:password|passwd|secret|token|authorization|cookie|api[_-]?key|input_value)\b[\"']?\s*[:=]\s*).+",
        r"\1[redacted]",
        value,
    )
    value = re.sub(r"(?<!\w)[a-zA-Z0-9_+/=-]{32,}(?!\w)", "[redacted]", value)
    value = "".join(c for c in value if c >= " " or c == "\t")
    return value.replace("`", "'")[:500]


class Tail:
    def __init__(self):
        self.lines = deque(maxlen=50)

    def add(self, line):
        self.lines.append(sanitize(line))

    def text(self):
        return "\n".join(self.lines)


class Store:
    def __init__(self, directory: Path):
        self.path = directory / "state.json"
        self.value = json.loads(self.path.read_text()) if self.path.exists() else {"reports": []}

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(self.value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
        if os.name == "posix":
            fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
