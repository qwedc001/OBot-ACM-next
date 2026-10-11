import json
import subprocess
import sys
from pathlib import Path


def test_debug_native_commands_permissions_buttons_and_delivery_failure():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.debug_probe"],
        cwd=root,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "ok": True,
        "superuser_only": True,
        "private_and_group": True,
        "native_rich_payloads": True,
        "failed_delivery_explicit": True,
    }
