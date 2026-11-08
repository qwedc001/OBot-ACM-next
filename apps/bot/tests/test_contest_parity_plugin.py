import json
import subprocess
import sys
from pathlib import Path


def test_contest_parity_native_routing():
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.contest_parity_probe"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1])["ok"] is True
