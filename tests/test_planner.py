"""``core/planner.py`` 的測試。

驗收標準（來自 v0.1-core 計畫 10/24）：
  1. 前置一定排在後置之前
  2. 每天總分鐘數 ≤ 上限

另外加測三件沒寫在驗收標準裡、但會實際出錯的事：
  3. 沒有題目的主題不能阻擋它的後置主題（``time_complexity`` 刻意維持零題目）
  4. 同樣的輸入要排出同樣的計畫
  5. 題庫塞不下時要有東西被淘汰，而且淘汰的理由要能說出來
"""

from __future__ import annotations

import csv
import datetime as _dt
from pathlib import Path

import pytest
import yaml

from core.planner import TaskInput, TopicNode, _effective_scores, _topic_order, build_plan
from core.scoring import TIER_MUST, TopicInput, UserContext, rank_topics

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOPICS_PATH = PROJECT_ROOT / "data" / "topics.yaml"
TASKS_PATH = PROJECT_ROOT / "data" / "tasks.csv"

START = _dt.date(2026, 10, 21)


# ---------------------------------------------------------------------------
# 真實資料
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def topics() -> list[TopicNode]:
    raw = yaml.safe_load(TOPICS_PATH.read_text(encoding="utf-8"))
    return [
        TopicNode(
            id=t["id"],
            name=t["name"],
            base_weight=int(t["base_weight"]),
            prereq=tuple(t.get("prereq") or ()),
        )
        for t in raw
    ]


@pytest.fixture(scope="module")
def tasks() -> list[TaskInput]:
    with TASKS_PATH.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [
        TaskInput(
            id=index + 1,
            title=row["title"],
            topic_id=row["topic"],
            difficulty=row["difficulty"],
            est_minutes=int(row["est_minutes"]),
            prereq_topic=row["prereq"] or None,
        )
        for index, row in enumerate(rows)
    ]


# ---------------------------------------------------------------------------
# 驗收標準
# ---------------------------------------------------------------------------


def test_prereq_comes_before_dependent(
    topics: list[TopicNode], tasks: list[TaskInput]
) -> None:
    """驗收標準 1：前置主題的題目不得排在後置主題的題目之後。

    只檢查**兩邊都有題目排進計畫**的主題對。沒排進去的主題無從比較。
    """
    plan = build_plan(topics, tasks, START, days=10, daily_minutes=60)

    first_slot: dict[str, tuple[_dt.date, int]] = {}
    last_slot: dict[str, tuple[_dt.date, int]] = {}
    for item in plan.items:
        slot = (item.scheduled_date, item.order_in_day)
        first_slot.setdefault(item.topic_id, slot)
        last_slot[item.topic_id] = slot

    for topic in topics:
        if topic.id not in first_slot:
            continue
        for prereq_id in topic.prereq:
            if prereq_id not in last_slot:
                continue  # 前置沒題目 → 透明，見 test_empty_topic_is_transparent
            assert last_slot[prereq_id] < first_slot[topic.id], (
                f"{prereq_id} 的最後一題排在 {topic.id} 的第一題之後"
            )


def test_daily_minutes_never_exceeded(
    topics: list[TopicNode], tasks: list[TaskInput]
) -> None:
    """驗收標準 2：每一天的總分鐘數都不能超過上限。"""
    limit = 60
    plan = build_plan(topics, tasks, START, days=10, daily_minutes=limit)
    for day, items in plan.by_date().items():
        total = sum(i.est_minutes for i in items)
        assert total <= limit, f"{day} 排了 {total} 分鐘，超過上限 {limit}"


def test_day_count_and_range_respected(
    topics: list[TopicNode], tasks: list[TaskInput]
) -> None:
    """排出來的日期必須落在 [start, start+days) 之內。"""
    days = 10
    plan = build_plan(topics, tasks, START, days=days, daily_minutes=60)
    dates = list(plan.by_date())
    assert dates, "十天六十分鐘應該排得出東西"
    assert min(dates) == START
    assert max(dates) <= START + _dt.timedelta(days=days - 1)


