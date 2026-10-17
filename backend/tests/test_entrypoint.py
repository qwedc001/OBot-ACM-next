import json
import os
import subprocess
import sys
from pathlib import Path


def test_module_entrypoint_upgrades_existing_database_and_starts_all_plugins():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.entrypoint_probe"],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "backend")},
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "ok": True,
        "module_entrypoint": True,
        "upgrade_existing": True,
    }


def test_entrypoint_rejects_partially_loaded_application():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.entrypoint_probe", "--failed-plugin"],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "backend")},
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"rejected_failed_plugin": True}
