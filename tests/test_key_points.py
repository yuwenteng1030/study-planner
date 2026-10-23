"""主題重點（key_points）：資料檔要是字串，舊資料裡的 dict 也要顯示得出來。

真的發生過：``topics.yaml`` 有一行 ``key=lambda x: (a, b)`` 沒加引號，被 YAML
讀成 dict。它沒有在任何地方報錯，一路存進資料庫，直到今日任務頁畫到 sorting
的題目時才炸——炸在交易裡，連重排與打勾都被 rollback。
"""

from __future__ import annotations

import pytest
import yaml

from db.repositories import key_point_text
from scripts.import_data import _check_key_points
from tests.conftest import TOPICS_PATH


def test_every_key_point_in_the_data_file_is_a_string() -> None:
    topics = yaml.safe_load(TOPICS_PATH.read_text(encoding="utf-8"))
    bad = [
        (t["id"], p) for t in topics for p in t.get("key_points") or [] if not isinstance(p, str)
    ]
    assert not bad, f"有重點被 YAML 讀成非字串（有冒號的那行要加引號）：{bad}"


def test_import_rejects_a_key_point_that_is_not_a_string() -> None:
    topics = yaml.safe_load("- id: s\n  key_points:\n    - 多鍵排序用 key=lambda x: (a, b)\n")
    with pytest.raises(ValueError, match="引號"):
        _check_key_points(topics)


def test_old_dict_key_points_still_display() -> None:
    """已經存進資料庫的舊資料不改，顯示時接回原句。"""
    assert key_point_text({"多鍵排序用 key=lambda x": "(a, b)；要反向就取負值"}) == (
        "多鍵排序用 key=lambda x: (a, b)；要反向就取負值"
    )
    assert key_point_text("一般的重點") == "一般的重點"
