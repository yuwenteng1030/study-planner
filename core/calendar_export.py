"""把計畫匯出成行事曆檔（iCalendar，``.ics``），當作每日提醒。

為什麼不寄提醒信：寄信要有寄件服務、要存使用者信箱、要排程器每天跑，公開展示版
也不該替陌生人寄信。行事曆檔匯入 Google／Apple／Outlook 行事曆之後，提醒由使用者
自己的行事曆負責——手機會跳通知，而且使用者隨時可以自己刪掉，不需要「取消訂閱」。

代價：計畫重排之後，行事曆裡的日子不會跟著變，要重新下載一次。每個事件的 UID 固定是
「計畫＋日期」、SEQUENCE 是下載的時間，重新匯入時行事曆會更新同一天的事件，而不是多出一份。
重排後整天被清空的日子，舊事件不會被取消，要自己刪。

這個模組是純函式，不碰資料庫，照 RFC 5545 輸出（CRLF 換行、75 位元組折行、跳脫逗號分號）。
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterable
from dataclasses import dataclass
from zoneinfo import ZoneInfo

#: 提醒提前幾分鐘跳出。
ALARM_MINUTES_BEFORE = 10


@dataclass(frozen=True)
class DayTask:
    """行事曆上一題的資料。"""

    day: _dt.date
    title: str
    difficulty: str
    est_minutes: int
    url: str


def _escape(text: str) -> str:
    """RFC 5545 §3.3.11：反斜線、逗號、分號要跳脫，換行寫成 ``\\n``。"""
    return (
        text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """一行超過 75 位元組（UTF-8）就折行，續行以一個空白開頭。不能把一個中文字切成兩半。"""
    out, current, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        limit = 75 if not out else 74  # 續行開頭的空白也算一個位元組
        if size + n > limit:
            out.append(current)
            current, size = "", 0
        current += ch
        size += n
    out.append(current)
    return "\r\n ".join(out)


def _utc(day: _dt.date, at: _dt.time, tz: str) -> str:
    local = _dt.datetime.combine(day, at, tzinfo=ZoneInfo(tz))
    return local.astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics(
    tasks: Iterable[DayTask],
    *,
    plan_id: int,
    start_at: _dt.time,
    tz: str,
    today: _dt.date,
    now_utc: _dt.datetime,
) -> str:
    """每天一個事件：從 ``start_at`` 開始、長度是當天預估的總分鐘數，提前 10 分鐘提醒。

    只匯出今天（含）以後的日子：過去的事件加進行事曆只會變成一排已經錯過的提醒。
    時間一律換成 UTC 寫進檔案（``...Z``），不必附 VTIMEZONE 區塊，各家行事曆都讀得懂。
    """
    by_day: dict[_dt.date, list[DayTask]] = {}
    for t in tasks:
        if t.day >= today:
            by_day.setdefault(t.day, []).append(t)

    stamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//study-planner//daily plan//ZH-TW",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:學習計畫",
    ]
    for day in sorted(by_day):
        items = by_day[day]
        minutes = sum(t.est_minutes for t in items)
        start = _dt.datetime.combine(day, start_at)
        end = start + _dt.timedelta(minutes=max(minutes, 15))
        description = "\n".join(
            f"{n}. {t.title}（{t.difficulty}、{t.est_minutes} 分鐘）\n{t.url}" for n, t in enumerate(items, start=1)
        )
        lines += [
            "BEGIN:VEVENT",
            f"UID:plan{plan_id}-{day:%Y%m%d}@study-planner",
            f"DTSTAMP:{stamp}",
            # 重新匯入時，行事曆靠較大的 SEQUENCE 判斷「這是同一個事件的新版」；沒有它，
            # Google 日曆常會略過 UID 已經存在的事件，題目清單就停在舊的
            f"SEQUENCE:{int(now_utc.replace(tzinfo=_dt.timezone.utc).timestamp())}",
            f"DTSTART:{_utc(day, start_at, tz)}",
            f"DTEND:{_utc(end.date(), end.time(), tz)}",
            f"SUMMARY:{_escape(f'📚 今天 {len(items)} 題（{minutes} 分鐘）')}",
            f"DESCRIPTION:{_escape(description)}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_escape(f'該練題了：今天 {len(items)} 題')}",
            f"TRIGGER:-PT{ALARM_MINUTES_BEFORE}M",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
