"""落後時重排計畫。

這個模組是「網站真的能用」與「只能 demo」的分界線。沒有它，使用者只要有一天
沒做完，計畫就永遠停在過去的日期，網站等於壞掉——而落後是常態，不是例外。

設計上刻意**不重新評分**。重排只做兩件事：

1. **保持原有的相對順序**。原本的順序已經是合法的拓撲順序，
   只要不交換任何兩項的前後關係，前置正確性就自動成立——
   不需要再把 ``topics.yaml`` 的前置圖讀一遍。這是整個模組最重要的性質。
2. **塞不下就砍，從最低優先度砍起**。先砍 ``bonus``，再砍 ``recommended``，
   ``must`` 一題都不砍。砍掉的項目會連同原因回報。

為什麼不重新評分：使用者的程度在一天之內不會變，重新評分只會讓計畫在使用者
眼前無理由地跳動。真正需要重新評分的是「使用者改了自評或目標」，那是另一條
路徑（重新產生計畫），不是這裡。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from core.planner import ScheduledItem
from core.scoring import TIER_BONUS, TIER_RECOMMENDED

#: 砍項目的順序：先砍加分，再砍建議。``must`` 不在這個清單裡，所以永遠不會被砍。
CULL_ORDER: tuple[str, ...] = (TIER_BONUS, TIER_RECOMMENDED)


@dataclass
class Replan:
    """重排的結果。"""

    #: 重排後的項目。日期已更新。
    items: list[ScheduledItem] = field(default_factory=list)

    #: 被砍掉的項目與原因。
    culled: list[tuple[int, str]] = field(default_factory=list)

    #: 是否真的動了。``False`` 表示原計畫照舊。
    changed: bool = False

    #: 超出原定期限的分鐘數。大於 0 表示連必學都排不完。
    #: 這時畫面上必須明講「時間不夠」，不能默默砍掉必學項目假裝沒事。
    overflow_minutes: int = 0

    #: 為了排完必學項目而超出原定結束日的天數。
    #:
    #: 為什麼是「加天數」而不是「某一天多塞一點」：每日上限是**物理限制**，
    #: 使用者一天只有那麼多時間，把 340 分鐘排進 60 分鐘的一天只是自我欺騙，
    #: 畫面上好看但照著做不可能完成。相反地，「你的計畫需要再 3 天」
    #: 是使用者可以實際處理的資訊——延長期限、調高每日時間，或接受做不完。
    extra_days: int = 0

    def by_date(self) -> dict[_dt.date, list[ScheduledItem]]:
        grouped: dict[_dt.date, list[ScheduledItem]] = {}
        for item in self.items:
            grouped.setdefault(item.scheduled_date, []).append(item)
        return {d: grouped[d] for d in sorted(grouped)}

    def total_minutes(self) -> int:
        return sum(i.est_minutes for i in self.items)


def _remaining_days(today: _dt.date, end_date: _dt.date) -> list[_dt.date]:
    """今天（含）到結束日（含）之間的日期。

    期限已過時回傳 ``[today]`` 而不是空清單。

    為什麼不回空清單：原本「期限已過」走一條獨立的捷徑，把**所有**未完成項目
    回報為砍掉，包括必學項目——那直接違反本模組最重要的保證
    （``must`` 一題都不砍），而且 ``progress_summary`` 會因此顯示 100% 完成。
    使用者放著 10 天的計畫 20 天後回來，看到的是「全部完成」。

    回傳 ``[today]`` 之後就沒有捷徑了：期限已過只是「剩下的天數很少」的極端情形，
    走同一條路徑——該砍的砍、必學的往後延、延了幾天照實回報。
    """
    if end_date < today:
        return [today]
    span = (end_date - today).days + 1
    return [today + _dt.timedelta(days=offset) for offset in range(span)]


def _pack(
    items: list[ScheduledItem], daily_minutes: int
) -> list[tuple[ScheduledItem, int, int]]:
    """依序裝填，回傳 ``(項目, 第幾天, 當天第幾項)``。

    這是唯一的裝填實作，``replan`` 的砍項目迴圈與 ``extra_days`` 都用它來算，
    所以兩者永遠一致。曾經「砍項目」用 ``len(days) × daily_minutes`` 的理想
    總量來判斷、而實際裝填是循序 first-fit，兩個模型對不起來：裝不滿理想容量時
    會回報「超出 0 分鐘，但延後 1 天」這種自相矛盾的結果。
    """
    placed: list[tuple[ScheduledItem, int, int]] = []
    day_index = 0
    used = 0
    order_in_day = 0
    for item in items:
        if used + item.est_minutes > daily_minutes and order_in_day > 0:
            day_index += 1
            used = 0
            order_in_day = 0
        placed.append((item, day_index, order_in_day))
        used += item.est_minutes
        order_in_day += 1
    return placed


def _days_needed(items: list[ScheduledItem], daily_minutes: int) -> int:
    if not items:
        return 0
    return _pack(items, daily_minutes)[-1][1] + 1


def _protected_topics(
    kept: list[ScheduledItem], prereq_closure: dict[str, frozenset[str]] | None
) -> frozenset[str]:
    """不能砍的主題：任何保留項目的（遞移）前置主題。

    為什麼需要這個：砍項目是按**優先度層級**挑的，而優先度來自主題分數，
    跟依賴深度無關。一個低分的前置主題可能落在「加分」層，它的後置主題
    卻在「必學」層——照原本「同層從後面砍起」的規則，就會把必學項目的
    前置砍掉，而必學項目本身留著。使用者會拿到一份要他先做後置的計畫。

    原本的註解寫「後面的項目依賴前面的，砍後面不會讓前面的失去前置」，
    方向想反了：保護的是**更早**的項目，但被砍的候選不一定在更後面。
    """
    if not prereq_closure:
        return frozenset()
    protected: set[str] = set()
    for item in kept:
        protected |= prereq_closure.get(item.topic_id, frozenset())
    return frozenset(protected)


def _day_at(days: list[_dt.date], index: int) -> _dt.date:
    """取第 ``index`` 天；超出原定期限就繼續往後算。

    超出不是錯誤，是必須誠實呈現的結果：見 ``Replan.extra_days``。
    """
    if index < len(days):
        return days[index]
    return days[-1] + _dt.timedelta(days=index - len(days) + 1)


def replan(
    items: list[ScheduledItem],
    completed_task_ids: set[int],
    today: _dt.date,
    end_date: _dt.date,
    daily_minutes: int,
    prereq_closure: dict[str, frozenset[str]] | None = None,
) -> Replan:
    """把還沒做完的項目重排進剩下的日子。

    Args:
        items: 原計畫的**未完成**項目（已移出計畫的不要傳進來）。
        completed_task_ids: 已完成的 task id。
        today: 今天。
        end_date: 計畫的最後一天（含）。
        daily_minutes: 每天可投入的分鐘數。
        prereq_closure: 主題 id → 遞移前置主題集合（``core.planner``
            的 ``_prereq_closure`` 可以產生）。給了就不會砍掉保留項目的前置；
            不給則不保護——目前題庫排出來全是 ``must``，砍不到任何東西，
            所以兩者沒差，但第二批題庫進來之後就有差了。

    Returns:
        ``Replan``。``changed=False`` 表示沒有落後，原計畫不動。
    """
    if daily_minutes <= 0:
        raise ValueError("daily_minutes 必須大於 0")

    pending = [i for i in items if i.task_id not in completed_task_ids]

    # 沒有任何未完成項目落在今天之前 → 沒落後，不要動使用者的計畫。
    # 這一條是刻意的：每次打開網站都重排會讓計畫看起來不穩定，
    # 使用者會不信任它。
    overdue = [i for i in pending if i.scheduled_date < today]
    if not overdue:
        return Replan(items=list(pending), changed=False)

    days = _remaining_days(today, end_date)

    # 依優先度砍到排得進剩下的天數為止。
    #
    # 判斷依據是**實際裝填需要幾天**（``_days_needed``），不是「總分鐘數是否
    # 超過理想容量」。循序裝填裝不滿理想容量，兩種算法會得出矛盾的結果。
    keep = list(pending)
    culled: list[tuple[int, str]] = []
    for tier in CULL_ORDER:
        while _days_needed(keep, daily_minutes) > len(days):
            protected = _protected_topics(keep, prereq_closure)
            victim_index = next(
                (
                    idx
                    for idx in range(len(keep) - 1, -1, -1)
                    if keep[idx].priority == tier
                    and keep[idx].topic_id not in protected
                ),
                None,
            )
            if victim_index is None:
                break
            victim = keep.pop(victim_index)
            label = "加分" if tier == TIER_BONUS else "建議"
            culled.append((victim.task_id, f"剩下的時間不夠，先砍{label}項目"))

    # 保持相對順序逐日裝填。順序不變 → 前置關係自動維持正確。
    #
    # **每日上限永遠優先於結束日。** 排不完時讓計畫往後延，而不是把多出來的
    # 時數硬塞進最後一天。曾經寫成後者，結果 60 分鐘的一天被排了 340 分鐘：
    # 測試綠、畫面好看、照著做不可能完成。每日上限是使用者真實的可用時間，
    # 結束日只是期望。
    placed = _pack(keep, daily_minutes)
    result = [
        ScheduledItem(
            task_id=item.task_id,
            title=item.title,
            topic_id=item.topic_id,
            scheduled_date=_day_at(days, day_index),
            order_in_day=order_in_day,
            priority=item.priority,
            est_minutes=item.est_minutes,
            difficulty=item.difficulty,
            topic_score=item.topic_score,
        )
        for item, day_index, order_in_day in placed
    ]

    # 超出的部分一律從**實際排定的位置**回推，和 extra_days 用同一份裝填結果，
    # 所以兩個數字不可能互相矛盾。
    overflow = sum(
        item.est_minutes for item, day_index, _ in placed if day_index >= len(days)
    )

    return Replan(
        items=result,
        culled=culled,
        changed=True,
        overflow_minutes=overflow,
        extra_days=max(0, _days_needed(keep, daily_minutes) - len(days)),
    )
