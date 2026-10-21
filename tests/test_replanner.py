"""``core/replanner.py`` 的測試。

計畫指定的三個案例：
  ① 落後 0 天 → 不變動
  ② 塞不下 → 先砍 bonus，must 一題都不能砍
  ③ 重排後前置仍正確
"""

from __future__ import annotations

import datetime as _dt

import pytest

from core.planner import ScheduledItem
from core.replanner import replan
from core.scoring import TIER_BONUS, TIER_MUST, TIER_RECOMMENDED

TODAY = _dt.date(2026, 10, 25)
END = _dt.date(2026, 10, 31)


def item(
    task_id: int,
    day: _dt.date,
    priority: str = TIER_MUST,
    minutes: int = 20,
    topic_id: str = "arrays",
    order_in_day: int = 0,
) -> ScheduledItem:
    return ScheduledItem(
        task_id=task_id,
        title=f"題 {task_id}",
        topic_id=topic_id,
        scheduled_date=day,
        order_in_day=order_in_day,
        priority=priority,
        est_minutes=minutes,
        difficulty="Easy",
        topic_score=9.0,
    )


# ---------------------------------------------------------------------------
# ① 落後 0 天 → 不變動
# ---------------------------------------------------------------------------


def test_no_backlog_leaves_plan_untouched() -> None:
    """沒有任何未完成項目落在今天之前時，計畫一動都不能動。

    每次打開網站都重排會讓計畫看起來不穩定，使用者就不會信任它。
    """
    items = [
        item(1, TODAY),
        item(2, TODAY + _dt.timedelta(days=1)),
        item(3, TODAY + _dt.timedelta(days=2)),
    ]
    result = replan(items, set(), TODAY, END, daily_minutes=60)
    assert result.changed is False
    assert result.culled == []
    assert [(i.task_id, i.scheduled_date) for i in result.items] == [
        (1, TODAY),
        (2, TODAY + _dt.timedelta(days=1)),
        (3, TODAY + _dt.timedelta(days=2)),
    ]


def test_completed_overdue_items_do_not_trigger_replan() -> None:
    """過期但**已完成**的項目不算落後。"""
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [item(1, yesterday), item(2, TODAY)]
    result = replan(items, {1}, TODAY, END, daily_minutes=60)
    assert result.changed is False
    assert [i.task_id for i in result.items] == [2]


def test_completed_items_are_dropped_from_output() -> None:
    """已完成的項目不該再出現在重排結果裡。"""
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [item(1, yesterday), item(2, yesterday), item(3, TODAY)]
    result = replan(items, {1}, TODAY, END, daily_minutes=60)
    assert result.changed is True
    assert 1 not in {i.task_id for i in result.items}


# ---------------------------------------------------------------------------
# ② 塞不下 → 先砍 bonus，must 不砍
# ---------------------------------------------------------------------------


def test_bonus_is_culled_before_recommended() -> None:
    """砍的順序是 bonus → recommended，must 永遠保留。"""
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [
        item(1, yesterday, TIER_MUST, 60),
        item(2, yesterday, TIER_RECOMMENDED, 60),
        item(3, yesterday, TIER_BONUS, 60),
    ]
    # 剩 1 天 × 60 分鐘 = 60 分鐘，三題共 180 分鐘，只能留一題。
    result = replan(items, set(), TODAY, TODAY, daily_minutes=60)
    kept = {i.task_id for i in result.items}
    culled = {tid for tid, _ in result.culled}
    assert kept == {1}, "只該留下 must"
    assert culled == {2, 3}
    # bonus 要先被砍。
    assert result.culled[0][0] == 3


def test_must_is_never_culled() -> None:
    """即使遠遠塞不下，must 一題都不能砍。"""
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [item(i, yesterday, TIER_MUST, 60) for i in range(1, 6)]
    result = replan(items, set(), TODAY, TODAY, daily_minutes=60)
    assert {i.task_id for i in result.items} == {1, 2, 3, 4, 5}
    assert result.culled == []


