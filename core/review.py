"""回顧：每週／每月的完成率，以及最常落後的主題。

只算不畫，不碰資料庫：輸入是 ``db/repositories.load_review_data`` 整理好的紀錄，
所以每一條規則都能用手寫的資料測。

兩個容易說謊的地方，這裡刻意處理：

1. **重排會改寫日期。** 一題上週沒做，重排後被挪到這週，如果照「現在的」日期分期，
   上週會變成 100%、這週背起所有債——跟實際發生的事相反。所以分期與「晚做完」
   一律用**原本排定的日期**（``ItemRecord.scheduled_date``，由重排事件還原），
   「過期未做」才看現在的日期（``current_date``）。
2. **分母包含被移出計畫的項目**，理由同 ``repositories.progress_summary``：
   把放棄的題目從分母扣掉，會讓「砍掉一半」看起來像「完成度大增」。
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

WEEK = "week"
MONTH = "month"


@dataclass(frozen=True)
class ItemRecord:
    """一個計畫項目在回顧時需要的欄位。跨所有計畫，包含已收起來的。"""

    task_id: int
    topic_id: str
    #: **原本**排定的日期（重排之前）。分期與「晚做完」都看它。
    scheduled_date: _dt.date
    status: str  # todo / done / skipped
    done_on: _dt.date | None = None
    #: 現在排在哪天（重排之後）。沒給就等於原本的日期。
    current_date: _dt.date | None = None
    #: 是不是進行中的計畫。已收起來的計畫裡沒做的題目算「被放下」，不算「過期未做」。
    active: bool = True

    @property
    def now_date(self) -> _dt.date:
        return self.current_date or self.scheduled_date


@dataclass(frozen=True)
class PeriodRow:
    """一週（或一個月）：那段期間原本排了幾題、其中做完幾題。"""

    start: _dt.date
    label: str
    planned: int
    done: int

    @property
    def ratio(self) -> float:
        return self.done / self.planned if self.planned else 0.0


@dataclass(frozen=True)
class TopicLag:
    """一個主題的落後情況。四種訊號分開數，畫面才說得出「怎麼落後的」。"""

    topic_id: str
    name: str
    #: 原本排定日期已經到了的題數（與趨勢圖同一個分母規則）。
    planned: int
    #: 做完了，但比原本排定的日子晚。
    late: int
    #: 進行中的計畫裡，現在排的日子已經過了，還沒做。
    overdue: int
    #: 被重排移出計畫，或計畫收起來時還沒做——而且之後也沒在別處做完。
    cut: int
    #: 過期了才被重排往後挪的題數（被別題擠到後面的不算）。
    moved: int
    #: 至少有一種落後訊號的題數。
    lagging: int

    @property
    def rate(self) -> float:
        return self.lagging / self.planned if self.planned else 0.0


def period_start(day: _dt.date, by: str) -> _dt.date:
    """所屬週（週一開始）或所屬月的第一天。"""
    if by == WEEK:
        return day - _dt.timedelta(days=day.weekday())
    if by == MONTH:
        return day.replace(day=1)
    raise ValueError(f"不認得的期間：{by!r}")


def _label(start: _dt.date, by: str) -> str:
    # 帶年份：跨年時「12/28 週」與「01/04 週」才分得出先後
    return f"{start:%Y/%m/%d} 週" if by == WEEK else f"{start:%Y-%m}"


def completion_trend(items: Iterable[ItemRecord], today: _dt.date, by: str = WEEK) -> list[PeriodRow]:
    """依**原本**排定的日期分組，算每段期間排了幾題、做完幾題。由舊到新。

    只算原本排定日期**不晚於今天**的項目：還沒到的日子沒有「該做而沒做」，
    算進來會讓本週的完成率在週一看起來很難看。
    算的是計畫項目，不是不重複的題目：同一題在兩份計畫各排一次，就是兩次承諾。
    """
    planned: dict[_dt.date, int] = {}
    done: dict[_dt.date, int] = {}
    for item in items:
        if item.scheduled_date > today:
            continue
        start = period_start(item.scheduled_date, by)
        planned[start] = planned.get(start, 0) + 1
        if item.status == "done":
            done[start] = done.get(start, 0) + 1
    return [
        PeriodRow(start=s, label=_label(s, by), planned=planned[s], done=done.get(s, 0))
        for s in sorted(planned)
    ]


def lagging_topics(
    items: Iterable[ItemRecord],
    moved_task_ids: Iterable[int],
    names: Mapping[str, str],
    today: _dt.date,
    done_elsewhere: Iterable[int] = (),
) -> list[TopicLag]:
    """有落後的主題，最嚴重的在前（落後題數多 → 落後比例高 → id）。

    ``moved_task_ids``：**過期了才**被重排往後挪的題目。重排會把後面所有待做的題
    往後推一格，那些只是被擠到，不是自己落後。
    ``done_elsewhere``：計畫外完成的題目。一題在任何地方做完了，就不再算
    「過期未做」或「被放下」——在舊計畫沒做、新計畫做完，是補上了，不是落後。
    """
    items = list(items)
    # 先算「做完了」再濾日期：新計畫把題目排到下週、使用者提早做完了，
    # 那一項還沒到日子，但舊計畫裡同一題就不該算「被放下」。
    finished = {i.task_id for i in items if i.status == "done"} | set(done_elsewhere)
    items = [i for i in items if i.scheduled_date <= today]
    moved = set(moved_task_ids)

    by_topic: dict[str, list[ItemRecord]] = {}
    for item in items:
        by_topic.setdefault(item.topic_id, []).append(item)

    out: list[TopicLag] = []
    for topic_id, members in by_topic.items():
        late = {
            i.task_id for i in members
            if i.status == "done" and i.done_on and i.done_on > i.scheduled_date
        }
        overdue = {
            i.task_id for i in members
            if i.status == "todo" and i.active and i.now_date < today and i.task_id not in finished
        }
        cut = {
            i.task_id for i in members
            if (i.status == "skipped" or (i.status == "todo" and not i.active))
            and i.task_id not in finished
        }
        shifted = {i.task_id for i in members if i.task_id in moved}
        lagging = late | overdue | cut | shifted
        if not lagging:
            continue
        out.append(
            TopicLag(
                topic_id=topic_id,
                name=names.get(topic_id, topic_id),
                planned=len({i.task_id for i in members}),
                late=len(late),
                overdue=len(overdue),
                cut=len(cut),
                moved=len(shifted),
                lagging=len(lagging),
            )
        )
    return sorted(out, key=lambda t: (-t.lagging, -t.rate, t.topic_id))
