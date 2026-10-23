"""匯出與刪除使用者資料。

這兩件事是「開放給其他人用」的最低義務，所以它們要有測試，而且測試要
守住兩個容易出錯的點：

1. **匯出不能只含進行中的計畫。** 已收起來的計畫也是使用者的紀錄。
2. **刪除的順序。** 現在的 schema 沒有一條 cascade 覆蓋 ``progress_events``，
   而 SQLite 的外鍵檢查是開著的——順序錯了會直接撞外鍵。
   這一點必須用「刪完之後四張表都是空的，而且過程沒有拋例外」來守，
   不能只檢查回傳的數字。
"""

from __future__ import annotations

from core import clock

import datetime as _dt
import json

import pytest
from sqlalchemy import func, select

from core.planner import build_plan
from core.scoring import JDStats, UserContext
from db import repositories as repo
from db.models import Plan, PlanItem, ProgressEvent, User

USER_ID = 1
OTHER_USER_ID = 2


# ---------------------------------------------------------------------------
# 測試資料
# ---------------------------------------------------------------------------


def _make_plan(session, user_id: int, *, start: _dt.date, days: int = 5) -> Plan:
    """用真的排程器排一份計畫並寫進資料庫。

    不手捏 PlanItem：手捏的資料不會踩到 ``create_plan`` 真正的行為
    （例如把舊計畫收起來），那樣測到的就不是產品路徑。
    """
    topics = repo.load_topic_nodes(session)
    tasks = repo.load_task_inputs(session)
    core_plan = build_plan(
        topics=topics,
        tasks=tasks,
        start_date=start,
        days=days,
        daily_minutes=60,
        user=UserContext(),
        jd=JDStats(),
    )
    return repo.create_plan(
        session,
        user_id=user_id,
        goal_text="測試目標",
        parsed_goal={repo.GOAL_TOPICS_KEY: ["arrays"]},
        start_date=start,
        days=days,
        daily_minutes=60,
        core_plan=core_plan,
    )


@pytest.fixture
def populated(db_session):
    """一位使用者、兩份計畫（一份已收起來）、一個已完成的項目、事件紀錄。"""
    session = db_session
    if session.get(User, USER_ID) is None:
        session.add(
            User(id=USER_ID, email="export@example.com", display_name="匯出測試")
        )
        session.flush()

    today = clock.today_in(None)
    # 第一份排完之後再排第二份，第一份會被 create_plan 收成 archived。
    _make_plan(session, USER_ID, start=today - _dt.timedelta(days=10))
    active = _make_plan(session, USER_ID, start=today)

    # 打一個勾，才會有 progress_events 與 actual_minutes 可以匯出。
    first = min(active.items, key=lambda i: (i.scheduled_date, i.order_in_day))
    repo.mark_item_done(session, first, actual_minutes=42)
    session.flush()
    return session


# ---------------------------------------------------------------------------
# 匯出
# ---------------------------------------------------------------------------


def test_export_includes_archived_plans(populated) -> None:
    """匯出要含**所有**計畫，不只進行中的那一份。

    只匯出 active 的話，使用者帶走的是一份殘缺的紀錄，而且不會發現——
    因為畫面上平常也只看得到進行中的那一份。
    """
    export = repo.export_user_data(populated, USER_ID)
    statuses = sorted(plan["status"] for plan in export["plans"])
    assert len(export["plans"]) == 2, "兩份計畫都要在裡面"
    assert statuses == ["active", "archived"]


def test_export_is_json_serialisable(populated) -> None:
    """匯出的 dict 必須能直接 json.dumps。

    日期與 datetime 不是 JSON 型別，漏轉一個欄位就會在使用者按下下載鍵的
    那一刻炸掉——而那是最不該炸的時機，因為他正要把資料帶走。
    """
    export = repo.export_user_data(populated, USER_ID)
    text = json.dumps(export, ensure_ascii=False)
    assert json.loads(text)["format_version"] == repo.EXPORT_FORMAT_VERSION


def test_export_carries_task_metadata(populated) -> None:
    """每一項要帶題目標題與網址，否則匯出檔離開這個資料庫就讀不懂。"""
    export = repo.export_user_data(populated, USER_ID)
    items = [item for plan in export["plans"] for item in plan["items"]]
    assert items
    for item in items:
        assert item["task_title"], "缺少題目標題"
        assert item["task_url"].startswith("http"), "缺少題目網址"


def test_export_keeps_observed_minutes(populated) -> None:
    """``actual_minutes`` 是觀測值，事後補不回來，匯出一定要帶。"""
    export = repo.export_user_data(populated, USER_ID)
    recorded = [
        item["actual_minutes"]
        for plan in export["plans"]
        for item in plan["items"]
        if item["status"] == "done"
    ]
    assert 42 in recorded


def test_export_includes_progress_events(populated) -> None:
    export = repo.export_user_data(populated, USER_ID)
    assert any(e["event"] == "done" for e in export["progress_events"])


def test_export_of_unknown_user_raises(db_session) -> None:
    """查不到人要拋錯，不能回一份空資料。

    沉默地回空的話，使用者會以為「我的紀錄真的是空的」，
    而實際情況是查錯了人——那是資料遺失與查詢錯誤長得一樣的最壞情況。
    """
    with pytest.raises(LookupError):
        repo.export_user_data(db_session, 99999)


def test_export_does_not_leak_other_users_data(populated) -> None:
    """另一位使用者的計畫不能出現在我的匯出檔裡。

    README 設計決策 #4（v0.1 就帶 user_id）的回報就在這裡：
    所有查詢都帶 user_id，所以這條性質是「本來就成立」而不是「額外防堵」。
    """
    session = populated
    session.add(
        User(id=OTHER_USER_ID, email="other@example.com", display_name="別人")
    )
    session.flush()
    other_plan = _make_plan(session, OTHER_USER_ID, start=clock.today_in(None))
    session.flush()

    export = repo.export_user_data(session, USER_ID)
    assert other_plan.id not in {plan["id"] for plan in export["plans"]}


