"""端到端測試：資料庫 → 排程 → 存檔 → 落後重排 → 打勾。

單元測試證明每個零件對，這一支證明**接得起來**。接縫是最容易出錯的地方：
欄位名拼錯、順序讀反、重排寫回時對不上原本的項目——這些單元測試都看不到。

用獨立的暫存 SQLite，不碰專案根目錄的 ``study.db``。
"""

from __future__ import annotations

import csv
import datetime as _dt
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from core.planner import build_plan
from core.replanner import replan
from core.scoring import TIER_BONUS, TIER_MUST, TIER_RECOMMENDED
from db import repositories as repo
from db.models import Base, PlanItem, ProgressEvent, Task, Topic, User

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOPICS_PATH = PROJECT_ROOT / "data" / "topics.yaml"
TASKS_PATH = PROJECT_ROOT / "data" / "tasks.csv"

START = _dt.date(2026, 10, 21)
USER_ID = 1


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    """乾淨的暫存資料庫，灌入真實的 topics.yaml 與 tasks.csv。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", future=True)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    with factory() as db:
        for row in yaml.safe_load(TOPICS_PATH.read_text(encoding="utf-8")):
            db.add(
                Topic(
                    id=row["id"],
                    name=row["name"],
                    base_weight=int(row["base_weight"]),
                    prereq=list(row.get("prereq") or []),
                    key_points=list(row.get("key_points") or []),
                )
            )
        with TASKS_PATH.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                db.add(
                    Task(
                        source=row["source"],
                        title=row["title"],
                        url=row["url"],
                        topic_id=row["topic"],
                        prereq=row["prereq"] or None,
                        difficulty=row["difficulty"],
                        est_minutes=int(row["est_minutes"]),
                        static_note=row["static_note"] or None,
                        focus_stage=int(row["focus"]) if row.get("focus") else None,
                    )
                )
        db.add(User(id=USER_ID, email="test@example.com", display_name="測試"))
        db.commit()
        yield db


def _make_plan(session: Session, days: int = 10, daily: int = 60, **kwargs):
    """走完整條路徑排出並存下一份計畫。"""
    topics = repo.load_topic_nodes(session)
    tasks = repo.load_task_inputs(session)
    core_plan = build_plan(
        topics, tasks, START, days=days, daily_minutes=daily,
        user=kwargs.get("user"),
    )
    parsed_goal = kwargs.get("parsed_goal", {})
    plan = repo.create_plan(
        session,
        user_id=USER_ID,
        goal_text="程式上機測驗（Python 基礎與資料結構）",
        parsed_goal=parsed_goal,
        start_date=START,
        days=days,
        daily_minutes=daily,
        core_plan=core_plan,
    )
    session.commit()
    return plan, core_plan


# ---------------------------------------------------------------------------
# 資料載入
# ---------------------------------------------------------------------------


def test_repository_load_matches_source_files(session: Session) -> None:
    """repository 讀出來的東西要跟資料檔一致。"""
    topics = repo.load_topic_nodes(session)
    tasks = repo.load_task_inputs(session)
    assert len(topics) == 21
    assert len(tasks) == 125
    # prereq 必須真的讀進來，不能全是空的——這種錯會讓排程看起來正常但順序全錯。
    assert any(t.prereq for t in topics)
    assert {t.id for t in topics} >= {t.topic_id for t in tasks}

    # 題庫擴充（BACKLOG 2）之後的新不變量：知識地圖上每個主題都要有題目，
    # 只有 ``time_complexity`` 例外——它刻意維持零題目，理由見
    # ``tests/test_planner.py::test_time_complexity_stays_task_free``。
    # 沒有這條斷言的話，之後改主題 id 就可能悄悄讓某個主題變成空殼：
    # 排程不會報錯，使用者只會看到計畫裡莫名少了一塊。
    uncovered = {t.id for t in topics} - {t.topic_id for t in tasks}
    assert uncovered == {"time_complexity"}, f"沒有題目的主題：{sorted(uncovered)}"


def test_plan_is_persisted_and_reloadable(session: Session) -> None:
    """存下去再讀回來，項目要一模一樣。"""
    plan, core_plan = _make_plan(session)

    reloaded = repo.load_active_plan(session, USER_ID)
    assert reloaded is not None
    assert reloaded.id == plan.id
    assert len(reloaded.items) == len(core_plan.items)

    original = [(i.task_id, i.scheduled_date, i.order_in_day) for i in core_plan.items]
    stored = [
        (i.task_id, i.scheduled_date, i.order_in_day)
        for i in sorted(reloaded.items, key=lambda x: (x.scheduled_date, x.order_in_day))
    ]
    assert stored == original


def test_static_note_becomes_the_reason(session: Session) -> None:
    """沒有 LLM 時，``reason`` 要自動填上 ``tasks.static_note``。

    這是「LLM 掛掉功能不減」的具體驗證：任務卡上的「為什麼重要」
    在完全沒有 API key 的情況下也必須有內容。
    """
    _make_plan(session)
    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None
    with_reason = [i for i in plan.items if i.reason]
    assert len(with_reason) == len(plan.items), "每一項都該有說明文字"


def test_key_points_snapshot_is_copied(session: Session) -> None:
    """``plan_items.key_points`` 是快照，不是即時查詢。"""
    _make_plan(session)
    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None
    assert any(i.key_points for i in plan.items)


# ---------------------------------------------------------------------------
# 自評程度真的會改變計畫
# ---------------------------------------------------------------------------


def test_familiar_topics_change_the_saved_plan(session: Session) -> None:
    """自評「已熟」要改變存進資料庫的計畫內容。

    這條接縫特別容易斷：``plans.level`` 只有一個值，對所有主題一視同仁，
    而 ``score = base_weight × jd × gap × goal``——所有主題乘同一個 gap
    等於等比放大，排序完全不變。所以程度必須逐主題存在 ``parsed_goal`` 裡。
    這個測試就是在防止有人把它改回單一 level。
    """
    _, baseline = _make_plan(session)

    parsed_goal = {repo.FAMILIAR_KEY: ["hashmaps", "python_basic"]}
    user = repo.build_user_context(parsed_goal)
    assert user.levels == {"hashmaps": "familiar", "python_basic": "familiar"}

    _, changed = _make_plan(session, user=user, parsed_goal=parsed_goal)
    assert [i.task_id for i in baseline.items] != [i.task_id for i in changed.items]


def test_build_user_context_round_trips(session: Session) -> None:
    """``parsed_goal`` 存進去再還原，評分條件要一致。"""
    parsed_goal = {
        repo.FAMILIAR_KEY: ["arrays"],
        repo.NEW_KEY: ["graphs"],
        repo.GOAL_TOPICS_KEY: ["hashmaps", "strings"],
    }
    _make_plan(session, user=repo.build_user_context(parsed_goal), parsed_goal=parsed_goal)

    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None
    restored = repo.build_user_context(plan.parsed_goal)
    assert restored.levels == {"arrays": "familiar", "graphs": "new"}
    assert restored.goal_topics == frozenset({"hashmaps", "strings"})


def test_creating_a_plan_archives_the_previous_one(session: Session) -> None:
    """同時只能有一份進行中的計畫，否則「今日任務」不知道顯示哪一份。"""
    first, _ = _make_plan(session)
    second, _ = _make_plan(session)
    active = repo.load_active_plan(session, USER_ID)
    assert active is not None
    assert active.id == second.id
    session.refresh(first)
    assert first.status == "archived"


# ---------------------------------------------------------------------------
# 打勾與事件紀錄
# ---------------------------------------------------------------------------


def test_marking_done_records_an_event_and_actual_minutes(session: Session) -> None:
    """打勾要同時更新狀態、記下實際分鐘數、留下事件。"""
    _make_plan(session)
    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None
    item = sorted(plan.items, key=lambda i: (i.scheduled_date, i.order_in_day))[0]

    repo.mark_item_done(session, item, actual_minutes=23)
    session.commit()

    session.refresh(item)
    assert item.status == "done"
    assert item.actual_minutes == 23
    assert item.done_at is not None

    events = session.scalars(
        select(ProgressEvent).where(ProgressEvent.item_id == item.id)
    ).all()
    assert [e.event for e in events] == ["done"]
    assert events[0].payload["actual_minutes"] == 23


def test_undo_keeps_actual_minutes(session: Session) -> None:
    """取消完成要保留實際分鐘數——那是觀測值，不是狀態。"""
    _make_plan(session)
    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None
    item = sorted(plan.items, key=lambda i: (i.scheduled_date, i.order_in_day))[0]

    repo.mark_item_done(session, item, actual_minutes=31)
    repo.mark_item_undone(session, item)
    session.commit()

    session.refresh(item)
    assert item.status == "todo"
    assert item.done_at is None
    assert item.actual_minutes == 31, "實際花的時間不該因為取消打勾而消失"


def test_progress_summary_counts_correctly(session: Session) -> None:
    _make_plan(session)
    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None

    day_one = sorted(
        (i for i in plan.items if i.scheduled_date == START),
        key=lambda i: i.order_in_day,
    )
    repo.mark_item_done(session, day_one[0], actual_minutes=10)
    session.commit()

    summary = repo.progress_summary(plan, START)
    assert summary["today_total"] == len(day_one)
    assert summary["today_done"] == 1
    assert 0 < summary["today_ratio"] <= 1
    assert summary["overall_done"] == 1


# ---------------------------------------------------------------------------
# 落後重排（完整路徑）
# ---------------------------------------------------------------------------


def test_falling_behind_reschedules_into_the_future(session: Session) -> None:
    """漏做前幾天後，未完成的項目要被搬到今天之後。

    這是「網站真的能用」的關鍵：不重排的話，使用者漏做一天，
    計畫就永遠停在過去的日期，今日任務頁永遠是空的。
    """
    _make_plan(session, days=10, daily=60)
    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None

    # 假裝今天是第 4 天，前三天一題都沒做。
    today = START + _dt.timedelta(days=3)
    end_date = plan.start_date + _dt.timedelta(days=plan.days - 1)

    result = replan(
        items=repo.to_scheduled_items(plan),
        completed_task_ids=repo.completed_task_ids(plan),
        today=today,
        end_date=end_date,
        daily_minutes=plan.daily_minutes,
    )
    assert result.changed is True

    repo.apply_replan(session, plan, result)
    session.commit()

    reloaded = repo.load_active_plan(session, USER_ID)
    assert reloaded is not None
    live = [i for i in reloaded.items if i.status == "todo"]
    assert live, "重排後應該還有待做項目"
    assert all(i.scheduled_date >= today for i in live), (
        "重排後不該還有項目留在過去的日期"
    )

    # 漏做三天後，剩下的時間裝不完所有必學項目，所以計畫會往後延。
    # 延期必須被回報，不能默默發生，也不能改成硬塞進最後一天。
    last_day = max(i.scheduled_date for i in live)
    if last_day > end_date:
        assert result.extra_days == (last_day - end_date).days
        assert result.overflow_minutes > 0


def test_replan_respects_daily_limit_in_the_database(session: Session) -> None:
    """重排寫回資料庫後，每天的總分鐘數仍不能超過上限。"""
    _make_plan(session, days=10, daily=60)
    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None
    today = START + _dt.timedelta(days=5)
    end_date = plan.start_date + _dt.timedelta(days=plan.days - 1)

    result = replan(
        repo.to_scheduled_items(plan),
        repo.completed_task_ids(plan),
        today,
        end_date,
        plan.daily_minutes,
    )
    repo.apply_replan(session, plan, result)
    session.commit()

    per_day: dict[_dt.date, int] = {}
    for item in session.scalars(
        select(PlanItem).where(PlanItem.plan_id == plan.id, PlanItem.status == "todo")
    ).all():
        per_day[item.scheduled_date] = (
            per_day.get(item.scheduled_date, 0) + item.task.est_minutes
        )
    for day, minutes in per_day.items():
        assert minutes <= plan.daily_minutes, f"{day} 排了 {minutes} 分鐘"


def test_real_question_bank_now_produces_all_three_tiers(session: Session) -> None:
    """題庫擴充之後，真實資料排出來要同時出現三個優先度層級。

    這一支取代了 ``test_current_question_bank_produces_only_must_items``。
    那支測試記錄的現狀是「題庫只覆蓋 8 個主題，而那 8 個剛好就是必學層，
    所以排出來全部都是必學」，並且在說明裡寫好了結案條件：第二批題庫
    （Trees／Graphs／DP／Recursion／Heaps）補進來之後就刪掉它。題庫已補，
    所以它被刪了，換成這一支反向的斷言——否則「分級會出現三層」這件事
    就只剩單元測試在保護，真實資料上沒人看著。

    為什麼是 180 分鐘：那是《全職求職時程》每日作息表裡演算法的時數
    （09:00–12:00，3 小時）。60 分鐘的計畫容量太小，連必學層都排不完，
    看不到 ``bonus``——用自己實際的時數當測試參數，測到的才是會發生的情況。
    """
    _, core_plan = _make_plan(session, days=10, daily=180)
    priorities = {i.priority for i in core_plan.items}
    assert priorities == {TIER_MUST, TIER_RECOMMENDED, TIER_BONUS}, (
        f"真實題庫只排出 {priorities}；"
        "「塞不下先砍 bonus」這條路徑會變成只有單元測試在保護"
    )


def test_culled_items_are_skipped_not_deleted(session: Session) -> None:
    """被砍掉的項目要標成 ``skipped``，不能從資料庫刪掉。

    刪掉就回答不出「為什麼這題不見了」，也看不出計畫曾經包含它。

    **用真實題庫，不手動改優先度。** 原本這裡手動把一半的項目改成 ``bonus``，
    因為那時題庫只覆蓋 8 個主題、排出來全是 ``must``，而 ``must`` 永遠不砍。
    題庫補齊之後真實資料本來就會排出 ``bonus``（見上一支測試），所以這條
    寫回路徑現在是用真的會發生的輸入在測，不是用擺好的局。
    """
    _make_plan(session, days=10, daily=180)
    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None
    before = len(plan.items)
    assert any(i.priority == TIER_BONUS for i in plan.items), (
        "前提不成立：真實題庫沒排出任何加分項目，這一支就測不到砍項目"
    )

    # 只剩一天，加分項目必然被砍掉。
    end_date = plan.start_date + _dt.timedelta(days=plan.days - 1)
    result = replan(
        repo.to_scheduled_items(plan),
        repo.completed_task_ids(plan),
        end_date,
        end_date,
        plan.daily_minutes,
    )
    assert result.culled, "加分項目應該被砍掉"
    repo.apply_replan(session, plan, result)
    session.commit()

    reloaded = repo.load_active_plan(session, USER_ID)
    assert reloaded is not None
    assert len(reloaded.items) == before, "項目不該被刪掉"
    skipped = [i for i in reloaded.items if i.status == "skipped"]
    assert skipped
    assert all(i.reason for i in skipped), "被移出計畫的項目必須留下原因"

    # ``CULL_ORDER`` 是 (bonus, recommended)：砍到只剩一天時，加分砍完還是裝不下，
    # 就會往上砍建議層。所以不能斷言「只砍加分」——真正不能破的是這兩條：
    #   1. 必學一項都不准砍
    #   2. 加分要先砍完，才准動建議層
    # 原本的斷言之所以看起來成立，是因為那時手動擺的局裡只有 must 與 bonus，
    # 建議層根本不存在。
    assert not [i for i in skipped if i.priority == TIER_MUST], "必學項目被砍掉了"
    skipped_tiers = {i.priority for i in skipped}
    if TIER_RECOMMENDED in skipped_tiers:
        surviving_bonus = [
            i
            for i in reloaded.items
            if i.priority == TIER_BONUS and i.status != "skipped"
        ]
        assert not surviving_bonus, (
            f"砍了建議層，卻還留著 {len(surviving_bonus)} 項加分項目——砍的順序錯了"
        )


def test_must_items_survive_aggressive_culling(session: Session) -> None:
    """砍到只剩一天，``must`` 項目一個都不能被砍。"""
    _make_plan(session, days=10, daily=60)
    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None
    must_before = {i.task_id for i in plan.items if i.priority == TIER_MUST}

    end_date = plan.start_date + _dt.timedelta(days=plan.days - 1)
    result = replan(
        repo.to_scheduled_items(plan),
        repo.completed_task_ids(plan),
        end_date,
        end_date,
        plan.daily_minutes,
    )
    repo.apply_replan(session, plan, result)
    session.commit()

    reloaded = repo.load_active_plan(session, USER_ID)
    assert reloaded is not None
    skipped_must = {
        i.task_id
        for i in reloaded.items
        if i.status == "skipped" and i.priority == TIER_MUST
    }
    assert not skipped_must, "必學項目被砍掉了"
    surviving = {i.task_id for i in reloaded.items if i.status != "skipped"}
    assert must_before <= surviving, "重排前的必學項目有的不見了"
    # 相對地，加分項目本來就該先被砍。
    culled_tiers = {i.priority for i in reloaded.items if i.status == "skipped"}
    if culled_tiers:
        assert TIER_MUST not in culled_tiers


def test_replan_writes_a_single_event(session: Session) -> None:
    """重排要留下一筆 ``replan`` 事件，之後才回溯得出「為什麼計畫變了」。"""
    _make_plan(session, days=10, daily=60)
    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None
    today = START + _dt.timedelta(days=4)
    end_date = plan.start_date + _dt.timedelta(days=plan.days - 1)

    result = replan(
        repo.to_scheduled_items(plan),
        repo.completed_task_ids(plan),
        today,
        end_date,
        plan.daily_minutes,
    )
    repo.apply_replan(session, plan, result)
    session.commit()

    events = session.scalars(
        select(ProgressEvent).where(
            ProgressEvent.plan_id == plan.id, ProgressEvent.event == "replan"
        )
    ).all()
    assert len(events) == 1
    assert events[0].payload["rescheduled"] == len(result.items)


def test_no_backlog_writes_nothing(session: Session) -> None:
    """沒落後時 ``apply_replan`` 必須完全不動資料庫。"""
    _make_plan(session, days=10, daily=60)
    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None
    snapshot = {
        i.task_id: (i.scheduled_date, i.order_in_day, i.status) for i in plan.items
    }

    result = replan(
        repo.to_scheduled_items(plan),
        repo.completed_task_ids(plan),
        START,  # 第一天，什麼都還沒過期
        plan.start_date + _dt.timedelta(days=plan.days - 1),
        plan.daily_minutes,
    )
    assert result.changed is False
    repo.apply_replan(session, plan, result)
    session.commit()

    reloaded = repo.load_active_plan(session, USER_ID)
    assert reloaded is not None
    assert {
        i.task_id: (i.scheduled_date, i.order_in_day, i.status) for i in reloaded.items
    } == snapshot
    assert not session.scalars(
        select(ProgressEvent).where(ProgressEvent.event == "replan")
    ).all()


# ---------------------------------------------------------------------------
# LLM 備援
# ---------------------------------------------------------------------------


def test_second_replan_does_not_resurrect_culled_items(session: Session) -> None:
    """重排兩次：第一次砍掉的項目不能在第二次又被當成待做工作。

    原本 ``to_scheduled_items`` 沒有過濾 ``skipped``，所以上次砍掉的項目
    會被餵回去佔用容量，害真正待做的項目被砍，而且日期還會被更新——
    使用者會看到某一天「有安排」但打開是空的。每重排一次就多累積一批。
    """
    _make_plan(session, days=10, daily=60)
    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None

    # 刻意把後半段壓成加分項目：真實題庫也排得出 bonus，但位置會隨題庫變動，
    # 而這一支要的是「可重現地剛好砍掉某一批」，所以自己擺局。
    ordered = sorted(plan.items, key=lambda i: (i.scheduled_date, i.order_in_day))
    for row in ordered[len(ordered) // 2 :]:
        row.priority = TIER_BONUS
    session.commit()

    end_date = START + _dt.timedelta(days=9)

    # 第一次重排：砍掉一批。
    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None
    first = replan(
        repo.to_scheduled_items(plan),
        repo.completed_task_ids(plan),
        end_date,
        end_date,
        plan.daily_minutes,
    )
    repo.apply_replan(session, plan, first)
    session.commit()

    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None
    skipped_after_first = {i.task_id for i in plan.items if i.status == "skipped"}
    assert skipped_after_first, "第一次重排應該砍掉一些項目"

    # 第二次重排：被砍的項目不該出現在輸入裡。
    fed_in = {i.task_id for i in repo.to_scheduled_items(plan)}
    assert not (fed_in & skipped_after_first), (
        "已移出計畫的項目又被當成待做工作餵回重排"
    )

    second = replan(
        repo.to_scheduled_items(plan),
        repo.completed_task_ids(plan),
        end_date,
        end_date,
        plan.daily_minutes,
    )
    repo.apply_replan(session, plan, second)
    session.commit()

    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None
    # 第二次不該再砍掉新的東西（第一次已經砍到塞得下了）。
    assert {i.task_id for i in plan.items if i.status == "skipped"} == (
        skipped_after_first
    ), "第二次重排又多砍了項目，表示死掉的項目還在佔容量"


def test_progress_does_not_jump_to_full_after_culling(session: Session) -> None:
    """砍掉項目不能讓整體進度看起來變高。

    原本 ``skipped`` 同時從分子和分母扣掉，於是「放棄一半的計畫」
    看起來像「完成度大幅提升」——砍 7 項、完成 3 項，進度條從 30% 跳到 100%。
    進度是學習追蹤工具唯一不能說謊的數字。
    """
    _make_plan(session, days=10, daily=60)
    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None

    ordered = sorted(plan.items, key=lambda i: (i.scheduled_date, i.order_in_day))
    for row in ordered[len(ordered) // 2 :]:
        row.priority = TIER_BONUS
    # 完成前兩題，製造一個非 0 也非 100 的進度。
    repo.mark_item_done(session, ordered[0], actual_minutes=10)
    repo.mark_item_done(session, ordered[1], actual_minutes=10)
    session.commit()

    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None
    before = repo.progress_summary(plan, START)["overall_ratio"]
    assert 0 < before < 1

    end_date = START + _dt.timedelta(days=9)
    result = replan(
        repo.to_scheduled_items(plan),
        repo.completed_task_ids(plan),
        end_date,
        end_date,
        plan.daily_minutes,
    )
    repo.apply_replan(session, plan, result)
    session.commit()

    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None
    after = repo.progress_summary(plan, START)["overall_ratio"]
    assert after == before, (
        f"砍掉 {len(result.culled)} 項之後，整體進度從 {before:.0%} "
        f"變成 {after:.0%}——放棄工作不等於完成工作"
    )
    assert after < 1.0


def test_cull_reason_preserves_the_original_recommendation(session: Session) -> None:
    """被砍的項目要保留原本的「為什麼推薦」，不能直接覆寫掉。"""
    _make_plan(session, days=10, daily=60)
    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None

    ordered = sorted(plan.items, key=lambda i: (i.scheduled_date, i.order_in_day))
    for row in ordered[len(ordered) // 2 :]:
        row.priority = TIER_BONUS
    session.commit()

    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None
    original = {i.task_id: i.reason for i in plan.items}

    end_date = START + _dt.timedelta(days=9)
    result = replan(
        repo.to_scheduled_items(plan),
        repo.completed_task_ids(plan),
        end_date,
        end_date,
        plan.daily_minutes,
    )
    repo.apply_replan(session, plan, result)
    session.commit()

    plan = repo.load_active_plan(session, USER_ID)
    assert plan is not None
    for row in plan.items:
        if row.status == "skipped" and original[row.task_id]:
            assert original[row.task_id] in row.reason, (
                "原本的推薦理由被覆寫掉了，回不來了"
            )


def test_explanation_falls_back_without_api_key(monkeypatch) -> None:
    """沒有 API key 時要回退到預存文字，而且不能拋例外。"""
    from llm import client

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert client.is_available() is False

    result = client.explain_task(
        title="Two Strings",
        topic_name="Hash Map",
        key_points=["用集合取交集"],
        static_note="預存的說明",
    )
    assert result.text == "預存的說明"
    assert result.source == "static"
    assert result.is_generated is False


def test_explanation_with_no_static_note_is_empty_not_an_error(monkeypatch) -> None:
    from llm import client

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = client.explain_task("題", "主題", [], None)
    assert result.source == "none"
    assert result.text == ""
