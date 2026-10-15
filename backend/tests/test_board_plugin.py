import json
import subprocess
import sys
from pathlib import Path


def test_board_native_private_group_binding_and_permissions():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.board_probe"],
        cwd=root,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1])["ok"] is True
