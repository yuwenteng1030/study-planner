"""回顧頁：完成率趨勢、落後主題，以及重排時記下「往後挪」。

期望值都是手寫的，不拿 ``core/review.py`` 自己的輸出回頭比。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import replace

import pytest

from core.replanner import Replan
from core.review import (
    MONTH,
    WEEK,
    ItemRecord,
    completion_trend,
    lagging_topics,
    period_start,
)
from db import repositories as repo
from db.models import TaskCompletion, User

D = _dt.date
TODAY = D(2026, 10, 22)  # 週四


def _it(
    task_id: int,
    topic: str,
    day: _dt.date,
    status: str = "todo",
    done_on: _dt.date | None = None,
    *,
    now: _dt.date | None = None,
    active: bool = True,
) -> ItemRecord:
    return ItemRecord(
        task_id=task_id, topic_id=topic, scheduled_date=day, status=status,
        done_on=done_on, current_date=now, active=active,
    )


# ---------------------------------------------------------------------------
# 期間
# ---------------------------------------------------------------------------


def test_weeks_start_on_monday_and_months_on_the_first() -> None:
    assert period_start(D(2026, 10, 22), WEEK) == D(2026, 10, 19)
    assert period_start(D(2026, 10, 19), WEEK) == D(2026, 10, 19)
    assert period_start(D(2026, 10, 25), WEEK) == D(2026, 10, 19)  # 週日還是同一週
    assert period_start(D(2026, 10, 22), MONTH) == D(2026, 10, 1)
    with pytest.raises(ValueError):
        period_start(TODAY, "year")


# ---------------------------------------------------------------------------
# 完成率趨勢
# ---------------------------------------------------------------------------


def test_trend_counts_planned_and_done_per_week_oldest_first() -> None:
    items = [
        _it(1, "a", D(2026, 10, 13), "done"),
        _it(2, "a", D(2026, 10, 14), "todo"),
        _it(3, "a", D(2026, 10, 20), "done"),
        _it(4, "a", D(2026, 10, 21), "done"),
        _it(5, "a", D(2026, 10, 22), "todo"),
    ]
    rows = completion_trend(items, TODAY, WEEK)
    assert [(r.start, r.planned, r.done) for r in rows] == [
        (D(2026, 10, 12), 2, 1),
        (D(2026, 10, 19), 3, 2),
    ]
    assert rows[0].ratio == 0.5


def test_trend_ignores_days_that_have_not_come_yet() -> None:
    """週一看本週：還沒到的日子沒有「該做而沒做」，不能拉低完成率。"""
    items = [_it(1, "a", D(2026, 10, 22), "done"), _it(2, "a", D(2026, 10, 23)), _it(3, "a", D(2026, 10, 30))]
    [row] = completion_trend(items, TODAY, WEEK)
    assert (row.planned, row.done) == (1, 1)


def test_skipped_items_stay_in_the_denominator() -> None:
    """砍掉的題目不從分母扣：放棄一半的計畫不能看起來像進步。"""
    items = [_it(1, "a", D(2026, 10, 20), "done"), _it(2, "a", D(2026, 10, 20), "skipped")]
    [row] = completion_trend(items, TODAY, WEEK)
    assert row.ratio == 0.5


def test_trend_uses_the_original_date_not_the_rescheduled_one() -> None:
    """上週沒做、重排挪到這週：上週要照樣算沒做完，不能變成 100%。"""
    items = [
        _it(1, "a", D(2026, 10, 13), "done"),
        _it(2, "a", D(2026, 10, 14), "todo", now=D(2026, 10, 21)),  # 原本上週，挪到這週
    ]
    rows = completion_trend(items, TODAY, WEEK)
    assert [(r.start, r.planned, r.done) for r in rows] == [(D(2026, 10, 12), 2, 1)]


def test_week_labels_carry_the_year() -> None:
    [row] = completion_trend([_it(1, "a", D(2026, 10, 20))], TODAY, WEEK)
    assert row.label == "2026/10/19 週"


def test_trend_by_month() -> None:
    items = [_it(1, "a", D(2026, 9, 30), "done"), _it(2, "a", D(2026, 10, 1)), _it(3, "a", D(2026, 10, 2), "done")]
    rows = completion_trend(items, TODAY, MONTH)
    assert [(r.label, r.planned, r.done) for r in rows] == [("2026-09", 1, 1), ("2026-10", 2, 1)]


# ---------------------------------------------------------------------------
# 落後主題
# ---------------------------------------------------------------------------


def test_each_kind_of_lag_is_counted_separately() -> None:
    items = [
        _it(1, "a", D(2026, 10, 20), "done", done_on=D(2026, 10, 21)),  # 晚做完
        _it(2, "a", D(2026, 10, 21), "todo"),  # 過期未做
        _it(3, "a", D(2026, 10, 20), "skipped"),  # 被移出
        _it(4, "a", D(2026, 10, 19), "todo", now=D(2026, 10, 24)),  # 過期後被往後挪
        _it(5, "a", D(2026, 10, 20), "done", done_on=D(2026, 10, 20)),  # 準時
        _it(6, "a", D(2026, 10, 30), "todo"),  # 還沒輪到
    ]
    [lag] = lagging_topics(items, moved_task_ids={4}, names={"a": "A"}, today=TODAY)
    assert (lag.late, lag.overdue, lag.cut, lag.moved) == (1, 1, 1, 1)
    assert lag.lagging == 4
    assert lag.planned == 5  # 第 6 題還沒輪到，不算
    # 第 4 題現在排在 10/24，所以不是「過期未做」，只算「被往後挪」
    assert lag.name == "A"


def test_a_task_with_several_signals_counts_once() -> None:
    """晚做完又被挪過，落後題數仍是一題。"""
    items = [_it(1, "a", D(2026, 10, 20), "done", done_on=D(2026, 10, 22))]
    [lag] = lagging_topics(items, moved_task_ids={1}, names={}, today=TODAY)
    assert (lag.late, lag.moved, lag.lagging) == (1, 1, 1)


def test_topics_are_ranked_by_lagging_count_then_rate() -> None:
    items = [
        # a：4 題落後 2 題
        *(_it(i, "a", D(2026, 10, 20), "todo") for i in (1, 2)),
        *(_it(i, "a", D(2026, 10, 20), "done", done_on=D(2026, 10, 20)) for i in (3, 4)),
        # b：2 題落後 2 題（比例較高，題數相同）
        *(_it(i, "b", D(2026, 10, 20), "todo") for i in (5, 6)),
        # c：落後 3 題
        *(_it(i, "c", D(2026, 10, 20), "skipped") for i in (7, 8, 9)),
        # d：沒落後，不出現
        _it(10, "d", D(2026, 10, 20), "done", done_on=D(2026, 10, 19)),
    ]
    lags = lagging_topics(items, moved_task_ids=set(), names={}, today=TODAY)
    assert [t.topic_id for t in lags] == ["c", "b", "a"]


def test_todo_left_in_an_archived_plan_counts_as_dropped_unless_done_elsewhere() -> None:
    """舊計畫沒做的題目算「被放下」，不是「過期未做」；之後在新計畫或地圖上做完了就不算落後。"""
    items = [
        _it(1, "a", D(2026, 10, 15), "todo", active=False),  # 放下了
        _it(2, "a", D(2026, 10, 15), "todo", active=False),  # 舊計畫沒做，新計畫做完
        _it(2, "a", D(2026, 10, 20), "done", done_on=D(2026, 10, 20)),
        _it(3, "a", D(2026, 10, 15), "todo", active=False),  # 舊計畫沒做，地圖上做完
    ]
    [lag] = lagging_topics(items, set(), {}, TODAY, done_elsewhere={3})
    assert (lag.overdue, lag.cut, lag.lagging) == (0, 1, 1)


def test_task_finished_early_in_the_new_plan_is_not_dropped_in_the_old_one() -> None:
    """新計畫把題目排到下週、使用者提早做完：那一項還沒到日子，舊計畫的同一題也不算被放下。"""
    items = [
        _it(1, "a", D(2026, 10, 15), "todo", active=False),
        _it(1, "a", D(2026, 10, 30), "done", done_on=D(2026, 10, 21)),
    ]
    assert lagging_topics(items, set(), {}, TODAY) == []


def test_done_without_a_date_is_not_late() -> None:
    """舊資料可能有 done 但沒有完成時間：不能當成晚做完，也不能炸。"""
    assert lagging_topics([_it(1, "a", D(2026, 10, 20), "done", done_on=None)], set(), {}, TODAY) == []


def test_no_lag_means_an_empty_list() -> None:
    assert lagging_topics([_it(1, "a", D(2026, 10, 20), "done", done_on=D(2026, 10, 20))], set(), {}, TODAY) == []


# ---------------------------------------------------------------------------
# 資料庫：重排記下往後挪、回顧資料跨計畫
# ---------------------------------------------------------------------------


@pytest.fixture()
def session(db_session):
    db_session.add(User(id=1, email="a@example.com", display_name="A"))
    db_session.flush()
    return db_session


def _plan(session, start: _dt.date):
    from core.planner import build_plan
    from core.scoring import JDStats, UserContext

    core_plan = build_plan(
        topics=repo.load_topic_nodes(session),
        tasks=repo.load_task_inputs(session),
        start_date=start,
        days=3,
        daily_minutes=60,
        user=UserContext(),
        jd=JDStats(),
    )
    plan = repo.create_plan(
        session, user_id=1, goal_text="回顧測試", parsed_goal={}, start_date=start,
        days=3, daily_minutes=60, core_plan=core_plan,
    )
    session.flush()
    return plan


def test_only_overdue_tasks_count_as_moved_with_the_real_replanner(session) -> None:
    """一題過期，重排會把後面每一題都往後擠；被擠到的不能算成落後。

    用真的 ``core.replanner.replan``——手捏的重排結果抓不到這件事。
    """
    from core.replanner import replan

    start, today = D(2026, 10, 20), D(2026, 10, 21)
    plan = _plan(session, start)  # 10/20–10/22，第一天的題都沒做
    first_day = {i.task_id for i in plan.items if i.scheduled_date == start}
    later = {i.task_id for i in plan.items if i.scheduled_date > start}
    original = {i.task_id: i.scheduled_date for i in plan.items}

    result = replan(
        items=repo.to_scheduled_items(plan),
        completed_task_ids=set(),
        today=today,
        end_date=D(2026, 10, 22),
        daily_minutes=60,
        prereq_closure=repo.load_prereq_closure(session),
    )
    assert result.changed
    repo.apply_replan(session, plan, result, today)
    session.flush()

    items, moved, _ = repo.load_review_data(session, 1)
    assert moved and moved <= first_day
    assert not moved & later
    # 被挪過的題，回顧時照樣用原本的日子
    by_task = {i.task_id: i for i in items}
    for task_id in moved:
        assert by_task[task_id].scheduled_date == original[task_id]
        assert by_task[task_id].now_date > original[task_id]


def test_moving_earlier_is_not_recorded(session) -> None:
    plan = _plan(session, D(2026, 10, 20))
    last = max(plan.items, key=lambda i: i.scheduled_date)
    old = last.scheduled_date
    scheduled = [
        replace(i, scheduled_date=old - _dt.timedelta(days=1)) if i.task_id == last.task_id else i
        for i in repo.to_scheduled_items(plan)
    ]
    repo.apply_replan(session, plan, Replan(items=scheduled, changed=True), D(2026, 10, 25))
    session.flush()
    _, moved, _ = repo.load_review_data(session, 1)
    assert moved == set()


def test_archived_plan_keeps_only_what_was_due_before_it_was_archived(session) -> None:
    """排新計畫時舊的被收起來：舊計畫裡還沒輪到的題不能進分母。"""
    from db.models import Plan

    first = _plan(session, D(2026, 10, 13))  # 10/13–10/15
    session.add(TaskCompletion(user_id=1, task_id=repo.load_task_inputs(session)[-1].id))
    repo.mark_item_done(session, first.items[0])
    second = _plan(session, D(2026, 10, 20))  # 收起第一份
    # 把建立時間設成固定日子：第一份 10/13 建、第二份 10/14 建 → 第一份 10/14 起的題都沒輪到
    session.get(Plan, first.id).created_at = _dt.datetime(2026, 10, 13, 9)
    session.get(Plan, second.id).created_at = _dt.datetime(2026, 10, 14, 9)
    session.flush()

    items, _, extra = repo.load_review_data(session, 1)
    old = [i for i in items if not i.active]
    assert old, "收起來之前就該做的題要留著"
    assert all(i.scheduled_date < D(2026, 10, 14) or i.status == "done" for i in old)
    assert any(i.status == "done" and i.done_on is not None for i in items)
    assert all(i.active for i in items if i.scheduled_date >= D(2026, 10, 20))
    assert len(extra) == 1
