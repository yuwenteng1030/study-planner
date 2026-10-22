"""把評分結果排成「每天做哪幾題」。

和 ``core/scoring.py`` 一樣是純函式：輸入 dataclass，輸出 dataclass，
不碰資料庫。排程邏輯本身就是這個專案最接近本職工作的部分——
概念上跟派工一樣：先排除前置沒完成的，再按權重排序，最後塞進每天的產能。

排程規則（優先序由上到下）：

1. **前置先排**：某主題的題目不得排在它的前置主題的題目之前。
2. **高分先排**：同一層（前置都已滿足）之內按 ``scoring`` 的分數由高到低。
3. **簡單先排**：同主題之內按難度 Easy → Medium → Hard，再按預估時間由短到長。
4. **塞不下就丟**：每天總分鐘數不得超過 ``daily_minutes``；天數用完就停，
   排不進去的題目不會出現在計畫裡。

第 4 點是重點：題庫的總時數**必須**大於計畫容量，否則 planner 只是把所有題目
原封不動排進去，評分與分級完全不影響結果，核心邏輯就空轉了。
目前 44 題共 1215 分鐘，而 10 天 × 60 分鐘 = 600 分鐘，必須淘汰約一半。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from core.scoring import JDStats, ScoredTopic, TopicInput, UserContext, rank_topics

#: 難度的排序權重。同主題之內先做簡單的，建立手感再碰難題。
DIFFICULTY_ORDER: dict[str, int] = {"Easy": 0, "Medium": 1, "Hard": 2}
DEFAULT_DIFFICULTY_RANK = 1


@dataclass(frozen=True)
class TaskInput:
    """排程需要的題目資料。"""

    id: int
    title: str
    topic_id: str
    difficulty: str
    est_minutes: int

    #: 跨主題前置：這一題額外需要哪個**主題**的基礎。
    #: 同主題內的前後關係不放這裡，那由 topics.prereq 管。
    prereq_topic: str | None = None


@dataclass(frozen=True)
class TopicNode:
    """知識地圖上的一個節點。"""

    id: str
    name: str
    base_weight: int
    prereq: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScheduledItem:
    """排好的一項。"""

    task_id: int
    title: str
    topic_id: str
    scheduled_date: _dt.date
    order_in_day: int
    priority: str
    est_minutes: int
    difficulty: str
    topic_score: float


@dataclass
class Plan:
    """排程的結果。"""

    items: list[ScheduledItem] = field(default_factory=list)

    #: 沒排進去的題目 id，以及原因。面試時要能回答「為什麼這題沒被選」。
    dropped: list[tuple[int, str]] = field(default_factory=list)

    #: 主題 id -> 評分結果，供畫面顯示「為什麼推薦」。
    scored_topics: dict[str, ScoredTopic] = field(default_factory=dict)

    def by_date(self) -> dict[_dt.date, list[ScheduledItem]]:
        """按日期分組，日期由早到晚。"""
        grouped: dict[_dt.date, list[ScheduledItem]] = {}
        for item in self.items:
            grouped.setdefault(item.scheduled_date, []).append(item)
        return {d: grouped[d] for d in sorted(grouped)}

    def total_minutes(self) -> int:
        return sum(item.est_minutes for item in self.items)


def _topic_order(
    topics: list[TopicNode], scores: dict[str, float], has_tasks: set[str]
) -> list[str]:
    """決定主題的處理順序：滿足前置的前提下，分數高的先來。

    用 Kahn 演算法做拓撲排序，每一輪從「前置都已排完」的候選中挑分數最高的。

    **沒有題目的主題視為透明**：它會被排序走過去，但不阻擋後面的主題。

    這一點是刻意的設計決策，不是實作細節。知識地圖有 21 個主題，其中
    ``time_complexity`` 刻意維持零題目（它是觀念節點，而且它一旦有題目就會
    卡住後置的 ``sorting``／``greedy``，見
    ``tests/test_planner.py::test_time_complexity_stays_task_free``）。
    如果要求「前置主題必須有題目、而且解過」，那麼 ``sorting``
    （前置是 ``time_complexity``）就永遠排不出來——使用者會看到一張
    莫名其妙缺了一半的計畫，而且沒有任何錯誤訊息。
    （題庫原本只覆蓋 8 / 21 個主題，所以這條規則當時影響的是
    ``trees``／``graphs``／``dp`` 一整片；2026-10-22 題庫補到 122 題、
    覆蓋 20 個主題之後，它實際擋的只剩 ``time_complexity`` 這一個節點，
    但規則本身沒有變。）

    換句話說：前置關係約束的是**順序**，不是**資格**。

    而且「沒有題目的主題」要**優先排掉**，不是按分數跟其他主題一起競爭。
    理由是它不佔任何時間，所以早排晚排對容量沒差，但晚排會壓住它的後置主題。
    實際踩過的坑：``sorting``（分數 8）的前置是 ``time_complexity``（分數 7，
    零題目）。原本讓它們按分數競爭，``time_complexity`` 就輸給同樣 7 分的
    ``stacks_queues``，連帶把 ``sorting`` 8 分、``greedy`` 8 分一路卡到天數用完——
    結果 7 分的主題排進計畫，8 分的主題一題都沒排到。零成本的節點不該擋路。
    """
    prereq_map = {t.id: set(t.prereq) for t in topics}
    remaining = dict(prereq_map)
    ordered: list[str] = []

    def release(chosen: str) -> None:
        ordered.append(chosen)
        del remaining[chosen]
        for deps in remaining.values():
            deps.discard(chosen)

    while remaining:
        ready = [tid for tid, deps in remaining.items() if not deps]
        if not ready:
            # 有循環。validate_graph.py 應該要先攔下來，但這裡也不能無窮迴圈：
            # 挑一個分數最高的強制放行，讓呼叫端從結果看得出異常。
            ready = sorted(remaining, key=lambda t: (-scores.get(t, 0.0), t))[:1]

        # 零成本的節點先走：不佔時間，卻可能解鎖高分主題。
        free = sorted(t for t in ready if t not in has_tasks)
        if free:
            for topic_id in free:
                release(topic_id)
            continue

        # 其餘按分數降冪，分數相同時用 id 定序讓結果可重現。
        ready.sort(key=lambda t: (-scores.get(t, 0.0), t))
        release(ready[0])

    return ordered


def _prereq_closure(topics: list[TopicNode]) -> dict[str, frozenset[str]]:
    """算出每個主題的**遞移**前置集合。

    只看直接前置會漏判。例子：``dp_1d`` 的前置是 ``recursion``，
    ``recursion`` 的前置是 ``python_basic``。如果 ``recursion`` 沒有題目，
    只檢查直接前置就會讓 ``dp_1d`` 在 ``python_basic`` 還沒排完時就排進去。

    直接前置若指向不存在的主題，或前置圖有循環，一律在這裡就拋
    ``ValueError``。

    為什麼要拋而不是容忍：``topics.prereq`` 是 JSON 欄位，**沒有外鍵**
    （``tasks.prereq`` 才有），所以 ``topics.yaml`` 打錯字是真的會發生的。
    循環會讓主題在自己的前置集合裡，於是它的題目永遠等自己排完——
    ``build_plan`` 會回傳**零個項目**，並把每一題標成「天數已用完」，
    然後畫面顯示「每天 N 分鐘排不進任何一題，請調高每日時間」。
    調高每日時間當然沒用，使用者會一直卡在那裡。寧願早點大聲壞掉。
    """
    known = {t.id for t in topics}
    direct = {t.id: tuple(t.prereq) for t in topics}

    for topic_id, parents in direct.items():
        missing = [p for p in parents if p not in known]
        if missing:
            raise ValueError(
                f"主題 {topic_id!r} 的前置 {missing} 不在知識地圖裡——"
                "請檢查 data/topics.yaml 的拼字"
            )

    cache: dict[str, frozenset[str]] = {}

    def resolve(topic_id: str, path: tuple[str, ...]) -> frozenset[str]:
        if topic_id in cache:
            return cache[topic_id]
        if topic_id in path:
            cycle = " → ".join(path[path.index(topic_id) :] + (topic_id,))
            raise ValueError(f"前置關係有循環：{cycle}")
        result: set[str] = set()
        for parent in direct.get(topic_id, ()):
            result.add(parent)
            result |= resolve(parent, path + (topic_id,))
        closure = frozenset(result)
        cache[topic_id] = closure
        return closure

    return {t.id: resolve(t.id, ()) for t in topics}


def build_plan(
    topics: list[TopicNode],
    tasks: list[TaskInput],
    start_date: _dt.date,
    days: int,
    daily_minutes: int,
    user: UserContext | None = None,
    jd: JDStats | None = None,
) -> Plan:
    """排出一份計畫。

    Args:
        topics: 知識地圖。
        tasks: 題庫。
        start_date: 第一天。
        days: 總天數。
        daily_minutes: 每天可投入的分鐘數。
        user: 使用者程度與目標；省略即冷啟動。
        jd: JD 統計；省略即沒有任何 JD。

    Returns:
        ``Plan``，含排好的項目與被淘汰的題目。
    """
    if days <= 0:
        raise ValueError("days 必須大於 0")
    if daily_minutes <= 0:
        raise ValueError("daily_minutes 必須大於 0")

    ranked = rank_topics(
        [TopicInput(id=t.id, name=t.name, base_weight=t.base_weight) for t in topics],
        user,
        jd,
    )
    scored = {r.topic_id: r for r in ranked}
    scores = {r.topic_id: r.score for r in ranked}

    tasks_by_topic: dict[str, list[TaskInput]] = {}
    for task in tasks:
        tasks_by_topic.setdefault(task.topic_id, []).append(task)

    # 同主題內：簡單的先、短的先，最後用 id 定序讓結果可重現。
    for topic_tasks in tasks_by_topic.values():
        topic_tasks.sort(
            key=lambda t: (
                DIFFICULTY_ORDER.get(t.difficulty, DEFAULT_DIFFICULTY_RANK),
                t.est_minutes,
                t.id,
            )
        )

    order = _topic_order(topics, scores, set(tasks_by_topic))
    position = {tid: index for index, tid in enumerate(order)}
    blockers = _prereq_closure(topics)

    plan = Plan(scored_topics=scored)

    # 題目的主題不在知識地圖裡 → 不會被走訪到，必須明確記為淘汰。
    # 否則它既不在 items 也不在 dropped，憑空消失，違反「每一題都有歸屬」。
    # （``tasks.topic_id`` 有外鍵，所以從網頁進來碰不到；直接呼叫才會。）
    pending: list[TaskInput] = []
    for task in tasks:
        if task.topic_id not in position:
            plan.dropped.append(
                (task.id, f"主題 {task.topic_id} 不在知識地圖裡")
            )

    # 待排清單：主題順序優先，其次同主題內的難度與長度。
    for topic_id in order:
        for task in tasks_by_topic.get(topic_id, []):
            # 單題就超過一天的上限，永遠排不進去，先剔掉以免佔著待排清單。
            if task.est_minutes > daily_minutes:
                plan.dropped.append(
                    (
                        task.id,
                        f"單題 {task.est_minutes} 分鐘超過每日上限 {daily_minutes} 分鐘",
                    )
                )
                continue
            # 跨主題前置指向排在本主題之後的主題 → 順序矛盾，排不了。
            if task.prereq_topic is not None:
                required = position.get(task.prereq_topic)
                if required is not None and required > position[topic_id]:
                    plan.dropped.append(
                        (task.id, f"跨主題前置 {task.prereq_topic} 排在本主題之後")
                    )
                    continue
            pending.append(task)

    def is_unlocked(task: TaskInput) -> bool:
        """前置主題還有題目沒排 → 這一題還不能排。

        這是維持「前置先排」的唯一依據，也是為什麼可以安心地在同一天裡
        跳過排不下的題目去撿後面的題目：只要前置主題的題目都已經排完，
        後置主題的題目排在哪一天都不會違反順序。

        ``blockers`` 是**遞移**閉包而不是只看直接前置。少了遞移性會漏判：
        ``graphs`` 沒有題目時，``trees``（有題目、還沒排）就擋不住
        ``graphs`` 的後置主題了。
        """
        required = blockers.get(task.topic_id, frozenset())
        if task.prereq_topic is not None:
            required = required | {task.prereq_topic} | blockers.get(
                task.prereq_topic, frozenset()
            )
        return not any(t.topic_id in required for t in pending)

    for day_offset in range(days):
        remaining = daily_minutes
        order_in_day = 0
        # 把這一天填滿：每次從待排清單的最前面找第一個「排得下又已解鎖」的題目。
        while True:
            chosen: TaskInput | None = None
            for task in pending:
                if task.est_minutes <= remaining and is_unlocked(task):
                    chosen = task
                    break
            if chosen is None:
                break

            pending.remove(chosen)
            plan.items.append(
                ScheduledItem(
                    task_id=chosen.id,
                    title=chosen.title,
                    topic_id=chosen.topic_id,
                    scheduled_date=start_date + _dt.timedelta(days=day_offset),
                    order_in_day=order_in_day,
                    priority=scored[chosen.topic_id].priority,
                    est_minutes=chosen.est_minutes,
                    difficulty=chosen.difficulty,
                    topic_score=scores[chosen.topic_id],
                )
            )
            remaining -= chosen.est_minutes
            order_in_day += 1

        if not pending:
            break

    for task in pending:
        plan.dropped.append((task.id, "天數已用完"))

    return plan
