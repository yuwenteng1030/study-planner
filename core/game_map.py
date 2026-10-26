"""學習地圖：把知識地圖攤成「區域 → 關卡」，並數出每一關打了幾題。

一關＝一題（``build_stages``），做完就變金色；主題（``Level``）是路上的一段，
用來分區與統計題數。不鎖關。星等（``Level.stars``）只顯示、不擋路，門檻見
``STAR_ONE_EASY``（提案決策 5，2026-10-23 由 AI 定為「Easy 完成 3 題」）。

區域不另外存，直接從 ``topics.prereq`` 算：一個主題的區號是它到起點的
**最長**前置鏈長度。用最長而不是最短，是因為 ``sorting`` 同時需要
``arrays``（第一區）與 ``time_complexity``（第一區），它必須排在兩者之後；
如果取最短，有兩個前置的主題可能被畫在其中一個前置的同一區。
"""

from __future__ import annotations

import datetime as _dt

from collections.abc import Iterable
from dataclasses import dataclass, field

from core.planner import TaskInput, TopicNode

#: 一星門檻：Easy 完成幾題（題數不到就要全做完）。不用「Easy 全完成」：
#: ``trees`` 有 7 題 Easy，全做完才一星會讓星等變成刷 Easy 的獎勵，而考卷上沒有 Easy。
STAR_ONE_EASY = 3

#: 顯示順序。題庫裡沒有其他難度；出現沒見過的難度時排在最後，不會炸。
DIFFICULTIES: tuple[str, ...] = ("Easy", "Medium", "Hard")


@dataclass(frozen=True)
class Level:
    """地圖上的一關＝一個主題。"""

    topic_id: str
    name: str
    zone: int
    prereq: tuple[str, ...]
    #: 難度 → 題數。沒有題目的難度不放進來。
    total: dict[str, int] = field(default_factory=dict)
    #: 難度 → 已完成題數。
    done: dict[str, int] = field(default_factory=dict)

    @property
    def total_count(self) -> int:
        return sum(self.total.values())

    @property
    def done_count(self) -> int:
        return sum(self.done.values())

    @property
    def stars(self) -> int:
        """0–3 星，刻意對應考卷（兩題 Medium＋一題 Hard），不是對應題數。

        - 一星：Easy 完成 ``STAR_ONE_EASY`` 題（沒有 Easy 的主題：Medium 完成 1 題）
        - 二星：Medium 完成過半（沒有 Medium 的主題：直接跳過這一級）
        - 三星：Hard 全完成（沒有 Hard 的主題：Medium 全完成）

        每一級都要先拿到前一級。觀念關永遠 0 星。
        """
        if self.is_concept_only:
            return 0
        easy, medium, hard = (self.total.get(d, 0) for d in DIFFICULTIES)
        d_easy, d_medium, d_hard = (self.done.get(d, 0) for d in DIFFICULTIES)

        one = d_easy >= min(STAR_ONE_EASY, easy) if easy else d_medium >= 1 or d_hard >= 1
        if not one:
            return 0
        two = d_medium * 2 >= medium if medium else True
        if not two:
            return 1
        three = d_hard >= hard if hard else d_medium >= medium
        return 3 if three else 2

    @property
    def is_concept_only(self) -> bool:
        """沒有題目的觀念關（目前只有 ``time_complexity``）。"""
        return self.total_count == 0


def zones(topics: Iterable[TopicNode]) -> dict[str, int]:
    """主題 id → 區號（到起點的最長前置鏈長度，起點為 0）。

    指向不存在主題的前置會被忽略，與排程器一致；有循環時拋 ``ValueError``，
    因為那代表 ``topics.yaml`` 寫壞了，畫出一張地圖只會掩蓋問題。
    """
    by_id = {t.id: t for t in topics}
    memo: dict[str, int] = {}
    visiting: set[str] = set()

    def depth(topic_id: str) -> int:
        if topic_id in memo:
            return memo[topic_id]
        if topic_id in visiting:
            raise ValueError(f"topics 的前置關係有循環，經過 {topic_id!r}")
        visiting.add(topic_id)
        parents = [p for p in by_id[topic_id].prereq if p in by_id]
        memo[topic_id] = 1 + max(map(depth, parents)) if parents else 0
        visiting.discard(topic_id)
        return memo[topic_id]

    return {topic_id: depth(topic_id) for topic_id in by_id}


