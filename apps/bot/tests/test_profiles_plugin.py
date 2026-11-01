import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("separator", [".", ":"])
def test_profiles_native_public_permissions_templates_aliases_and_help(separator):
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.profiles_probe", separator],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        encoding="utf-8",
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "ok": True,
        "everyone": True,
        "aliases": True,
        "help": True,
    }