def test_overflow_is_reported_when_must_exceeds_capacity() -> None:
    """must 超量時要回報超出的分鐘數，不能默默裝沒事。

    畫面必須能說出「時間不夠，建議延長天數或調高每日時間」。
    """
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [item(i, yesterday, TIER_MUST, 60) for i in range(1, 4)]  # 180 分鐘
    result = replan(items, set(), TODAY, TODAY, daily_minutes=60)  # 容量 60
    assert result.overflow_minutes == 120


def test_daily_limit_wins_over_the_deadline() -> None:
    """must 排不完時要往後延，不能把多出來的時數硬塞進最後一天。

    這是實際發生過的 bug：原本只在「還有下一天」時才換天，於是剩一天時
    所有項目全堆到那一天——60 分鐘的一天被排了 340 分鐘。測試是綠的、
    畫面也好看，但照著做不可能完成。

    每日上限是使用者真實的可用時間（物理限制），結束日只是期望。
    衝突時必須讓期限讓步。
    """
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [item(i, yesterday, TIER_MUST, 60) for i in range(1, 6)]  # 300 分鐘
    result = replan(items, set(), TODAY, TODAY, daily_minutes=60)  # 只剩今天

    assert len(result.items) == 5, "must 不能被砍"
    for day, day_items in result.by_date().items():
        total = sum(i.est_minutes for i in day_items)
        assert total <= 60, f"{day} 排了 {total} 分鐘，超過每日上限"


def test_extra_days_are_reported() -> None:
    """超出原定期限幾天要明確回報，使用者才能決定怎麼處理。"""
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [item(i, yesterday, TIER_MUST, 60) for i in range(1, 4)]  # 需要 3 天
    result = replan(items, set(), TODAY, TODAY, daily_minutes=60)  # 只給 1 天
    assert result.extra_days == 2
    assert max(i.scheduled_date for i in result.items) == TODAY + _dt.timedelta(days=2)


def test_no_extra_days_when_it_fits() -> None:
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [item(1, yesterday, TIER_MUST, 20), item(2, yesterday, TIER_MUST, 20)]
    result = replan(items, set(), TODAY, END, daily_minutes=60)
    assert result.extra_days == 0


def test_no_overflow_when_everything_fits() -> None:
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [item(1, yesterday, TIER_MUST, 20), item(2, TODAY, TIER_MUST, 20)]
    result = replan(items, set(), TODAY, END, daily_minutes=60)
    assert result.overflow_minutes == 0
    assert result.culled == []


def test_culling_keeps_earlier_items_of_the_same_tier() -> None:
    """同一層要從後面砍起：後面的項目依賴前面的。"""
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [item(i, yesterday, TIER_BONUS, 30) for i in range(1, 5)]  # 120 分鐘
    result = replan(items, set(), TODAY, TODAY, daily_minutes=60)  # 容量 60
    assert [i.task_id for i in result.items] == [1, 2], "應該保留前兩題"
    assert sorted(tid for tid, _ in result.culled) == [3, 4]


# ---------------------------------------------------------------------------
# ③ 重排後前置仍正確
# ---------------------------------------------------------------------------


def test_relative_order_is_preserved() -> None:
    """重排不得交換任何兩項的前後關係。

    這是前置正確性的依據：原本的順序已經是合法的拓撲順序，
    只要相對順序不變，前置就自動還是對的——不必再讀一次前置圖。
    """
    yesterday = TODAY - _dt.timedelta(days=1)
    order = [1, 2, 3, 4, 5, 6]
    items = [
        item(tid, yesterday, TIER_MUST, 25, topic_id=f"t{tid}") for tid in order
    ]
    result = replan(items, set(), TODAY, END, daily_minutes=60)
    assert [i.task_id for i in result.items] == order


