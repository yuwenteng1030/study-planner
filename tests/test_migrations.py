"""Alembic migration：從零建表、舊資料庫接軌、資料轉換。

每個測試用自己的暫存資料庫，把 ``db.session`` 的 engine 暫時換掉——
migration 走的是 ``db.session.upgrade_to_head``，跟網站啟動時一模一樣。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, event, inspect, text

import db.session as session_mod
from db.models import Base


@pytest.fixture
def engine(tmp_path: Path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'mig.db'}", future=True)

    @event.listens_for(eng, "connect")
    def _fk_on(dbapi_connection, _record):  # noqa: ANN001
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    monkeypatch.setattr(session_mod, "_engine", eng)
    yield eng
    eng.dispose()


def _latest() -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(session_mod._alembic_config()).get_current_head()


def _head(eng) -> str:
    with eng.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def test_empty_database_is_built_to_match_the_models(engine) -> None:
    session_mod.upgrade_to_head()
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    # migration 建出來的 schema 跟 db/models.py 完全一致：改了 model 忘了寫 migration 會紅
    assert diff == []
    assert _head(engine) == _latest()


def test_running_twice_is_harmless(engine) -> None:
    session_mod.upgrade_to_head()
    session_mod.upgrade_to_head()
    assert _head(engine) == _latest()


def _legacy_schema(eng, *, with_reason_source: bool) -> None:
    """導入 Alembic 以前的樣子：baseline 的 7 張表，沒有版本表。"""
    from alembic import command

    cfg = session_mod._alembic_config()
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0001")
        conn.execute(text("DROP TABLE alembic_version"))
        if not with_reason_source:
            conn.execute(text("ALTER TABLE plan_items DROP COLUMN reason_source"))


def _seed_two_active_plans(eng, done_at_local: _dt.datetime) -> None:
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO topics (id,name,domain,base_weight,prereq,key_points) VALUES ('a','A','python',5,'[]','[]')"))
        conn.execute(text("INSERT INTO tasks (source,title,url,topic_id,difficulty,est_minutes,task_type) VALUES ('HR','T','https://x/1','a','Easy',10,'problem')"))
        conn.execute(text("INSERT INTO users (id,email,display_name,daily_api_quota) VALUES (1,'a@b','me',50)"))
        for pid, created in ((1, "2026-10-01 00:00:00"), (2, "2026-10-02 00:00:00")):
            conn.execute(
                text("INSERT INTO plans (id,user_id,goal_text,parsed_goal,start_date,days,daily_minutes,level,status,created_at) "
                     "VALUES (:id,1,'g','{}','2026-10-01',3,60,'normal','active',:c)"),
                {"id": pid, "c": created},
            )
        conn.execute(
            text("INSERT INTO plan_items (plan_id,task_id,scheduled_date,order_in_day,priority,status,done_at,key_points) "
                 "VALUES (2,1,'2026-10-02',0,'must','done',:at,'[]')"),
            {"at": done_at_local.strftime("%Y-%m-%d %H:%M:%S.%f")},
        )


def test_legacy_database_is_adopted_and_upgraded(engine) -> None:
    _legacy_schema(engine, with_reason_source=False)
    local = _dt.datetime(2026, 10, 2, 9, 30)
    _seed_two_active_plans(engine, local)

    session_mod.upgrade_to_head()

    assert _head(engine) == _latest()
    insp = inspect(engine)
    assert "reason_source" in {c["name"] for c in insp.get_columns("plan_items")}
    assert "tz" in {c["name"] for c in insp.get_columns("users")}
    with engine.connect() as conn:
        statuses = dict(conn.execute(text("SELECT id, status FROM plans")).all())
        stored = conn.execute(text("SELECT done_at FROM plan_items")).scalar_one()
        tz = conn.execute(text("SELECT tz FROM users")).scalar_one()
    # 兩份 active 只留最新的，另一份收起來（不刪）
    assert statuses == {1: "archived", 2: "active"}
    # done_at 從這台機器的本地時間換成 UTC
    expected = local.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    assert _dt.datetime.fromisoformat(str(stored)) == expected
    assert tz == "Asia/Taipei"


def test_second_active_plan_is_rejected_by_the_database(engine) -> None:
    session_mod.upgrade_to_head()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id,email,display_name,daily_api_quota) VALUES (1,'a@b','me',50)"))
        conn.execute(text("INSERT INTO plans (user_id,goal_text,parsed_goal,start_date,days,daily_minutes,level,status) "
                          "VALUES (1,'g','{}','2026-10-01',3,60,'normal','active')"))
        # 收起來的計畫不受限制
        conn.execute(text("INSERT INTO plans (user_id,goal_text,parsed_goal,start_date,days,daily_minutes,level,status) "
                          "VALUES (1,'g','{}','2026-10-01',3,60,'normal','archived')"))
    with pytest.raises(Exception, match="UNIQUE"):
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO plans (user_id,goal_text,parsed_goal,start_date,days,daily_minutes,level,status) "
                              "VALUES (1,'g','{}','2026-10-01',3,60,'normal','active')"))


def test_database_built_by_create_all_is_stamped_as_latest(engine) -> None:
    Base.metadata.create_all(engine)
    session_mod.upgrade_to_head()
    assert _head(engine) == _latest()


def test_downgrade_and_upgrade_again_keep_the_data(engine) -> None:
    """0002 的 downgrade 在有資料、外鍵開著的資料庫上也要能跑（SQLite batch 會重建 users 表）。"""
    from alembic import command

    session_mod.upgrade_to_head()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id,email,display_name,daily_api_quota) VALUES (1,'a@b','me',50)"))
        conn.execute(text("INSERT INTO plans (user_id,goal_text,parsed_goal,start_date,days,daily_minutes,level,status) "
                          "VALUES (1,'g','{}','2026-10-01',3,60,'normal','active')"))
    cfg = session_mod._alembic_config()
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()
        with conn.begin():
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "0001")
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.commit()
    assert _head(engine) == "0001"
    session_mod.upgrade_to_head()
    assert _head(engine) == _latest()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM plans")).scalar_one() == 1
        # 外鍵檢查在 migration 之後恢復
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
