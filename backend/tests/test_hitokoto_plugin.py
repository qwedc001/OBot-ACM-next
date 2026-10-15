import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("separator", [".", ":"])
def test_hitokoto_native_permissions_aliases_help_and_delivery(separator):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.hitokoto_probe", separator],
        cwd=root,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "ok": True,
        "everyone": True,
        "aliases": True,
        "shared_help": True,
        "failure_and_recovery": True,
    }
