"""從學習地圖按「我 submit 過了」／「取消完成」：寫到哪裡、怎麼算、怎麼收回。

守的是三件容易錯的事：

1. **同一題只有一個地方說它做完了。** 在計畫裡的題目勾計畫項目，
   不在的才寫 ``task_completions``——兩邊都寫，今日任務頁與地圖就會對不上。
2. **取消要取消乾淨。** 地圖的進度是聯集，只取消一邊，那一關還是金色的。
3. **畫面送來的 id 不可信。** 不存在的題目不能被寫進資料庫。
"""

from __future__ import annotations

from core import clock


import pytest
from sqlalchemy import func, select

from core.planner import build_plan
from core.scoring import JDStats, UserContext
from db import repositories as repo
from db.models import ProgressEvent, TaskCompletion, User

USER_ID = 1
OTHER_ID = 2


def _plan(session, user_id: int = USER_ID):
    today = clock.today_in(None)
    core_plan = build_plan(
        topics=repo.load_topic_nodes(session),
        tasks=repo.load_task_inputs(session),
        start_date=today,
        days=3,
        daily_minutes=60,
        user=UserContext(),
        jd=JDStats(),
    )
    plan = repo.create_plan(
        session,
        user_id=user_id,
        goal_text="地圖完成測試",
        parsed_goal={},
        start_date=today,
        days=3,
        daily_minutes=60,
        core_plan=core_plan,
    )
    session.flush()
    return plan


def _off_plan_task(session, plan) -> int:
    planned = {i.task_id for i in plan.items} if plan else set()
    return next(t.id for t in repo.load_task_inputs(session) if t.id not in planned)


def _extra_rows(session, user_id: int = USER_ID) -> int:
    return session.scalar(
        select(func.count()).select_from(TaskCompletion).where(TaskCompletion.user_id == user_id)
    )


@pytest.fixture()
def session(db_session):
    db_session.add_all([
        User(id=USER_ID, email="a@example.com", display_name="A"),
        User(id=OTHER_ID, email="b@example.com", display_name="B"),
    ])
    db_session.flush()
    return db_session


# ---------------------------------------------------------------------------
# 完成
# ---------------------------------------------------------------------------


def test_planned_task_ticks_the_plan_item_not_a_second_record(session) -> None:
    plan = _plan(session)
    item = plan.items[0]

    assert repo.mark_task_done_for_user(session, USER_ID, item.task_id) == "plan"
    session.flush()

    assert item.status == "done"
    assert _extra_rows(session) == 0
    # 跟今日任務頁打勾一樣有事件紀錄
    assert session.scalar(
        select(func.count()).select_from(ProgressEvent).where(ProgressEvent.item_id == item.id)
    ) == 1


def test_off_plan_task_is_stored_as_an_extra_completion(session) -> None:
    plan = _plan(session)
    task_id = _off_plan_task(session, plan)

    assert repo.mark_task_done_for_user(session, USER_ID, task_id) == "extra"
    session.flush()

    assert _extra_rows(session) == 1
    assert all(i.status != "done" for i in plan.items)
    assert task_id in repo.load_completed_task_ids_for_user(session, USER_ID)


def test_works_without_any_plan(session) -> None:
    """還沒排過計畫也能從地圖開始做題。"""
    task_id = repo.load_task_inputs(session)[0].id
    assert repo.mark_task_done_for_user(session, USER_ID, task_id) == "extra"
    session.flush()
    assert repo.load_completed_task_ids_for_user(session, USER_ID) == {task_id}


def test_skipped_plan_item_counts_as_off_plan(session) -> None:
    """被重排移出計畫的項目不是「計畫裡的題目」，不能被偷偷勾回計畫。"""
    plan = _plan(session)
    item = plan.items[0]
    item.status = "skipped"
    session.flush()

    assert repo.mark_task_done_for_user(session, USER_ID, item.task_id) == "extra"
    assert item.status == "skipped"


def test_pressing_twice_writes_once(session) -> None:
    """使用者沒看到畫面更新就再按一次，是會發生的事。"""
    task_id = repo.load_task_inputs(session)[0].id
    repo.mark_task_done_for_user(session, USER_ID, task_id)
    session.flush()
    assert repo.mark_task_done_for_user(session, USER_ID, task_id) == "already"
    session.flush()
    assert _extra_rows(session) == 1


def test_task_done_only_in_an_archived_plan_is_already_done(session) -> None:
    first = _plan(session)
    task_id = first.items[0].task_id
    repo.mark_item_done(session, first.items[0])
    session.flush()
    second = _plan(session)  # 收起第一份
    for item in second.items:  # 新計畫裡沒有這一題（Home 排計畫時會排除做過的題）
        if item.task_id == task_id:
            item.status = "skipped"
    session.flush()

    assert repo.mark_task_done_for_user(session, USER_ID, task_id) == "already"
    assert _extra_rows(session) == 0


