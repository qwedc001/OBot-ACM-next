import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("separator", [".", ":"])
def test_real_nonebot_matchers_help_denial_and_duplicate_event(separator):
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.nonebot_probe", separator],
        cwd=root,
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "ok": True,
        "public_identity_roles": True,
        "duplicate_suppressed": True,
        "superuser_scene_equivalence": True,
        "canonical_paths_and_aliases": True,
        "three_native_roles": True,
        "portable_reply": True,
        "adapter_reply_sequence": True,
        "delivery_failure_recorded": True,
        "group_mentions_sender": True,
        "native_priority_and_block": True,
        "ordered_client_replies": True,
        "native_arguments_and_start": True,
        "return_not_sent": True,
        "session_reauthorization": True,
        "graphical_help": importlib.util.find_spec("pixie") is not None
        and importlib.util.find_spec("easy_pixie") is not None,
    }


@pytest.mark.parametrize("mode", ["default", "localstore", "orm"])
def test_official_plugins_use_fresh_schema_and_direct_scheduler(mode):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.infrastructure_probe", mode],
        cwd=root,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "ok": True,
        "mode": mode,
        "fresh_schema": True,
        "direct_scheduler": True,
    }


def test_core_plugins_run_on_a_second_non_qq_adapter():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.platform_probe"],
        cwd=root,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "ok": True,
        "generic_handlers": True,
        "qualified_identities": True,
        "platform_roles": True,
        "proactive_send": True,
        "unsupported_content_explicit": True,
        "graphical_help": importlib.util.find_spec("pixie") is not None
        and importlib.util.find_spec("easy_pixie") is not None,
    }
