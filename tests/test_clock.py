"""時區：「今天」照使用者時區，不照伺服器的時鐘；時間戳一律 UTC。"""

from __future__ import annotations

import datetime as _dt

from core import clock

UTC = _dt.timezone.utc


def test_taipei_morning_is_already_today_while_utc_is_still_yesterday() -> None:
    # 台灣 10/24 早上 7 點＝UTC 10/23 23:00：部署在 UTC 的伺服器用 date.today() 會拿到 10/23
    now = _dt.datetime(2026, 10, 23, 23, 0, tzinfo=UTC)
    assert clock.today_in("Asia/Taipei", now) == _dt.date(2026, 10, 24)
    assert clock.today_in("UTC", now) == _dt.date(2026, 10, 23)


def test_stored_utc_stamp_becomes_the_users_date() -> None:
    stamp = _dt.datetime(2026, 10, 23, 17, 30)  # 資料庫裡不帶時區的 UTC
    assert clock.to_local_date(stamp, "Asia/Taipei") == _dt.date(2026, 10, 24)
    assert clock.to_local_date(stamp, "America/New_York") == _dt.date(2026, 10, 23)


def test_broken_timezone_falls_back_instead_of_crashing() -> None:
    now = _dt.datetime(2026, 10, 23, 23, 0, tzinfo=UTC)
    assert clock.today_in("Not/AZone", now) == clock.today_in(clock.DEFAULT_TZ, now)
    assert clock.today_in(None, now) == _dt.date(2026, 10, 24)


def test_utc_now_is_naive_utc() -> None:
    before = _dt.datetime.now(UTC).replace(tzinfo=None)
    got = clock.utc_now()
    assert got.tzinfo is None
    assert abs((got - before).total_seconds()) < 5


def test_export_stamps_say_they_are_utc() -> None:
    assert clock.iso_utc(_dt.datetime(2026, 10, 23, 1, 2, 3)) == "2026-10-23T01:02:03+00:00"
    assert clock.iso_utc(None) is None


def test_done_at_is_stored_as_utc(db_session) -> None:
    from core.planner import build_plan
    from core.scoring import JDStats, UserContext
    from db import repositories as repo

    repo.ensure_user(db_session, 1)
    start = repo.user_today(db_session, 1)
    core_plan = build_plan(
        topics=repo.load_topic_nodes(db_session), tasks=repo.load_task_inputs(db_session),
        start_date=start, days=2, daily_minutes=60, user=UserContext(), jd=JDStats(),
    )
    plan = repo.create_plan(db_session, user_id=1, goal_text="t", parsed_goal={}, start_date=start,
                            days=2, daily_minutes=60, core_plan=core_plan)
    item = plan.items[0]
    repo.mark_item_done(db_session, item)
    utc = _dt.datetime.now(UTC).replace(tzinfo=None)
    assert abs((item.done_at - utc).total_seconds()) < 5
