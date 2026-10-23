"""估時校正：拿使用者填的實際分鐘數，看題庫的預估準不準。

只算不改：``tasks.est_minutes`` 一個字都不動。真的拿數據改預估是 v0.2 的 estimator，
而且預估值會影響排得進幾題，改不改、怎麼改是使用者的決定。

三個刻意的取捨：

1. **沒填的不算。** 今日任務頁的輸入框預設空白（2026-10-23 起），所以 ``None`` 就是沒填，
   填了剛好等於預估就是真的準時，照算。（之前輸入框預設是預估值，只好把所有「等於預估」
   的紀錄丟掉，連真的準時的也一起丟——樣本偏向有落差的題。改成空白就不必猜了。）
2. **0 分鐘不算。** 存下來是因為那是使用者刻意填的（見今日任務頁），但比例除不出意義。
3. **同一題做過好幾次只算第一次。** 預估的是「第一次做這題要多久」；第二次通常快很多，
   算進來會把預估拉成高估。

結論用**中位數**比例（實際 ÷ 預估）：幾筆卡了很久的題不該把整體拖成「全部低估」。
"""

from __future__ import annotations

import datetime as _dt
import statistics
from collections.abc import Iterable
from dataclasses import dataclass

#: 一組至少要有幾筆才下結論。少於這個數只顯示筆數，不說準不準。
MIN_SAMPLES = 5

#: 中位數比例落在這個區間內就算「準」。上下界對稱（0.8 ≈ 1 / 1.25）。
ACCURATE_RANGE = (0.8, 1.25)

DIFFICULTIES = ("Easy", "Medium", "Hard")

ACCURATE = "準"
UNDER = "低估"  # 實際比預估久
OVER = "高估"  # 實際比預估快
TOO_FEW = "資料不足"


@dataclass(frozen=True)
class Sample:
    """一筆打勾紀錄：哪一題、預估幾分鐘、填了幾分鐘。"""

    task_id: int
    difficulty: str
    est_minutes: int
    actual_minutes: int | None
    done_at: _dt.datetime | None = None


@dataclass(frozen=True)
class GroupResult:
    """一組（某個難度或全部）的校正結果。"""

    label: str
    n: int
    #: 實際 ÷ 預估的中位數。``n`` 為 0 時是 None。
    median_ratio: float | None
    #: 實際 − 預估的中位數（分鐘）。正的＝比預估久。
    median_diff: float | None
    verdict: str


@dataclass(frozen=True)
class Calibration:
    groups: list[GroupResult]
    #: 打勾了但不能用的筆數（沒填、0 分鐘、重做）。畫面上要說出來。
    excluded: int


def usable(samples: Iterable[Sample]) -> tuple[list[Sample], int]:
    """挑出能拿來校正的紀錄，回傳（能用的, 不能用的筆數）。每題只留最早的一筆。"""
    samples = list(samples)
    # 先照完成時間排，同一題留第一次；沒有時間的排最後
    ordered = sorted(samples, key=lambda s: (s.done_at is None, s.done_at or _dt.datetime.min))
    seen: set[int] = set()
    kept: list[Sample] = []
    for s in ordered:
        if s.task_id in seen:
            continue
        seen.add(s.task_id)
        if (
            s.actual_minutes is None
            or s.actual_minutes <= 0
            or s.est_minutes <= 0
        ):
            continue
        kept.append(s)
    return kept, len(samples) - len(kept)


def _verdict(ratio: float | None, n: int) -> str:
    if ratio is None or n < MIN_SAMPLES:
        return TOO_FEW
    low, high = ACCURATE_RANGE
    if ratio > high:
        return UNDER
    if ratio < low:
        return OVER
    return ACCURATE


def _group(label: str, samples: list[Sample]) -> GroupResult:
    if not samples:
        return GroupResult(label=label, n=0, median_ratio=None, median_diff=None, verdict=TOO_FEW)
    ratio = statistics.median(s.actual_minutes / s.est_minutes for s in samples)
    diff = statistics.median(s.actual_minutes - s.est_minutes for s in samples)
    return GroupResult(
        label=label, n=len(samples), median_ratio=ratio, median_diff=diff,
        verdict=_verdict(ratio, len(samples)),
    )


def calibrate(samples: Iterable[Sample]) -> Calibration:
    """依難度分組（Easy／Medium／Hard），最後一列是全部。沒有資料的難度也列出來。"""
    kept, excluded = usable(samples)
    groups = [_group(d, [s for s in kept if s.difficulty == d]) for d in DIFFICULTIES]
    groups.append(_group("全部", kept))
    return Calibration(groups=groups, excluded=excluded)