def test_prereq_topic_still_comes_first_after_replan() -> None:
    """具體檢查：前置主題的題目重排後仍排在後置主題之前。"""
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [
        item(1, yesterday, TIER_MUST, 40, topic_id="arrays"),
        item(2, yesterday, TIER_MUST, 40, topic_id="arrays"),
        item(3, yesterday, TIER_MUST, 40, topic_id="hashmaps"),
        item(4, yesterday, TIER_MUST, 40, topic_id="hashmaps"),
    ]
    result = replan(items, set(), TODAY, END, daily_minutes=60)
    slots = {
        i.topic_id: [] for i in result.items
    }
    for i in result.items:
        slots[i.topic_id].append((i.scheduled_date, i.order_in_day))
    assert max(slots["arrays"]) < min(slots["hashmaps"])


def test_daily_minutes_respected_after_replan() -> None:
    """重排後每天也不能超過上限。"""
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [item(i, yesterday, TIER_MUST, 25) for i in range(1, 8)]
    result = replan(items, set(), TODAY, END, daily_minutes=60)
    for day, day_items in result.by_date().items():
        total = sum(i.est_minutes for i in day_items)
        assert total <= 60, f"{day} 排了 {total} 分鐘"


def test_replan_starts_today_not_in_the_past() -> None:
    """重排後不能有任何項目留在過去的日期。"""
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [item(i, yesterday, TIER_MUST, 20) for i in range(1, 5)]
    result = replan(items, set(), TODAY, END, daily_minutes=60)
    assert all(i.scheduled_date >= TODAY for i in result.items)


def test_order_in_day_is_sequential_after_replan() -> None:
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [item(i, yesterday, TIER_MUST, 25) for i in range(1, 8)]
    result = replan(items, set(), TODAY, END, daily_minutes=60)
    for _day, day_items in result.by_date().items():
        assert [i.order_in_day for i in day_items] == list(range(len(day_items)))


# ---------------------------------------------------------------------------
# 邊界
# ---------------------------------------------------------------------------


def test_expired_plan_still_keeps_must_items() -> None:
    """期限已過時，必學項目仍然不能被砍。

    這一條原本是反的：期限已過走一條捷徑，把**所有**未完成項目回報為砍掉，
    包括必學。於是使用者放著 10 天的計畫 20 天後回來，整份計畫被清空、
    `progress_summary` 還顯示 100% 完成。

    現在期限已過只是「剩下的天數很少」的極端情形，走同一條路徑：
    必學的往後延，該砍的砍。
    """
    past = _dt.date(2026, 10, 20)
    items = [item(1, past, TIER_MUST, 30), item(2, past, TIER_BONUS, 30)]
    result = replan(items, set(), TODAY, past, daily_minutes=60)

    assert result.changed is True
    kept = {i.task_id for i in result.items}
    assert 1 in kept, "必學項目不該因為期限已過就被丟掉"
    assert all(i.scheduled_date >= TODAY for i in result.items)
    assert 1 not in {tid for tid, _ in result.culled}


def test_expired_plan_extends_for_must_items() -> None:
    """期限已過且必學項目很多時，往後延並回報，不是全部砍掉。"""
    past = _dt.date(2026, 10, 20)
    items = [item(i, past, TIER_MUST, 60) for i in range(1, 4)]
    result = replan(items, set(), TODAY, past, daily_minutes=60)

    assert len(result.items) == 3, "三個必學項目都要保留"
    assert result.culled == []
    assert result.extra_days == 2
    assert result.overflow_minutes == 120
    for _day, day_items in result.by_date().items():
        assert sum(i.est_minutes for i in day_items) <= 60


def test_everything_completed_is_a_no_op() -> None:
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [item(1, yesterday), item(2, yesterday)]
    result = replan(items, {1, 2}, TODAY, END, daily_minutes=60)
    assert result.items == []
    assert result.changed is False


def test_invalid_daily_minutes_raises() -> None:
    with pytest.raises(ValueError):
        replan([], set(), TODAY, END, daily_minutes=0)


# ---------------------------------------------------------------------------
# 程式碼審閱抓到的問題（迴歸測試）
# ---------------------------------------------------------------------------