def test_order_in_day_is_sequential(
    topics: list[TopicNode], tasks: list[TaskInput]
) -> None:
    """每天的 order_in_day 必須是 0,1,2... 不跳號，畫面才排得出順序。"""
    plan = build_plan(topics, tasks, START, days=10, daily_minutes=60)
    for day, items in plan.by_date().items():
        assert [i.order_in_day for i in items] == list(range(len(items))), (
            f"{day} 的 order_in_day 不連續"
        )


# ---------------------------------------------------------------------------
# 前置透明化：題庫只覆蓋 8/21 個主題
# ---------------------------------------------------------------------------


def test_empty_topic_is_transparent() -> None:
    """沒有題目的主題不能阻擋它的後置主題。

    這是實際會踩到的狀況：``graphs`` 的前置是 ``trees``，而 ``trees``
    目前一題都沒有（第二批待補）。如果前置要求「必須做過」，
    ``graphs`` 就永遠排不出來，而且畫面上不會有任何錯誤訊息。
    """
    topics = [
        TopicNode(id="base", name="基礎", base_weight=10),
        TopicNode(id="trees", name="樹", base_weight=6, prereq=("base",)),
        TopicNode(id="graphs", name="圖", base_weight=6, prereq=("trees",)),
    ]
    tasks = [
        TaskInput(1, "基礎題", "base", "Easy", 10),
        # trees 刻意沒有任何題目
        TaskInput(2, "圖論題", "graphs", "Medium", 20),
    ]
    plan = build_plan(topics, tasks, START, days=5, daily_minutes=60)
    scheduled = {i.task_id for i in plan.items}
    assert scheduled == {1, 2}, "trees 沒題目，不該把 graphs 一起擋掉"


def test_cross_topic_prereq_is_respected() -> None:
    """題目層級的跨主題前置：指向的主題要排在前面才做這題。"""
    topics = [
        TopicNode(id="arrays", name="陣列", base_weight=9),
        TopicNode(id="hashmaps", name="雜湊", base_weight=10, prereq=("arrays",)),
    ]
    tasks = [
        TaskInput(1, "陣列題", "arrays", "Easy", 10),
        TaskInput(2, "雜湊題（需要陣列）", "hashmaps", "Easy", 10, prereq_topic="arrays"),
    ]
    plan = build_plan(topics, tasks, START, days=5, daily_minutes=60)
    order = [i.task_id for i in plan.items]
    assert order.index(1) < order.index(2)


def test_cross_topic_prereq_pointing_forward_is_dropped() -> None:
    """跨主題前置指向排在後面的主題時，該題要被淘汰並記下理由。

    注意 ``late`` 必須真的有題目，否則它會被當成零成本節點優先排掉，
    順序矛盾就不存在了——那也是正確行為（見
    ``test_empty_topic_is_transparent``），只是測不到這裡要測的東西。
    """
    topics = [
        TopicNode(id="early", name="先", base_weight=10),
        TopicNode(id="late", name="後", base_weight=1),
    ]
    tasks = [
        # early 主題的題目卻依賴分數更低、因此排在後面的 late 主題
        TaskInput(1, "順序矛盾", "early", "Easy", 10, prereq_topic="late"),
        TaskInput(2, "後置主題的題目", "late", "Easy", 10),
    ]
    plan = build_plan(topics, tasks, START, days=5, daily_minutes=60)
    assert [i.task_id for i in plan.items] == [2]
    assert [tid for tid, _ in plan.dropped] == [1]
    assert "前置" in plan.dropped[0][1]


# ---------------------------------------------------------------------------
# 容量與淘汰
# ---------------------------------------------------------------------------


def test_over_capacity_drops_tasks(
    topics: list[TopicNode], tasks: list[TaskInput]
) -> None:
    """題庫時數大於計畫容量時，一定要有題目被淘汰。

    如果這個測試紅了，表示題庫塞得進計畫，評分與分級就完全不影響結果——
    核心邏輯會在空轉而測試還是綠的。
    """
    days, daily = 10, 60
    total_minutes = sum(t.est_minutes for t in tasks)
    assert total_minutes > days * daily, (
        f"題庫只有 {total_minutes} 分鐘，塞得進 {days * daily} 分鐘的計畫，"
        "這個測試就失去意義了——請加題或縮短計畫"
    )

    plan = build_plan(topics, tasks, START, days=days, daily_minutes=daily)
    assert plan.dropped, "容量不足卻沒有任何題目被淘汰"
    assert plan.total_minutes() <= days * daily
    assert len(plan.items) + len(plan.dropped) == len(tasks), (
        "每一題都必須有歸屬：排進計畫或列入淘汰，不能憑空消失"
    )
    for _task_id, reason in plan.dropped:
        assert reason, "被淘汰的題目必須有理由"


