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
from db.models import Base, Plan, Task, Topic, User

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
    plan_a = repo.load_active_plan(db_session, a)
    plan_b = repo.load_active_plan(db_session, b)
    assert plan_a.id != plan_b.id
    assert not {i.id for i in plan_a.items} & {i.id for i in plan_b.items}
    assert not {j.id for j in repo.load_jds(db_session, a)} & {j.id for j in repo.load_jds(db_session, b)}
    repo.delete_user_data(db_session, a, drop_account=True)
    assert repo.load_active_plan(db_session, b) is not None
    assert len(repo.load_jds(db_session, b)) == len(demo.SAMPLE_JDS) + 1  # 加一篇範例面經


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
    assert repo.load_active_plan(db_session, fresh) is not None, "沒過期的訪客資料要留著"
    assert not db_session.scalars(select(Plan).where(Plan.user_id == old)).all(), "過期訪客的計畫要一起刪"


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
    assert app.session_state["visitor_email"].endswith("@" + demo.VISITOR_DOMAIN), "展示版不能用到單人模式的帳號"


def test_each_browser_session_is_a_different_visitor(demo_env) -> None:
    home = str(PROJECT_ROOT / "app" / "Home.py")
    a = AppTest.from_file(home, default_timeout=60).run()
    b = AppTest.from_file(home, default_timeout=60).run()
    assert a.session_state["visitor_email"] != b.session_state["visitor_email"]
    # 同一個分頁再跑一次（切頁、按按鈕）還是同一個人
    first = a.session_state["visitor_email"]
    a.run()
    assert a.session_state["visitor_email"] == first


def test_a_deleted_visitors_tab_never_lands_in_the_next_visitors_account(demo_env) -> None:
    """SQLite 會把刪掉的最大 id 發給下一個新帳號。舊分頁要拿到新帳號，不能接手別人的。"""
    from db.session import session_scope

    home = str(PROJECT_ROOT / "app" / "Home.py")
    a = AppTest.from_file(home, default_timeout=60).run()
    a_email = a.session_state["visitor_email"]
    with session_scope() as session:
        a_id = session.scalar(select(User.id).where(User.email == a_email))
        repo.delete_user_data(session, a_id, drop_account=True)

    b = AppTest.from_file(home, default_timeout=60).run()
    b_email = b.session_state["visitor_email"]
    with session_scope() as session:
        b_id = session.scalar(select(User.id).where(User.email == b_email))
    assert b_id == a_id, "前提：id 真的被重複使用了（不成立的話這個測試沒在測東西）"

    a.run()
    assert not a.exception
    assert a.session_state["visitor_email"] not in (a_email, b_email), "舊分頁要拿到一個全新的帳號"


def test_visitor_count_is_capped(db_session, monkeypatch) -> None:
    monkeypatch.setattr(demo, "MAX_VISITORS", 3)
    ids = []
    for _ in range(5):
        demo.purge_stale_visitors(db_session)
        ids.append(demo.create_visitor(db_session, TODAY))
    alive = [i for i in ids if db_session.get(User, i) is not None]
    assert len(alive) == 3
    assert ids[-1] in alive, "最新的一定留著"


def test_demo_does_not_silently_recreate_missing_accounts(db_session, monkeypatch) -> None:
    """補建的帳號不在訪客網域，永遠不會被清掉。"""
    monkeypatch.setenv("STUDY_PLANNER_DEMO", "1")
    with pytest.raises(LookupError):
        repo.ensure_user(db_session, 999)


def test_new_visitors_are_rate_limited(db_session, monkeypatch) -> None:
    monkeypatch.setattr(demo, "MAX_NEW_VISITORS_PER_MINUTE", 2)
    now = _dt.datetime(2026, 10, 24, 12, 0)
    for _ in range(2):
        user = db_session.get(User, demo.create_visitor(db_session, TODAY))
        user.created_at = now - _dt.timedelta(seconds=10)
    db_session.flush()
    assert demo.too_many_new_visitors(db_session, now)
    assert not demo.too_many_new_visitors(db_session, now + _dt.timedelta(minutes=2))
