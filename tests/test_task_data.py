"""題庫資料本身的健全性：說明會直接顯示在任務卡上。"""

from __future__ import annotations

import csv
from pathlib import Path

ROWS = list(csv.DictReader((Path(__file__).parent.parent / "data" / "tasks.csv").open(encoding="utf-8")))


def test_static_notes_are_not_cut_off_by_unquoted_commas() -> None:
    # 實際踩過：「組合數 C(n,2)」沒加引號，CSV 在逗號切斷，任務卡只剩「組合數 C(n」
    broken = [r["title"] for r in ROWS if r["static_note"].count("(") != r["static_note"].count(")")]
    assert broken == []


def test_every_row_has_exactly_the_header_columns() -> None:
    assert all(None not in r and all(v is not None for v in r.values()) for r in ROWS)
