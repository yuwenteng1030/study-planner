"""估時校正：哪些紀錄能用、中位數與結論、從資料庫撈打勾紀錄。"""

from __future__ import annotations

import datetime as _dt

import pytest

from core.calibration import ACCURATE, MIN_SAMPLES, OVER, TOO_FEW, UNDER, Sample, calibrate, usable
from db import repositories as repo
from db.models import User

T0 = _dt.datetime(2026, 10, 20, 9, 0)


def _s(task_id: int, actual: int | None, est: int = 20, difficulty: str = "Easy", minutes_later: int = 0) -> Sample:
    return Sample(
        task_id=task_id, difficulty=difficulty, est_minutes=est, actual_minutes=actual,
        done_at=T0 + _dt.timedelta(minutes=minutes_later),
    )


def _group(result, label):
    return next(g for g in result.groups if g.label == label)


def test_untouched_default_zero_and_blank_are_not_usable() -> None:
    """沒改過的預設值（＝預估）當成「準時」會讓預估看起來比實際準。"""
    kept, excluded = usable([_s(1, 20), _s(2, 0), _s(3, None), _s(4, 30)])
    assert [s.task_id for s in kept] == [4]
    assert excluded == 3


def test_only_the_first_attempt_at_a_task_counts() -> None:
    """第二次做通常快很多；算進來會把預估拉成高估。"""
    kept, excluded = usable([_s(1, 10, minutes_later=60), _s(1, 35, minutes_later=0)])
    assert [s.actual_minutes for s in kept] == [35]
    assert excluded == 1


def test_first_attempt_that_was_untouched_hides_the_retry_too() -> None:
    """第一次沒填，就不拿第二次頂替——第二次量的不是預估要估的東西。"""
    kept, _ = usable([_s(1, 20, minutes_later=0), _s(1, 12, minutes_later=60)])
    assert kept == []


def test_too_few_samples_give_no_verdict() -> None:
    result = calibrate([_s(i, 40) for i in range(MIN_SAMPLES - 1)])
    easy = _group(result, "Easy")
    assert easy.n == MIN_SAMPLES - 1 and easy.median_ratio == 2.0
    assert easy.verdict == TOO_FEW


def test_verdict_uses_the_median_ratio_per_difficulty() -> None:
    samples = (
        [_s(i, 40, difficulty="Easy") for i in range(MIN_SAMPLES)]  # 2 倍：低估
        + [_s(100 + i, 10, difficulty="Medium") for i in range(MIN_SAMPLES)]  # 0.5 倍：高估
        + [_s(200 + i, 22, difficulty="Hard") for i in range(MIN_SAMPLES)]  # 1.1 倍：準
    )
    result = calibrate(samples)
    assert [g.label for g in result.groups] == ["Easy", "Medium", "Hard", "全部"]
    assert _group(result, "Easy").verdict == UNDER
    assert _group(result, "Medium").verdict == OVER
    assert _group(result, "Hard").verdict == ACCURATE
    assert _group(result, "Easy").median_diff == 20


def test_one_very_slow_task_does_not_drag_the_whole_group() -> None:
    samples = [_s(i, 22) for i in range(MIN_SAMPLES - 1)] + [_s(99, 300)]
    assert _group(calibrate(samples), "Easy").verdict == ACCURATE


def test_difficulties_without_data_are_still_listed() -> None:
    result = calibrate([])
    assert [(g.label, g.n, g.verdict) for g in result.groups] == [
        ("Easy", 0, TOO_FEW), ("Medium", 0, TOO_FEW), ("Hard", 0, TOO_FEW), ("全部", 0, TOO_FEW),
    ]


# ---------------------------------------------------------------------------
# 資料庫
# ---------------------------------------------------------------------------


@pytest.fixture()
def session(db_session):
    db_session.add_all([
        User(id=1, email="a@example.com", display_name="A"),
        User(id=2, email="b@example.com", display_name="B"),
    ])
    db_session.flush()
    return db_session


def _plan(session, user_id: int):
    from core.planner import build_plan
    from core.scoring import JDStats, UserContext

    start = _dt.date(2026, 10, 20)
    core_plan = build_plan(
        topics=repo.load_topic_nodes(session), tasks=repo.load_task_inputs(session),
        start_date=start, days=3, daily_minutes=60, user=UserContext(), jd=JDStats(),
    )
    plan = repo.create_plan(
        session, user_id=user_id, goal_text="校正測試", parsed_goal={}, start_date=start,
        days=3, daily_minutes=60, core_plan=core_plan,
    )
    session.flush()
    return plan


def test_samples_are_this_users_done_items_with_the_task_estimate(session) -> None:
    mine, theirs = _plan(session, 1), _plan(session, 2)
    first, second, untouched = sorted(mine.items, key=lambda i: i.id)[:3]
    repo.mark_item_done(session, first, actual_minutes=first.task.est_minutes + 7)
    repo.mark_item_done(session, second)
    repo.mark_item_done(session, sorted(theirs.items, key=lambda i: i.id)[0], actual_minutes=99)
    session.flush()

    samples = repo.load_calibration_samples(session, 1)
    assert {s.task_id for s in samples} == {first.task_id, second.task_id}
    assert untouched.task_id not in {s.task_id for s in samples}
    got = next(s for s in samples if s.task_id == first.task_id)
    assert (got.est_minutes, got.actual_minutes, got.difficulty) == (
        first.task.est_minutes, first.task.est_minutes + 7, first.task.difficulty,
    )
