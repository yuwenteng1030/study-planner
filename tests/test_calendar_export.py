"""每日提醒的行事曆檔：格式照 RFC 5545、時區換算、只匯今天以後、跳脫與折行。"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from streamlit.testing.v1 import AppTest

from core.calendar_export import DayTask, build_ics

TODAY = _dt.date(2026, 10, 24)
NOW = _dt.datetime(2026, 10, 24, 1, 0)


def _task(day_offset: int, title: str = "Two Sum", minutes: int = 20) -> DayTask:
    return DayTask(
        day=TODAY + _dt.timedelta(days=day_offset), title=title, difficulty="Easy",
        est_minutes=minutes, url="https://www.hackerrank.com/challenges/x/problem",
    )


def _unfold(ics: str) -> list[str]:
    return ics.replace("\r\n ", "").split("\r\n")


def _build(tasks):
    return build_ics(tasks, plan_id=7, start_at=_dt.time(20, 0), tz="Asia/Taipei", today=TODAY, now_utc=NOW)


def test_one_event_per_day_from_today_with_an_alarm() -> None:
    ics = _build([_task(-1), _task(0), _task(0, "B", 30), _task(2)])
    lines = _unfold(ics)
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-2] == "END:VCALENDAR" and lines[-1] == ""
    assert lines.count("BEGIN:VEVENT") == 2, "昨天的不匯出；今天兩題合成一個事件"
    assert "UID:plan7-20261024@study-planner" in lines, "UID 固定，重新匯入會更新同一天"
    assert lines.count("TRIGGER:-PT10M") == 2
    assert sum(line.startswith("SEQUENCE:") for line in lines) == 2, "重新匯入要能蓋過舊版"


def test_times_are_converted_to_utc() -> None:
    lines = _unfold(_build([_task(0, minutes=20), _task(0, "B", 30)]))
    assert "DTSTART:20261024T120000Z" in lines, "台北晚上 8 點＝UTC 中午 12 點"
    assert "DTEND:20261024T125000Z" in lines, "長度是當天預估的總分鐘數（50 分鐘）"


def test_text_is_escaped_and_lines_are_folded() -> None:
    ics = _build([_task(0, "Sherlock, and; the \\ Valid String 很長的中文標題" * 3)])
    assert all(len(line.encode("utf-8")) <= 75 for line in ics.split("\r\n")), "每行最多 75 位元組"
    description = next(line for line in _unfold(ics) if line.startswith("DESCRIPTION:1."))
    assert r"Sherlock\, and\; the \\ Valid" in description
    ics.encode("utf-8").decode("utf-8")  # 折行沒有把中文字切壞
    lone_cr = _build([_task(0, "A\rSUMMARY:偽造")])
    assert "\r\nSUMMARY:偽造" not in lone_cr, "單獨的 \\r 也不能讓標題開出一行新的欄位"


def test_today_page_offers_the_calendar_download(seeded_app_db) -> None:
    root = Path(__file__).resolve().parent.parent / "app"
    home = AppTest.from_file(str(root / "Home.py"), default_timeout=60).run()
    home.number_input[0].set_value(10)
    home.number_input[1].set_value(1.0)
    home.button[0].click().run()
    page = AppTest.from_file(str(root / "pages" / "1_今日任務.py"), default_timeout=60).run()
    assert not page.exception, [str(e.value) for e in page.exception]
    assert any("行事曆" in e.label for e in page.expander)