def test_high_score_topics_win_the_limited_capacity(
    topics: list[TopicNode], tasks: list[TaskInput]
) -> None:
    """容量有限時，分數高的主題要拿到位子。

    不能斷言「必學層全部排進去」——目前題庫只覆蓋 8 個主題，而那 8 個**剛好**
    就是必學層，所以「必學層排進去幾題 vs 丟掉幾題」兩邊都是同一批題目，
    斷言不出任何東西（第一版就是這樣寫的，結果 22 比 22）。

    改成比較「排進計畫的題目」與「被淘汰的題目」各自的平均主題分數：
    前者一定要高於後者，否則排程根本沒照分數走。
    """
    plan = build_plan(topics, tasks, START, days=10, daily_minutes=60)
    scheduled_ids = {i.task_id for i in plan.items}
    scores = plan.scored_topics

    scheduled = [t for t in tasks if t.id in scheduled_ids]
    dropped = [t for t in tasks if t.id not in scheduled_ids]
    assert scheduled and dropped, "這個測試需要「有排進去也有被丟掉」的情況"

    avg_scheduled = sum(scores[t.topic_id].score for t in scheduled) / len(scheduled)
    avg_dropped = sum(scores[t.topic_id].score for t in dropped) / len(dropped)
    assert avg_scheduled > avg_dropped, (
        f"排進計畫的平均主題分 {avg_scheduled:.2f} 沒有高於"
        f"被淘汰的 {avg_dropped:.2f}，排程沒照分數走"
    )


def test_zero_cost_topic_does_not_delay_its_dependents(
    topics: list[TopicNode], tasks: list[TaskInput]
) -> None:
    """沒有題目的主題不能把它的高分後置主題往後推。

    這是實際踩過的 bug，不是假想案例。``sorting``（8 分）的前置
    ``time_complexity`` 一題都沒有、分數 7；原本讓它按分數跟別人競爭，
    它就輸給同樣 7 分的 ``stacks_queues``，連帶讓 ``sorting`` 與
    ``greedy``（都是 8 分）整個排不進計畫——7 分的排到了，8 分的沒排到。

    斷言的對象是**主題順序**，不是排進去的題數。

    原本這一支斷言的是「8 分主題不能掛零」。題庫從 8 個主題擴到 20 個之後
    那個寫法失效了，而且不是因為 bug 回來了：21 個主題、125 題擠 600 分鐘，
    最後一定會剩下一小塊容量（實測 45 分鐘），而填得進去的只有最便宜的那一題
    ——``linked_lists`` 的 10 分鐘題。於是「掛零的主題一定比排進去的都重要」
    這句話就不可能成立，但它不成立的原因是裝箱的零頭，不是前置擋路。

    順序才是 ``_topic_order`` 真正控制的東西，而且不受容量影響：
    ``sorting`` 與 ``greedy`` 之前不准出現任何「有題目、而且更不重要」的主題。
    """
    by_weight = {t.id: t.base_weight for t in topics}
    has_tasks = {t.topic_id for t in tasks}
    scores = {
        r.topic_id: r.score
        for r in rank_topics(
            [
                TopicInput(id=t.id, name=t.name, base_weight=t.base_weight)
                for t in topics
            ],
            None,
            None,
        )
    }
    order = _topic_order(topics, scores, has_tasks)

    for victim in ("sorting", "greedy"):
        ahead = order[: order.index(victim)]
        queue_jumpers = [
            tid
            for tid in ahead
            if tid in has_tasks and by_weight[tid] < by_weight[victim]
        ]
        assert not queue_jumpers, (
            f"{victim}（base_weight {by_weight[victim]}）被 {queue_jumpers} 插隊——"
            "零成本的前置節點又開始擋路了"
        )