def build_map(
    topics: Iterable[TopicNode],
    tasks: Iterable[TaskInput],
    done_task_ids: set[int],
) -> list[Level]:
    """組出整張地圖，依（區號, base_weight 由高到低, id）排好。

    同一區的主題彼此**不可能**有前置關係（有的話後者的區號會更大），
    所以區內順序只是顯示問題。不用輸入的順序：資料庫讀出來是照 id 的
    字母序，那對使用者沒有意義；照重要性排，最該先打的關卡在最前面。
    """
    topics = list(topics)
    zone_of = zones(topics)

    total: dict[str, dict[str, int]] = {t.id: {} for t in topics}
    done: dict[str, dict[str, int]] = {t.id: {} for t in topics}
    for task in tasks:
        if task.topic_id not in total:
            continue
        bucket = total[task.topic_id]
        bucket[task.difficulty] = bucket.get(task.difficulty, 0) + 1
        if task.id in done_task_ids:
            bucket = done[task.topic_id]
            bucket[task.difficulty] = bucket.get(task.difficulty, 0) + 1

    weight = {t.id: t.base_weight for t in topics}
    levels = [
        Level(
            topic_id=t.id,
            name=t.name,
            zone=zone_of[t.id],
            prereq=t.prereq,
            total=total[t.id],
            done=done[t.id],
        )
        for t in topics
    ]
    return sorted(levels, key=lambda lv: (lv.zone, -weight[lv.topic_id], lv.topic_id))


def difficulty_order(difficulty: str) -> int:
    """給畫面排序用：Easy → Medium → Hard → 其他。"""
    try:
        return DIFFICULTIES.index(difficulty)
    except ValueError:
        return len(DIFFICULTIES)


# ---------------------------------------------------------------------------
# 一關一題
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stage:
    """路線上的一關＝一題。主題（``Level``）退成「這一關屬於哪一段路」。"""

    task_id: int
    title: str
    difficulty: str
    topic_id: str
    topic_name: str
    zone: int
    done: bool


def build_stages(levels: list[Level], tasks: Iterable[TaskInput], done_task_ids: set[int]) -> list[Stage]:
    """把每一題攤成一關，照路線順序排好。

    主題的順序沿用 ``build_map``（區號、重要性）；同一主題裡 Easy → Medium → Hard，
    同難度短的先、再照題目 id——**跟排程器（``core.planner``）同一個規則**。
    原本地圖少比了預估分鐘，同主題裡兩邊的順序對不上，小熊會從第 1 關跳到第 3 關、
    再回頭做第 2 關（2026-10-24 使用者回報）。觀念關沒有題目，所以不在路線上。

    這裡不鎖關：後面的關卡照樣能點。「下一關」只是小熊站的位置（``next_stage``）。
    """
    by_topic: dict[str, list[TaskInput]] = {}
    for task in tasks:
        by_topic.setdefault(task.topic_id, []).append(task)

    stages: list[Stage] = []
    for level in levels:
        members = sorted(
            by_topic.get(level.topic_id, []),
            key=lambda t: (difficulty_order(t.difficulty), t.est_minutes, t.id),
        )
        stages.extend(
            Stage(
                task_id=t.id,
                title=t.title,
                difficulty=t.difficulty,
                topic_id=level.topic_id,
                topic_name=level.name,
                zone=level.zone,
                done=t.id in done_task_ids,
            )
            for t in members
        )
    return stages


def next_stage(stages: list[Stage]) -> Stage | None:
    """路線上第一個還沒做的關卡；全部做完就是最後一關（小熊站在終點）。"""
    for stage in stages:
        if not stage.done:
            return stage
    return stages[-1] if stages else None


@dataclass(frozen=True)
class PlanMarks:
    """進行中計畫在地圖上的標記。沒有計畫時兩個都是空的。"""

    #: 今天排的題目，照今天的順序（含已做完的，地圖上要看得出今天排了哪幾關）。
    today: tuple[int, ...] = ()
    #: 排在今天以前、還沒做的題目。
    overdue: frozenset[int] = frozenset()


def bear_stage(stages: list[Stage], marks: PlanMarks) -> Stage | None:
    """下一關：今天排的題目裡，**路線上最前面**那個還沒做的；今天的都做完（或沒計畫）
    就回到路線上第一個沒做的。地圖上方的「下一關」面板與破關後的小卡用它。

    名字沿用舊的：以前小熊站在這裡，2026-10-26 起小熊改站在最後過關的那一關
    （``last_cleared_stage``），一關都還沒過時才站在這裡。

    照路線順序、不照今天的排程順序：排程把同一主題的題目排在一起，地圖照「暖身 → 主力 →
    進階」編號，兩邊不一樣時小熊會在編號上跳來跳去（做完第 1 關跳到第 3 關）。今日任務頁
    也照同一個路線順序列（``route_positions``），兩頁說的「下一題」才會是同一題。

    不先看過期的：落後要靠「今日任務」的重排處理，小熊跑回三天前會讓今天的任務看起來不見了。
    """
    today = set(marks.today)
    for stage in stages:
        if stage.task_id in today and not stage.done:
            return stage
    return next_stage(stages)


