"""學習地圖：把知識地圖攤成「區域 → 關卡」，並數出每一關打了幾題。

這一版是**唯讀**的：只畫地圖與進度，不做星等、不做解鎖。
破關條件（幾題算一星、一星是否解鎖下一關）是參數，等實際看過畫面再定——
憑空選一個數字，之後多半要改。

區域不另外存，直接從 ``topics.prereq`` 算：一個主題的區號是它到起點的
**最長**前置鏈長度。用最長而不是最短，是因為 ``sorting`` 同時需要
``arrays``（第一區）與 ``time_complexity``（第一區），它必須排在兩者之後；
如果取最短，有兩個前置的主題可能被畫在其中一個前置的同一區。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from core.planner import TaskInput, TopicNode

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