def test_time_complexity_stays_task_free(tasks: list[TaskInput]) -> None:
    """``time_complexity`` 不能有題目——這條限制是上一支測試的前提。

    ``_topic_order`` 把「沒有題目的主題」當透明節點優先放行，所以
    ``time_complexity``（7 分）才不會卡住它的後置 ``sorting``／``greedy``（都 8 分）。
    一旦給它題目，它就變成要按分數競爭的普通節點，於是輸給同樣 7 分的
    ``stacks_queues``，``sorting`` 從第 5 位掉到第 7 位、``greedy`` 第 6 掉到第 8——
    600 分鐘的計畫就排不到它們了。**那個 bug 不是被修掉，是被「它沒有題目」擋著。**

    這不是假設：擴充題庫時真的把 ``Time Complexity: Primality`` 與 ``Is Fibo``
    放進 ``time_complexity``，這一組測試立刻紅。兩題後來改掛
    ``math_number_theory``（質數判斷與費氏數本來就是數論題），
    ``time_complexity`` 維持成只有 ``key_points`` 的觀念節點。

    要改掉這條限制，得先改 ``_topic_order`` 的排序規則
    （例如「高分主題的前置跟著它一起提前」），那是評分與排程邏輯的決策，
    不是資料檔的決策。所以這裡擋在資料檔這一層，讓它大聲壞掉。
    """
    assert not [t for t in tasks if t.topic_id == "time_complexity"], (
        "time_complexity 被塞了題目。請看這支測試的說明："
        "它會讓 sorting 與 greedy 排不進計畫。"
    )


def test_real_data_has_no_contradictory_prereqs(
    topics: list[TopicNode], tasks: list[TaskInput]
) -> None:
    """``tasks.csv`` 不能有「前置主題排在自己之後」的題目。

    這是真的發生過的資料錯誤：``Sparse Arrays`` 放在 ``arrays`` 主題，
    卻標了 ``prereq: hashmaps``，而 ``hashmaps`` 的前置就是 ``arrays``——
    等於要求一個排在自己後面的主題。排程會正確地把它丟掉，但如果沒有這個
    測試，就只是靜悄悄少一題，不會有人發現。

    題庫擴充（第二批待補）時最容易再犯，所以把它釘成測試。
    """
    plan = build_plan(topics, tasks, START, days=10, daily_minutes=60)
    contradictions = [
        (task_id, reason)
        for task_id, reason in plan.dropped
        if "排在本主題之後" in reason
    ]
    titles = {t.id: t.title for t in tasks}
    assert not contradictions, "資料矛盾：" + "；".join(
        f"{titles[tid]}（{reason}）" for tid, reason in contradictions
    )


def test_capacity_is_not_wasted(
    topics: list[TopicNode], tasks: list[TaskInput]
) -> None:
    """有題目被淘汰時，容量利用率不能太低。

    第一版排程只會往前推進：某一天塞不下就換天，剩下的分鐘永遠補不回來。
    結果是 600 分鐘只用了 465，卻同時有 24 題因為「天數已用完」被丟掉——
    一邊浪費容量一邊丟工作。改成逐日裝填後利用率回到 9 成以上。

    門檻設 85%：留一點空間給前置關係造成的合理空檔
    （第一天只能排 python_basic，確實填不滿），但擋得住「換天就放棄」這種退化。
    """
    days, daily = 10, 60
    plan = build_plan(topics, tasks, START, days=days, daily_minutes=daily)
    if not plan.dropped:
        pytest.skip("題庫塞得進計畫，沒有容量壓力")

    utilization = plan.total_minutes() / (days * daily)
    assert utilization >= 0.85, (
        f"容量利用率只有 {utilization:.0%}，卻還有 {len(plan.dropped)} 題被淘汰——"
        "排程在浪費時間"
    )


def test_task_longer_than_daily_limit_is_dropped() -> None:
    """單題時間超過每日上限時要淘汰，不能無窮換天找位子。"""
    topics = [TopicNode(id="t", name="主題", base_weight=5)]
    tasks = [
        TaskInput(1, "超長題", "t", "Hard", 90),
        TaskInput(2, "正常題", "t", "Easy", 10),
    ]
    plan = build_plan(topics, tasks, START, days=5, daily_minutes=30)
    assert [i.task_id for i in plan.items] == [2]
    assert plan.dropped[0][0] == 1
    assert "超過每日上限" in plan.dropped[0][1]