def test_active_todo_item_is_ticked_even_if_done_elsewhere(session) -> None:
    """這個功能上線前排的計畫，會把舊計畫做過的題再排一次。

    地圖上按「我 submit 過了」時，進行中計畫裡的那一項還是要勾——
    否則地圖是金色、今日任務頁卻還沒勾。
    """
    first = _plan(session)
    task_id = first.items[0].task_id
    repo.mark_item_done(session, first.items[0])
    session.flush()
    second = _plan(session)  # 沒排除做過的題：同一題又以 todo 出現
    again = next(i for i in second.items if i.task_id == task_id)
    assert again.status == "todo"

    # 地圖上要畫成還沒做，小卡才會給「我 submit 過了」
    assert task_id not in repo.load_map_done_task_ids(session, USER_ID)
    assert task_id in repo.load_completed_task_ids_for_user(session, USER_ID)

    assert repo.mark_task_done_for_user(session, USER_ID, task_id) == "plan"
    assert again.status == "done"
    assert task_id in repo.load_map_done_task_ids(session, USER_ID)


def test_unknown_task_is_rejected(session) -> None:
    with pytest.raises(LookupError):
        repo.mark_task_done_for_user(session, USER_ID, 999_999)
    assert _extra_rows(session) == 0


def test_completions_belong_to_one_user(session) -> None:
    task_id = repo.load_task_inputs(session)[0].id
    repo.mark_task_done_for_user(session, OTHER_ID, task_id)
    session.flush()
    assert repo.load_completed_task_ids_for_user(session, USER_ID) == set()
    assert repo.load_completed_task_ids_for_user(session, OTHER_ID) == {task_id}


# ---------------------------------------------------------------------------
# 取消
# ---------------------------------------------------------------------------


def test_undo_removes_an_extra_completion(session) -> None:
    task_id = repo.load_task_inputs(session)[0].id
    repo.mark_task_done_for_user(session, USER_ID, task_id)
    session.flush()

    assert repo.mark_task_undone_for_user(session, USER_ID, task_id) == 1
    session.flush()
    assert repo.load_completed_task_ids_for_user(session, USER_ID) == set()


def test_undo_unticks_the_plan_item(session) -> None:
    plan = _plan(session)
    item = plan.items[0]
    repo.mark_task_done_for_user(session, USER_ID, item.task_id)
    session.flush()

    assert repo.mark_task_undone_for_user(session, USER_ID, item.task_id) == 1
    session.flush()
    assert item.status == "todo"
    assert item.task_id not in repo.load_completed_task_ids_for_user(session, USER_ID)


def test_undo_clears_every_record_including_archived_plans(session) -> None:
    """舊計畫裡勾過、又在地圖上記過：取消之後兩邊都要清掉，否則那一關還是金色。"""
    first = _plan(session)
    task_id = first.items[0].task_id
    repo.mark_item_done(session, first.items[0])
    session.flush()
    _plan(session)  # 收起第一份
    session.add(TaskCompletion(user_id=USER_ID, task_id=task_id))
    session.flush()

    assert repo.mark_task_undone_for_user(session, USER_ID, task_id) == 2
    session.flush()
    assert task_id not in repo.load_completed_task_ids_for_user(session, USER_ID)


def test_undo_does_not_touch_other_users(session) -> None:
    task_id = repo.load_task_inputs(session)[0].id
    repo.mark_task_done_for_user(session, USER_ID, task_id)
    repo.mark_task_done_for_user(session, OTHER_ID, task_id)
    session.flush()

    repo.mark_task_undone_for_user(session, USER_ID, task_id)
    session.flush()
    assert repo.load_completed_task_ids_for_user(session, OTHER_ID) == {task_id}


def test_undo_of_something_never_done_is_a_noop(session) -> None:
    assert repo.mark_task_undone_for_user(session, USER_ID, 1) == 0


# ---------------------------------------------------------------------------
# 匯出與刪除
# ---------------------------------------------------------------------------


def test_export_includes_extra_completions(session) -> None:
    task_id = repo.load_task_inputs(session)[0].id
    repo.mark_task_done_for_user(session, USER_ID, task_id)
    session.flush()

    export = repo.export_user_data(session, USER_ID)
    assert export["format_version"] >= 2  # 2 起才有 task_completions
    [row] = export["task_completions"]
    assert row["task_id"] == task_id
    assert row["task_url"].startswith("https://")


def test_delete_removes_extra_completions_even_with_the_account(session) -> None:
    """``task_completions.user_id`` 指向 ``users``：沒先刪它，刪帳號會撞外鍵。"""
    task_id = repo.load_task_inputs(session)[0].id
    repo.mark_task_done_for_user(session, USER_ID, task_id)
    repo.mark_task_done_for_user(session, OTHER_ID, task_id)
    session.flush()

    deleted = repo.delete_user_data(session, USER_ID, drop_account=True)
    session.flush()
    assert deleted["task_completions"] == 1
    assert _extra_rows(session) == 0
    assert _extra_rows(session, OTHER_ID) == 1
