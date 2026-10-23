"""地圖與每日計畫連動：今天的關卡標藍圈、過期的標紅圈、小熊走到今天的第一題。"""

from __future__ import annotations

import datetime as _dt
import xml.etree.ElementTree as ET

from core.game_map import PlanMarks, Stage, bear_stage
from core.map_art import render_svg
from core.planner import build_plan
from core.scoring import JDStats, UserContext
from db import repositories as repo

HR = "https://www.hackerrank.com/challenges/"


def _st(task_id: int, done: bool = False) -> Stage:
    return Stage(task_id=task_id, title=f"T{task_id}", difficulty="Easy",
                 topic_id="a", topic_name="A", zone=0, done=done)


# ---------------------------------------------------------------------------
# 小熊站哪裡
# ---------------------------------------------------------------------------


def test_bear_goes_to_todays_first_unfinished_task() -> None:
    stages = [_st(1), _st(2), _st(3), _st(4)]
    # 路線上第一個沒做的是 1，但今天排的是 3、4
    assert bear_stage(stages, PlanMarks(today=(3, 4))).task_id == 3


def test_bear_follows_todays_order_not_the_route_order() -> None:
    stages = [_st(1), _st(2), _st(3)]
    assert bear_stage(stages, PlanMarks(today=(3, 2))).task_id == 3


def test_when_today_is_done_bear_returns_to_the_route() -> None:
    stages = [_st(1), _st(2, done=True), _st(3, done=True)]
    assert bear_stage(stages, PlanMarks(today=(2, 3))).task_id == 1


def test_bear_does_not_chase_overdue_tasks() -> None:
    stages = [_st(1), _st(2), _st(3)]
    assert bear_stage(stages, PlanMarks(today=(3,), overdue=frozenset({1}))).task_id == 3


def test_without_a_plan_bear_uses_the_route() -> None:
    assert bear_stage([_st(1, done=True), _st(2)], PlanMarks()).task_id == 2


# ---------------------------------------------------------------------------
# 圈圈
# ---------------------------------------------------------------------------


def _classes(svg: str) -> dict[int, str]:
    root = ET.fromstring(svg)
    return {int(e.get("data-task")): e.get("class") for e in root.iter() if e.get("data-task")}


def test_today_and_overdue_stages_get_their_own_rings() -> None:
    stages = [_st(1), _st(2), _st(3, done=True)]
    urls = {s.task_id: f"{HR}t{s.task_id}" for s in stages}
    svg = render_svg(stages, urls, None, today_ids={2, 3}, overdue_ids={1})
    classes = _classes(svg)
    assert "overdue" in classes[1].split()
    assert "today" in classes[2].split()
    assert "today" in classes[3].split() and "done" in classes[3].split()
    assert svg.count('class="plan-ring"') == 3


def test_no_plan_means_no_rings() -> None:
    stages = [_st(1), _st(2)]
    svg = render_svg(stages, {}, None)
    assert "plan-ring" not in svg


# ---------------------------------------------------------------------------
# 從資料庫算標記
# ---------------------------------------------------------------------------


def _make_plan(session, start: _dt.date):
    repo.ensure_user(session, 1)
    core_plan = build_plan(
        topics=repo.load_topic_nodes(session), tasks=repo.load_task_inputs(session),
        start_date=start, days=3, daily_minutes=60, user=UserContext(), jd=JDStats(),
    )
    return repo.create_plan(
        session, user_id=1, goal_text="t", parsed_goal={}, start_date=start,
        days=3, daily_minutes=60, core_plan=core_plan,
    )


def test_plan_marks_split_today_and_overdue(db_session) -> None:
    today = _dt.date(2026, 10, 25)
    plan = _make_plan(db_session, today - _dt.timedelta(days=1))
    yesterday = [i for i in plan.items if i.scheduled_date == today - _dt.timedelta(days=1)]
    todays = sorted((i for i in plan.items if i.scheduled_date == today), key=lambda i: i.order_in_day)
    assert yesterday and todays
    repo.mark_item_done(db_session, yesterday[0], actual_minutes=None)
    db_session.flush()

    marks = repo.plan_marks(plan, today)
    assert marks.today == tuple(i.task_id for i in todays)
    # 昨天做完的不算過期，沒做的才算
    assert marks.overdue == frozenset(i.task_id for i in yesterday[1:])


def test_skipped_items_are_not_marked(db_session) -> None:
    today = _dt.date(2026, 10, 25)
    plan = _make_plan(db_session, today)
    first = min((i for i in plan.items if i.scheduled_date == today), key=lambda i: i.order_in_day)
    first.status = "skipped"
    db_session.flush()
    assert first.task_id not in repo.plan_marks(plan, today).today


def test_no_plan_gives_empty_marks() -> None:
    assert repo.plan_marks(None, _dt.date(2026, 10, 25)) == PlanMarks()