def test_zero_capacity_schedules_nothing() -> None:
    """一天只排一題的極端情況也要正確，不能漏題或重複。"""
    topics = [TopicNode(id="t", name="主題", base_weight=5)]
    tasks = [TaskInput(i, f"題 {i}", "t", "Easy", 30) for i in range(1, 6)]
    plan = build_plan(topics, tasks, START, days=3, daily_minutes=30)
    assert len(plan.items) == 3
    assert len(plan.dropped) == 2
    assert len({i.scheduled_date for i in plan.items}) == 3


# ---------------------------------------------------------------------------
# 排序與可重現性
# ---------------------------------------------------------------------------


def test_easy_tasks_come_first_within_a_topic() -> None:
    """同主題內：Easy 先於 Medium 先於 Hard。"""
    topics = [TopicNode(id="t", name="主題", base_weight=5)]
    tasks = [
        TaskInput(1, "難", "t", "Hard", 10),
        TaskInput(2, "中", "t", "Medium", 10),
        TaskInput(3, "易", "t", "Easy", 10),
    ]
    plan = build_plan(topics, tasks, START, days=5, daily_minutes=60)
    assert [i.difficulty for i in plan.items] == ["Easy", "Medium", "Hard"]


def test_plan_is_deterministic(
    topics: list[TopicNode], tasks: list[TaskInput]
) -> None:
    """輸入順序改變不能改變計畫。資料庫回傳順序是不保證的。"""

    def fingerprint(plan) -> list[tuple]:
        return [
            (i.task_id, i.scheduled_date, i.order_in_day) for i in plan.items
        ]

    forward = build_plan(topics, tasks, START, days=10, daily_minutes=60)
    backward = build_plan(
        list(reversed(topics)), list(reversed(tasks)), START, days=10, daily_minutes=60
    )
    assert fingerprint(forward) == fingerprint(backward)


def test_user_level_changes_the_plan(
    topics: list[TopicNode], tasks: list[TaskInput]
) -> None:
    """自評「已熟」要真的改變計畫內容，否則評分等於沒接上排程。"""
    baseline = build_plan(topics, tasks, START, days=10, daily_minutes=60)
    familiar = build_plan(
        topics,
        tasks,
        START,
        days=10,
        daily_minutes=60,
        user=UserContext(levels={"hashmaps": "familiar", "python_basic": "familiar"}),
    )
    assert [i.task_id for i in baseline.items] != [i.task_id for i in familiar.items]


# ---------------------------------------------------------------------------
# 輸入檢查
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "daily"),
    [(0, 60), (-1, 60), (10, 0), (10, -30)],
)
def test_invalid_capacity_raises(days: int, daily: int) -> None:
    with pytest.raises(ValueError):
        build_plan([], [], START, days=days, daily_minutes=daily)


def test_prereq_cycle_raises_instead_of_silently_emptying_the_plan() -> None:
    """前置有循環時要大聲壞掉，不能靜悄悄排出空計畫。

    ``topics.prereq`` 是 JSON 欄位，沒有外鍵，所以 ``topics.yaml`` 打錯字
    是真的會發生的。循環會讓主題出現在自己的前置集合裡，於是它的題目
    永遠等自己排完——排程會回傳**零個項目**、把每題標成「天數已用完」，
    畫面則顯示「每天 N 分鐘排不進任何一題，請調高每日時間」。
    調高時間當然沒用，使用者只會一直卡在那裡。
    """
    topics = [
        TopicNode(id="a", name="A", base_weight=5, prereq=("b",)),
        TopicNode(id="b", name="B", base_weight=5, prereq=("a",)),
    ]
    tasks = [TaskInput(1, "題", "a", "Easy", 10)]
    with pytest.raises(ValueError, match="循環"):
        build_plan(topics, tasks, START, days=5, daily_minutes=60)


def test_dangling_prereq_raises() -> None:
    """前置指向不存在的主題也要立刻拋錯，而不是悄悄排到最後面。"""
    topics = [TopicNode(id="a", name="A", base_weight=5, prereq=("typo",))]
    with pytest.raises(ValueError, match="不在知識地圖裡"):
        build_plan(topics, [], START, days=5, daily_minutes=60)


