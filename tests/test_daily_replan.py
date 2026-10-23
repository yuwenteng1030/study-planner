"""每份計畫每天最多重排一次：打開頁面不再每次都寫資料庫。"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import func, select

from core.planner import build_plan
from core.scoring import JDStats, UserContext
from db import repositories as repo
from db.models import ProgressEvent

START = _dt.date(2026, 10, 20)


def _plan(session):
    repo.ensure_user(session, 1)
    core_plan = build_plan(
        topics=repo.load_topic_nodes(session), tasks=repo.load_task_inputs(session),
        start_date=START, days=5, daily_minutes=60, user=UserContext(), jd=JDStats(),
    )
    return repo.create_plan(session, user_id=1, goal_text="t", parsed_goal={}, start_date=START,
                            days=5, daily_minutes=60, core_plan=core_plan)


def _replan_events(session) -> int:
    return session.scalar(select(func.count()).where(ProgressEvent.event == "replan"))


def test_behind_schedule_is_replanned_once_per_day(db_session) -> None:
    plan = _plan(db_session)
    behind = START + _dt.timedelta(days=2)  # 前兩天一題都沒做

    first = repo.ensure_replanned(db_session, plan, behind)
    second = repo.ensure_replanned(db_session, plan, behind)

    assert first is not None and first.changed
    assert second is None  # 同一天再開頁面：不重排、不寫入
    assert _replan_events(db_session) == 1
    assert plan.last_replanned_on == behind


def test_next_day_checks_again(db_session) -> None:
    plan = _plan(db_session)
    day = START + _dt.timedelta(days=1)
    repo.ensure_replanned(db_session, plan, day)
    assert repo.ensure_replanned(db_session, plan, day + _dt.timedelta(days=1)) is not None
    assert _replan_events(db_session) == 2


def test_on_schedule_claims_the_day_but_writes_no_event(db_session) -> None:
    plan = _plan(db_session)
    assert repo.ensure_replanned(db_session, plan, START) is None
    assert _replan_events(db_session) == 0
    assert plan.last_replanned_on == START


def test_another_tab_that_already_claimed_today_wins(db_session) -> None:
    """模擬另一個分頁先搶到今天：資料庫裡已經是今天，這邊的 UPDATE 改到 0 列。"""
    plan = _plan(db_session)
    behind = START + _dt.timedelta(days=2)
    db_session.execute(
        repo.update(repo.Plan).where(repo.Plan.id == plan.id).values(last_replanned_on=behind)
    )
    assert repo.ensure_replanned(db_session, plan, behind) is None
    assert _replan_events(db_session) == 0
