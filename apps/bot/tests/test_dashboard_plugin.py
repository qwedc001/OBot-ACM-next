import json
import subprocess
import sys
from pathlib import Path


def test_private_qq_login_command_and_group_no_capability():
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.dashboard_probe"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "ok": True,
        "private_login": True,
        "group_link_denied": True,
        "group_admin_verification": True,
    }