def test_real_topic_graph_is_valid(topics: list[TopicNode]) -> None:
    """真實的 ``topics.yaml`` 必須通得過上面兩項檢查。"""
    build_plan(topics, [], START, days=5, daily_minutes=60)


def test_task_with_unknown_topic_is_accounted_for() -> None:
    """題目的主題不在地圖上時，要記成淘汰，不能憑空消失。

    ``Plan.dropped`` 的契約是「每一題都有歸屬：排進計畫或列入淘汰」。
    原本這種題目既不在 items 也不在 dropped。
    """
    topics = [TopicNode(id="known", name="有", base_weight=5)]
    tasks = [
        TaskInput(1, "正常", "known", "Easy", 10),
        TaskInput(2, "孤兒", "ghost", "Easy", 10),
    ]
    plan = build_plan(topics, tasks, START, days=5, daily_minutes=60)
    assert len(plan.items) + len(plan.dropped) == len(tasks)
    assert 2 in {tid for tid, _ in plan.dropped}
    assert "不在知識地圖裡" in dict(plan.dropped)[2]


def test_no_tasks_returns_empty_plan(topics: list[TopicNode]) -> None:
    plan = build_plan(topics, [], START, days=10, daily_minutes=60)
    assert plan.items == []
    assert plan.dropped == []
    assert len(plan.scored_topics) == len(topics)


# ---------------------------------------------------------------------------
# 有效分數：高分後置把前置一起拉上來（2026-10-23，AI 實作）
# ---------------------------------------------------------------------------


def test_effective_score_is_the_best_of_self_and_all_descendants() -> None:
    chain = [
        TopicNode("a", "A", 5, ()),
        TopicNode("b", "B", 3, ("a",)),
        TopicNode("c", "C", 9, ("b",)),
        TopicNode("d", "D", 7, ()),
    ]
    eff = _effective_scores(chain, {"a": 5.0, "b": 3.0, "c": 9.0, "d": 7.0})
    # c 是 a 的孫子：遞移後置也算
    assert eff == {"a": 9.0, "b": 9.0, "c": 9.0, "d": 7.0}


def test_low_prereq_of_a_high_topic_beats_a_middling_topic() -> None:
    """b（3 分）是 c（9 分）唯一的前置；d 7 分。舊規則先排 d，c 被卡在 b 後面。"""
    chain = [
        TopicNode("b", "B", 3, ()),
        TopicNode("c", "C", 9, ("b",)),
        TopicNode("d", "D", 7, ()),
    ]
    order = _topic_order(chain, {"b": 3.0, "c": 9.0, "d": 7.0}, {"b", "c", "d"})
    assert order == ["b", "c", "d"]


def test_ties_go_to_the_topic_that_is_itself_higher() -> None:
    """前置借來的分數跟某個主題本身同分時，本身高分的先。"""
    chain = [
        TopicNode("b", "B", 3, ()),
        TopicNode("c", "C", 8, ("b",)),
        TopicNode("e", "E", 8, ()),
    ]
    order = _topic_order(chain, {"b": 3.0, "c": 8.0, "e": 8.0}, {"b", "c", "e"})
    assert order == ["e", "b", "c"]


def test_familiar_basics_let_advanced_topics_into_a_short_plan(
    topics: list[TopicNode], tasks: list[TaskInput]
) -> None:
    """真實資料：基礎標成「已經熟」、目標選進階，14 天 × 120 分鐘要排得到 graphs。

    改規則前這裡是 0 題：graphs 的另一個前置 stacks_queues（7 分，不是目標）
    一直輸給其他候選、排得太晚，graphs 就進不了計畫。trees 在舊規則下本來就排得到，
    所以只斷言 graphs。
    """
    familiar = {
        t: "familiar"
        for t in ("python_basic", "arrays", "strings", "hashmaps", "sorting", "greedy", "search")
    }
    advanced = ("recursion", "trees", "graphs", "heaps", "dp_1d", "dp_2d", "tries")
    plan = build_plan(
        topics, tasks, START, days=14, daily_minutes=120,
        user=UserContext(levels=familiar, goal_topics=frozenset(advanced)),
    )
    by_id = {t.id: t for t in tasks}
    picked = {by_id[i.task_id].topic_id for i in plan.items}
    assert "graphs" in picked
