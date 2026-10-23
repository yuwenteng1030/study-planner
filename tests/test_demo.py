"""公開展示版：訪客帳號、範例資料、過期清理、不呼叫 Claude。"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from streamlit.testing.v1 import AppTest

from db import demo
from db import repositories as repo
from db.models import Base, PlanItem, Task, Topic, User

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TODAY = _dt.date(2026, 10, 24)


def test_visitor_gets_a_sample_plan_with_one_missed_task(db_session) -> None:
    user_id = demo.create_visitor(db_session, TODAY)
    user = db_session.get(User, user_id)
    assert demo.is_visitor(user)

    plan = repo.load_active_plan(db_session, user_id)
    assert plan is not None and plan.start_date == TODAY - _dt.timedelta(days=demo.SAMPLE_DAYS_AGO)
    focus = repo.load_focus_stages(db_session)
    assert {i.task_id for i in plan.items} <= set(focus), "範例計畫只排重點題組"

    past = [i for i in plan.items if i.scheduled_date < TODAY]
    undone = [i for i in past if i.status != "done"]
    assert len(undone) == 1, "只留一題沒做，讓每日重排有東西可挪"
    assert undone[0].scheduled_date == TODAY - _dt.timedelta(days=1)
    done = [i for i in past if i.status == "done"]
    assert done and all(i.actual_minutes for i in done), "回顧頁的估時校正要有資料"
    assert all(i.status == "todo" for i in plan.items if i.scheduled_date >= TODAY)


def test_sample_plan_replans_without_running_out_of_time(db_session) -> None:
    """第一眼看到的應該是「漏掉的題目被挪走了」，不是紅色的「時間不夠」。"""
    user_id = demo.create_visitor(db_session, TODAY)
    plan = repo.load_active_plan(db_session, user_id)
    result = repo.ensure_replanned(db_session, plan, TODAY)
    assert result is not None and result.changed
    assert result.overflow_minutes == 0 and result.extra_days == 0 and not result.culled


def test_two_visitors_do_not_share_data(db_session) -> None:
    a = demo.create_visitor(db_session, TODAY)
    b = demo.create_visitor(db_session, TODAY)
    assert a != b
    repo.delete_user_data(db_session, a, drop_account=True)
    assert repo.load_active_plan(db_session, b) is not None


def test_purge_removes_only_stale_visitors(db_session) -> None:
    db_session.add(User(id=1, email="owner@localhost", display_name="我"))
    old = demo.create_visitor(db_session, TODAY)
    fresh = demo.create_visitor(db_session, TODAY)
    now = _dt.datetime(2026, 10, 24, 12, 0)
    db_session.get(User, old).created_at = now - demo.VISITOR_TTL - _dt.timedelta(minutes=1)
    db_session.get(User, fresh).created_at = now - _dt.timedelta(hours=1)
    db_session.get(User, 1).created_at = now - _dt.timedelta(days=365)
    db_session.flush()

    assert demo.purge_stale_visitors(db_session, now) == 1
    assert db_session.get(User, old) is None
    assert db_session.get(User, fresh) is not None
    assert db_session.get(User, 1) is not None, "非訪客帳號再舊也不能刪"
    assert not db_session.scalars(select(PlanItem).join(PlanItem.plan).where(PlanItem.plan.has(user_id=old))).all()


def test_demo_never_calls_claude_unless_explicitly_allowed(monkeypatch) -> None:
    from llm import client

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.delenv("STUDY_PLANNER_DEMO", raising=False)
    assert client.is_available()
    monkeypatch.setenv("STUDY_PLANNER_DEMO", "1")
    assert not client.is_available(), "訪客重新整理就換帳號，每人額度擋不住，預設不呼叫"
    monkeypatch.setenv("STUDY_PLANNER_DEMO_LLM", "1")
    assert client.is_available()


def test_upsert_catalog_fills_an_empty_database_and_is_idempotent(tmp_path) -> None:
    from scripts.import_data import load_tasks, load_topics, upsert_catalog

    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    for _ in range(2):
        with factory() as session, session.begin():
            upsert_catalog(session)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Task)) == len(load_tasks())
        assert session.scalar(select(func.count()).select_from(Topic)) == len(load_topics())
        assert session.scalar(select(func.count()).select_from(User)) == 0, "匯入題庫不建使用者"


@pytest.fixture
def demo_env(monkeypatch, seeded_app_db):
    monkeypatch.setenv("STUDY_PLANNER_DEMO", "1")
    return seeded_app_db


@pytest.mark.parametrize(
    "page",
    ["Home.py", "pages/1_今日任務.py", "pages/2_我的資料.py", "pages/3_學習地圖.py", "pages/4_回顧.py"],
)
def test_every_page_renders_for_a_new_visitor(demo_env, page) -> None:
    app = AppTest.from_file(str(PROJECT_ROOT / "app" / page), default_timeout=60).run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert any("公開展示版" in i.value for i in app.sidebar.info)
    visitor = app.session_state["visitor_user_id"]
    assert visitor != 1, "展示版不能用到單人模式的帳號"


def test_each_browser_session_is_a_different_visitor(demo_env) -> None:
    home = str(PROJECT_ROOT / "app" / "Home.py")
    a = AppTest.from_file(home, default_timeout=60).run()
    b = AppTest.from_file(home, default_timeout=60).run()
    assert a.session_state["visitor_user_id"] != b.session_state["visitor_user_id"]
    # 同一個分頁再跑一次（切頁、按按鈕）還是同一個人
    first = a.session_state["visitor_user_id"]
    a.run()
    assert a.session_state["visitor_user_id"] == first
