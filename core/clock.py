"""時間的唯一出處：「現在」一律 UTC，「今天」一律照使用者的時區。

為什麼要集中：原本各頁各自 ``date.today()``，用的是**伺服器**的時鐘；完成時間用
``datetime.now()`` 存本地時間，而 ``created_at`` 是資料庫的 ``now()``（UTC）。在自己的
筆電上剛好都是台灣時間所以看不出來；部署到 UTC 的伺服器後，台灣早上 8 點前會看到
昨天的任務，重排也會晚一天才發現落後。

資料庫裡的時間戳全部是「不帶時區的 UTC」（SQLite 沒有時區型別）。要變成使用者的
日期時才換算，換算只在 ``to_local_date`` 這一處。
"""

from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: 使用者沒設時區時的預設值，跟 ``users.tz`` 的預設一致。
DEFAULT_TZ = "Asia/Taipei"


def _zone(tz: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        # 資料庫裡的時區字串壞了不該讓整個網站打不開；退回預設，日期最多差幾小時
        return ZoneInfo(DEFAULT_TZ)


def utc_now() -> _dt.datetime:
    """現在的 UTC 時間，不帶時區（直接存進資料庫用）。"""
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def today_in(tz: str | None, now: _dt.datetime | None = None) -> _dt.date:
    """使用者時區的今天。``now`` 是帶時區的時間，測試用；省略就是現在。"""
    moment = now or _dt.datetime.now(_dt.timezone.utc)
    return moment.astimezone(_zone(tz)).date()


def to_local_date(stamp_utc: _dt.datetime, tz: str | None) -> _dt.date:
    """資料庫裡的時間戳（不帶時區的 UTC）→ 使用者時區的日期。"""
    aware = stamp_utc if stamp_utc.tzinfo else stamp_utc.replace(tzinfo=_dt.timezone.utc)
    return aware.astimezone(_zone(tz)).date()


def iso_utc(stamp_utc: _dt.datetime | None) -> str | None:
    """匯出用：明確標上 UTC（``+00:00``），讀檔的人才知道是哪個時區。"""
    if stamp_utc is None:
        return None
    aware = stamp_utc if stamp_utc.tzinfo else stamp_utc.replace(tzinfo=_dt.timezone.utc)
    return aware.isoformat(timespec="seconds")
