"""冒險計時：從地圖（或今日任務）開始做題就計時，按完成時自動記下實際分鐘數。"""

from __future__ import annotations

import datetime as _dt

import pytest

from core import clock
from core.planner import build_plan
from core.scoring import JDStats, UserContext
from db import repositories as repo
from db.models import ActiveAdventure, ApiUsage, TaskCompletion


def _plan(session):
    repo.ensure_user(session, 1)
    start = repo.user_today(session, 1)
    core_plan = build_plan(
        topics=repo.load_topic_nodes(session), tasks=repo.load_task_inputs(session),
        start_date=start, days=2, daily_minutes=60, user=UserContext(), jd=JDStats(),
    )
    return repo.create_plan(session, user_id=1, goal_text="t", parsed_goal={}, start_date=start,
                            days=2, daily_minutes=60, core_plan=core_plan)


def _rewind(session, minutes: float) -> None:
    """把冒險的開始時間往前調，假裝已經過了 ``minutes`` 分鐘。"""
    row = session.get(ActiveAdventure, 1)
    row.started_at = clock.utc_now() - _dt.timedelta(minutes=minutes)
    session.flush()


def test_minutes_round_up_and_are_capped() -> None:
    t0 = _dt.datetime(2026, 10, 23, 10, 0)
    assert repo.adventure_minutes(t0, t0 + _dt.timedelta(seconds=5)) == 1
    assert repo.adventure_minutes(t0, t0 + _dt.timedelta(minutes=12, seconds=1)) == 13
    assert repo.adventure_minutes(t0, t0 + _dt.timedelta(hours=30)) == repo.ADVENTURE_MAX_MINUTES


def test_one_adventure_at_a_time_and_same_task_does_not_restart(db_session) -> None:
    plan = _plan(db_session)
    a, b = plan.items[0].task_id, plan.items[1].task_id
    repo.start_adventure(db_session, 1, a)
    _rewind(db_session, 10)
    repo.start_adventure(db_session, 1, a)  # 不小心又點一次：不重新計時
    assert repo.adventure_minutes(repo.load_adventure(db_session, 1).started_at) >= 10
    repo.start_adventure(db_session, 1, b)  # 換題：換成這一題、重新計時
    adv = repo.load_adventure(db_session, 1)
    assert adv.task_id == b and repo.adventure_minutes(adv.started_at) == 1


def test_ticking_a_plan_item_records_the_timed_minutes(db_session) -> None:
    item = _plan(db_session).items[0]
    repo.start_adventure(db_session, 1, item.task_id)
    _rewind(db_session, 17.5)
    repo.mark_item_done(db_session, item)
    assert item.actual_minutes == 18
    assert repo.load_adventure(db_session, 1) is None


def test_typed_minutes_win_over_the_timer(db_session) -> None:
    item = _plan(db_session).items[0]
    repo.start_adventure(db_session, 1, item.task_id)
    _rewind(db_session, 40)
    repo.mark_item_done(db_session, item, actual_minutes=25)
    assert item.actual_minutes == 25
    assert repo.load_adventure(db_session, 1) is None  # 計時照樣收掉


def test_timer_on_another_task_is_left_alone(db_session) -> None:
    plan = _plan(db_session)
    repo.start_adventure(db_session, 1, plan.items[1].task_id)
    repo.mark_item_done(db_session, plan.items[0])
    assert plan.items[0].actual_minutes is None
    assert repo.load_adventure(db_session, 1).task_id == plan.items[1].task_id


def test_map_completion_outside_the_plan_stores_minutes(db_session) -> None:
    plan = _plan(db_session)
    in_plan = {i.task_id for i in plan.items}
    outside = next(t.id for t in repo.load_task_inputs(db_session) if t.id not in in_plan)
    repo.start_adventure(db_session, 1, outside)
    _rewind(db_session, 21.5)
    assert repo.mark_task_done_for_user(db_session, 1, outside) == "extra"
    row = db_session.query(TaskCompletion).filter_by(user_id=1, task_id=outside).one()
    assert row.actual_minutes == 22
    # 估時校正也會算進這一筆
    assert any(s.task_id == outside and s.actual_minutes == 22
               for s in repo.load_calibration_samples(db_session, 1))


def test_unknown_task_cannot_start_an_adventure(db_session) -> None:
    repo.ensure_user(db_session, 1)
    with pytest.raises(LookupError):
        repo.start_adventure(db_session, 1, 999999)


def test_deleting_the_account_also_removes_timer_and_api_usage(db_session) -> None:
    plan = _plan(db_session)
    repo.start_adventure(db_session, 1, plan.items[0].task_id)
    db_session.add(ApiUsage(user_id=1, day=_dt.date(2026, 10, 23), kind="explain", count=3))
    db_session.flush()
    counts = repo.delete_user_data(db_session, 1, drop_account=True)
    assert counts["active_adventures"] == 1 and counts["api_usage"] == 1 and counts["users"] == 1


def test_clearing_records_keeps_the_api_quota(db_session) -> None:
    """只清紀錄（不刪帳號）時不能順便把當天的 API 額度歸零，否則按一下就多一份額度。"""
    repo.ensure_user(db_session, 1)
    db_session.add(ApiUsage(user_id=1, day=_dt.date(2026, 10, 23), kind="explain", count=3))
    db_session.flush()
    counts = repo.delete_user_data(db_session, 1)
    assert counts["api_usage"] == 0
    assert db_session.query(ApiUsage).filter_by(user_id=1).count() == 1


def test_untimed_map_completions_are_not_calibration_samples(db_session) -> None:
    repo.ensure_user(db_session, 1)
    task_id = repo.load_task_inputs(db_session)[0].id
    repo.mark_task_done_for_user(db_session, 1, task_id)  # 沒計時
    assert repo.load_calibration_samples(db_session, 1) == []