def test_overflow_and_extra_days_never_contradict() -> None:
    """``overflow_minutes`` 與 ``extra_days`` 必須一致。

    原本砍項目看「總分鐘數 vs 理想容量」，而實際裝填是循序 first-fit：
    裝不滿理想容量時，會回報「延後 1 天，但還差 0 分鐘」這種矛盾結果。
    現在兩個數字都從同一份裝填結果推出來。

    這個案例剛好踩到：3 × 40 分鐘、每天 60 分鐘、2 天。
    理想容量 120 分鐘 = 總時數 120 分鐘，看起來剛好塞得下，
    但一天只放得下一個 40 分鐘的項目（80 > 60），所以實際需要 3 天。
    """
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [item(i, yesterday, TIER_MUST, 40) for i in range(1, 4)]
    result = replan(items, set(), TODAY, TODAY + _dt.timedelta(days=1), daily_minutes=60)

    assert result.extra_days == 1
    assert result.overflow_minutes > 0, (
        f"延後了 {result.extra_days} 天卻說只差 {result.overflow_minutes} 分鐘"
    )
    # 反向也要成立。
    if result.overflow_minutes > 0:
        assert result.extra_days > 0


def test_culling_never_removes_a_prereq_of_a_kept_item() -> None:
    """砍項目不能砍掉保留項目的前置主題。

    砍項目是按**優先度層級**挑的，而優先度來自主題分數，跟依賴深度無關。
    低分的前置主題可能落在「加分」層，它的後置主題卻在「必學」層——
    照「同層從後面砍起」的規則就會把必學項目的前置砍掉，必學項目本身留著，
    於是排出一份「要你先做後置」的計畫。

    這裡用 time_complexity（加分）→ sorting（必學）這個真實存在的關係。
    """
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [
        item(1, yesterday, TIER_BONUS, 30, topic_id="time_complexity"),
        item(2, yesterday, TIER_MUST, 30, topic_id="sorting"),
        item(3, yesterday, TIER_MUST, 30, topic_id="sorting"),
    ]
    closure = {
        "time_complexity": frozenset(),
        "sorting": frozenset({"time_complexity"}),
    }

    # 容量只有 60 分鐘，一定得砍東西。
    result = replan(
        items, set(), TODAY, TODAY, daily_minutes=60, prereq_closure=closure
    )

    kept_topics = {i.topic_id for i in result.items}
    if "sorting" in kept_topics:
        assert "time_complexity" in kept_topics, (
            "sorting 留著但它的前置 time_complexity 被砍掉了"
        )
    assert 1 not in {tid for tid, _ in result.culled}


def test_without_closure_culling_still_works() -> None:
    """不傳 ``prereq_closure`` 時照舊運作（只是不保護前置）。"""
    yesterday = TODAY - _dt.timedelta(days=1)
    items = [
        item(1, yesterday, TIER_BONUS, 60),
        item(2, yesterday, TIER_MUST, 60),
    ]
    result = replan(items, set(), TODAY, TODAY, daily_minutes=60)
    assert {i.task_id for i in result.items} == {2}
    assert {tid for tid, _ in result.culled} == {1}


def test_daily_limit_holds_in_every_scenario() -> None:
    """掃一遍各種組合，每日上限一次都不能被打破。

    每日上限是這個模組唯一的物理限制，值得用窮舉的方式守住。
    """
    yesterday = TODAY - _dt.timedelta(days=1)
    for count in (1, 3, 7, 15):
        for minutes in (10, 25, 45, 60):
            for span in (0, 1, 4):
                items = [
                    item(i, yesterday, TIER_MUST, minutes)
                    for i in range(1, count + 1)
                ]
                result = replan(
                    items,
                    set(),
                    TODAY,
                    TODAY + _dt.timedelta(days=span),
                    daily_minutes=60,
                )
                assert len(result.items) == count, "must 項目不該遺失"
                for day, day_items in result.by_date().items():
                    total = sum(i.est_minutes for i in day_items)
                    assert total <= 60, (
                        f"{count} 項 × {minutes} 分鐘、{span + 1} 天："
                        f"{day} 排了 {total} 分鐘"
                    )
