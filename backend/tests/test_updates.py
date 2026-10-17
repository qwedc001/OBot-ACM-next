import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from framework.contracts import AccessDenied, Role
from framework.services.updates import UpdatesService, report_pages

from .conftest import access


def test_git_native_commands_permissions_and_acknowledgement_before_restart():
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.git_probe"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        encoding="utf-8",
        timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"ok": True}


async def test_update_service_rejects_everyone_and_group_admin():
    client = SimpleNamespace(request=AsyncMock())
    service = UpdatesService(SimpleNamespace(clients=SimpleNamespace(updater=client)))
    for role in (Role.EVERYONE, Role.GROUP_ADMIN):
        context = SimpleNamespace(command_allowed=access(role))
        with pytest.raises(AccessDenied):
            await service.execute(context, "update")
    client.request.assert_not_awaited()
    await service.execute(SimpleNamespace(command_allowed=access(Role.SUPERUSER)), "restart")
    client.request.assert_awaited_once_with("restart")


def test_error_report_keeps_last_50_lines_and_splits_markdown():
    report = {
        "id": "test",
        "outcome": "rolled_back",
        "log": "\n".join(f"{n}: {'x' * 100}" for n in range(70)),
    }
    pages = report_pages(report)
    assert len(pages) > 1
    body = "\n".join(pages)
    assert "19: " not in body
    assert "20: " in body and "69: " in body
    assert "已恢复最近可用版本" in body
    assert all(len(page) < 2200 and page.count("```text") == 1 for page in pages)