def last_cleared_stage(stages: list[Stage], done_times: dict[int, _dt.datetime]) -> Stage | None:
    """最後過關的那一關：路線上已完成的關卡裡，完成時間最晚的那個。小熊平常站這裡。

    同一個時間（理論上不會，但匯入的舊資料可能有）取路線上比較後面的。
    已完成但查不到時間的關卡不算；一關都沒過就回 ``None``，由呼叫端決定站哪
    （地圖頁讓小熊站在下一關，也就是起點）。
    """
    best: tuple[_dt.datetime, int] | None = None
    winner: Stage | None = None
    for i, stage in enumerate(stages):
        when = done_times.get(stage.task_id)
        if not stage.done or when is None:
            continue
        if best is None or (when, i) > best:
            best, winner = (when, i), stage
    return winner


def route_positions(
    topics: list[TopicNode], tasks: list[TaskInput], focus_stages: dict[int, int], *, focus_only: bool
) -> dict[int, int]:
    """題目 id → 在地圖路線上的位置（0 起算）。今日任務頁用它把今天的題目照地圖的順序列。

    ``focus_only`` 時用重點路線的編號（計畫只排重點題組時，地圖預設也是這個版本）。
    """
    stages = build_stages(build_map(topics, tasks, set()), tasks, set())
    if focus_only:
        stages = focus_route(stages, focus_stages)
    return {s.task_id: i for i, s in enumerate(stages)}


#: 地圖元件會送回來的事件種類。
MAP_ACTIONS: frozenset[str] = frozenset(
    {"pick", "close", "done", "undo", "start", "cancel", "pause", "resume"}
)


def parse_map_action(action: object, known_ids: Iterable[int]) -> tuple[str, int] | None:
    """檢查地圖元件從瀏覽器送回來的事件；不合格就回 ``None``，什麼都不做。

    內容來自瀏覽器，任何人都能在開發者工具裡送任意值，所以：

    - ``task`` 必須是整數、而且是地圖上畫出來的題目——擋掉超大整數
      （SQLite 會拋 ``OverflowError``）、清單或字典（``in`` 會拋 ``TypeError``）、
      以及不在地圖上的 id。
    - ``True`` 在 Python 裡等於 1，所以 bool 要另外擋。
    """
    if not isinstance(action, dict):
        return None
    kind, task = action.get("type"), action.get("task")
    if kind not in MAP_ACTIONS:
        return None
    if not isinstance(task, int) or isinstance(task, bool) or task not in set(known_ids):
        return None
    return kind, task


# ---------------------------------------------------------------------------
# 重點路線與經驗值（遊戲化的外觀，2026-10-23）
# ---------------------------------------------------------------------------

#: 重點路線三段的名字（區號 0、1、2）。
FOCUS_ZONE_NAMES: dict[int, str] = {0: "第一段・暖身", 1: "第二段・Medium 主力", 2: "第三段・進階"}


def focus_route(stages: list[Stage], focus_stages: dict[int, int]) -> list[Stage]:
    """只留重點題組，依階段（暖身 → Medium 主力 → 進階）重新分區。

    同一段裡沿用原本路線的順序（主題的前置關係、Easy → Hard），所以每一段仍然是
    「先基礎後進階」。區號＝階段減一，畫成三條色帶。
    """
    from dataclasses import replace

    picked = [
        (focus_stages[s.task_id], i, s) for i, s in enumerate(stages) if s.task_id in focus_stages
    ]
    picked.sort(key=lambda t: (t[0], t[1]))
    return [replace(s, zone=stage - 1) for stage, _, s in picked]


#: 每題的經驗值：Medium 是考卷的主體，Hard 是拉開差距的那一題。
XP_BY_DIFFICULTY: dict[str, int] = {"Easy": 10, "Medium": 25, "Hard": 50}
#: 每升一級要的經驗值
XP_PER_LEVEL = 100


@dataclass(frozen=True)
class Experience:
    xp: int
    level: int
    into_level: int  # 這一級已經累積多少
    per_level: int = XP_PER_LEVEL

    @property
    def ratio(self) -> float:
        return self.into_level / self.per_level


def experience(stages: list[Stage]) -> Experience:
    """做完的題目換算成經驗值與等級。只是畫面上的回饋，不影響排程。"""
    xp = sum(XP_BY_DIFFICULTY.get(s.difficulty, 10) for s in stages if s.done)
    return Experience(xp=xp, level=xp // XP_PER_LEVEL + 1, into_level=xp % XP_PER_LEVEL)