# ---------------------------------------------------------------------------
# 刪除
# ---------------------------------------------------------------------------


def _counts(session) -> dict[str, int]:
    def count(model) -> int:
        return session.scalar(select(func.count()).select_from(model)) or 0

    return {
        "plans": count(Plan),
        "plan_items": count(PlanItem),
        "progress_events": count(ProgressEvent),
        "users": count(User),
    }


def test_clearing_records_empties_all_three_tables(populated) -> None:
    """清除紀錄之後 plans / plan_items / progress_events 都要是空的。

    這一條守的是刪除順序。``progress_events.item_id`` 指向 ``plan_items``、
    ``plan_id`` 指向 ``plans``，而沒有任何 cascade 覆蓋它；
    SQLite 的外鍵檢查是開著的，所以順序錯了這裡會拋 IntegrityError
    而不是靜靜留下孤兒列。
    """
    session = populated
    before = _counts(session)
    assert before["plans"] > 0 and before["progress_events"] > 0

    deleted = repo.delete_user_data(session, USER_ID)
    session.flush()

    after = _counts(session)
    assert after["plans"] == 0
    assert after["plan_items"] == 0
    assert after["progress_events"] == 0
    assert deleted["plans"] == before["plans"]


def test_clearing_records_keeps_the_account(populated) -> None:
    """「清除紀錄」不該連帳號一起刪——那是另一個按鈕。"""
    session = populated
    repo.delete_user_data(session, USER_ID, drop_account=False)
    session.flush()
    assert session.get(User, USER_ID) is not None
    assert _counts(session)["users"] == 1


def test_dropping_account_removes_the_user_row(populated) -> None:
    session = populated
    deleted = repo.delete_user_data(session, USER_ID, drop_account=True)
    session.flush()
    assert deleted["users"] == 1
    assert session.get(User, USER_ID) is None


def test_delete_reports_what_it_removed(populated) -> None:
    """回傳數量，畫面才能說「刪掉了 3 份計畫」。

    回 None 的話使用者按完按鈕只會看到一句「完成」，分不出
    「真的刪了」與「什麼都沒刪」。
    """
    session = populated
    before = _counts(session)
    deleted = repo.delete_user_data(session, USER_ID)
    assert deleted["plan_items"] == before["plan_items"]
    assert deleted["progress_events"] == before["progress_events"]
    assert sum(deleted.values()) > 0


def test_delete_does_not_touch_other_users(populated) -> None:
    """刪我的資料不能順手刪掉別人的。"""
    session = populated
    session.add(
        User(id=OTHER_USER_ID, email="other2@example.com", display_name="別人")
    )
    session.flush()
    _make_plan(session, OTHER_USER_ID, start=clock.today_in(None))
    session.flush()

    repo.delete_user_data(session, USER_ID, drop_account=True)
    session.flush()

    assert session.get(User, OTHER_USER_ID) is not None
    survivors = session.scalars(select(Plan).where(Plan.user_id == OTHER_USER_ID)).all()
    assert survivors, "別人的計畫被刪掉了"


def test_delete_is_idempotent(populated) -> None:
    """刪第二次不能炸。

    使用者按完按鈕沒看到畫面更新就再按一次，是會發生的事。
    """
    session = populated
    repo.delete_user_data(session, USER_ID)
    session.flush()
    again = repo.delete_user_data(session, USER_ID)
    assert sum(again.values()) == 0


def test_delete_on_empty_account_is_a_noop(db_session) -> None:
    """全新帳號按刪除也不能炸。"""
    session = db_session
    if session.get(User, USER_ID) is None:
        session.add(User(id=USER_ID, email="fresh@example.com", display_name="新"))
        session.flush()
    assert sum(repo.delete_user_data(session, USER_ID).values()) == 0


# ---------------------------------------------------------------------------
# 刪掉帳號之後還要能用
# ---------------------------------------------------------------------------


def test_ensure_user_recreates_a_deleted_account(populated) -> None:
    """刪掉帳號之後 ensure_user 要補回來。

    v0.1 沒有登入，``get_current_user()`` 永遠回傳 1。帳號那一列不見了
    但身分還是 1，於是下一次 ``create_plan`` 會因為外鍵指不到人而炸掉——
    使用者會看到一個壞掉的網站，而不是一個乾淨的新開始。
    """
    session = populated
    repo.delete_user_data(session, USER_ID, drop_account=True)
    session.flush()
    assert session.get(User, USER_ID) is None

    user = repo.ensure_user(session, USER_ID)
    assert user.id == USER_ID
    assert session.get(User, USER_ID) is not None


def test_ensure_user_is_not_a_reset(populated) -> None:
    """帳號已經存在時 ensure_user 不能覆寫它。"""
    session = populated
    original = session.get(User, USER_ID)
    name = original.display_name
    assert repo.ensure_user(session, USER_ID).display_name == name


def test_can_plan_again_after_dropping_the_account(populated) -> None:
    """刪掉帳號 → 補回帳號 → 再排一份計畫，整條路徑要通。

    這是「刪除帳號」真正的驗收條件：不是「資料不見了」，
    而是「資料不見了，而且網站還能用」。
    """
    session = populated
    repo.delete_user_data(session, USER_ID, drop_account=True)
    session.flush()

    repo.ensure_user(session, USER_ID)
    plan = _make_plan(session, USER_ID, start=clock.today_in(None))
    session.flush()

    assert plan.items, "重建帳號後排不出計畫"
    assert repo.load_active_plan(session, USER_ID) is not None
