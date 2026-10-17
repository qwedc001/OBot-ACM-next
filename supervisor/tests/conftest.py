import sqlite3
from contextlib import closing

import pytest

from obot_supervisor.config import Config
from obot_supervisor.engine import Engine


@pytest.fixture
def engine(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    with closing(sqlite3.connect(data / "bot.sqlite3")) as db, db:
        db.execute("create table test (value text)")
        db.execute("insert into test values ('original')")
    (data / "nonebot_plugin_orm").mkdir()
    (data / "nonebot_plugin_orm" / "revision").write_text("old")
    env = tmp_path / ".env.dev"
    env.write_text("")
    state = tmp_path / "state"
    state.mkdir()
    result = Engine(Config(state, data, env, str(tmp_path / "remote"), stable_seconds=0.01))
    result.state.update(good="old", commit="a" * 40)
    result.save()
    return result
