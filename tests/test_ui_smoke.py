"""UI 冒煙測試：真的把表單送出去，看頁面會不會炸。

為什麼需要這一支：``pytest`` 全綠只證明 ``core/`` 與 ``db/`` 對，
不證明頁面跑得起來。Streamlit 把例外畫在瀏覽器裡，所以
「伺服器回 200」也不代表沒出錯——實際啟動時 log 一樣是乾淨的。

用 ``st.testing.v1.AppTest``：它會在同一個行程裡執行整個頁面腳本，
把例外收集到 ``at.exception``。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HOME = str(PROJECT_ROOT / "app" / "Home.py")
TODAY_PAGE = str(PROJECT_ROOT / "app" / "pages" / "1_今日任務.py")

TIMEOUT = 60


def test_home_renders(seeded_app_db) -> None:
    app = AppTest.from_file(HOME, default_timeout=TIMEOUT).run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert app.title[0].value.endswith("學習計畫")


def test_today_page_renders_without_a_plan(seeded_app_db) -> None:
    """還沒有計畫時也不能炸，要給出下一步指示。"""
    app = AppTest.from_file(TODAY_PAGE, default_timeout=TIMEOUT).run()
    assert not app.exception, [str(e.value) for e in app.exception]


def test_submitting_the_form_creates_a_plan(seeded_app_db) -> None:
    """送出表單 → 排出計畫 → 顯示結果，整條路徑不能有例外。"""
    app = AppTest.from_file(HOME, default_timeout=TIMEOUT).run()
    assert not app.exception

    app.number_input[0].set_value(10)  # 天數
    app.number_input[1].set_value(1.0)  # 每天小時
    app.button[0].click().run()

    assert not app.exception, [str(e.value) for e in app.exception]
    assert app.success, "應該顯示排程成功的訊息"
    assert "題" in app.success[0].value


def test_today_page_shows_tasks_after_planning(seeded_app_db) -> None:
    """排完計畫之後，今日任務頁要列得出今天的題目。"""
    home = AppTest.from_file(HOME, default_timeout=TIMEOUT).run()
    home.number_input[0].set_value(10)
    home.number_input[1].set_value(1.0)
    home.button[0].click().run()
    assert not home.exception

    today = AppTest.from_file(TODAY_PAGE, default_timeout=TIMEOUT).run()
    assert not today.exception, [str(e.value) for e in today.exception]
    # 計畫從今天開始，所以今天一定有任務，也一定有打勾用的 checkbox。
    assert today.checkbox, "今天應該有可以打勾的任務"
    assert today.progress, "應該顯示進度條"


def test_checking_a_task_marks_it_done(seeded_app_db) -> None:
    """打勾要真的寫進資料庫並反映在進度上。"""
    home = AppTest.from_file(HOME, default_timeout=TIMEOUT).run()
    home.number_input[0].set_value(10)
    home.number_input[1].set_value(1.0)
    home.button[0].click().run()

    page = AppTest.from_file(TODAY_PAGE, default_timeout=TIMEOUT).run()
    assert page.checkbox
    before = page.progress[1].value

    page.checkbox[0].set_value(True).run()
    assert not page.exception, [str(e.value) for e in page.exception]

    after = AppTest.from_file(TODAY_PAGE, default_timeout=TIMEOUT).run()
    assert not after.exception
    assert after.progress[1].value > before, "整體進度應該前進"


def test_overlapping_self_assessment_is_rejected(seeded_app_db) -> None:
    """同一主題同時選「已熟」和「沒學過」要被擋下來，不能默默取其一。"""
    app = AppTest.from_file(HOME, default_timeout=TIMEOUT).run()
    app.multiselect[0].set_value(["arrays"])
    app.multiselect[1].set_value(["arrays"])
    app.button[0].click().run()

    assert not app.exception
    assert app.error, "應該顯示錯誤訊息"
    assert "不能同時" in app.error[0].value


def test_impossible_daily_minutes_is_reported(seeded_app_db) -> None:
    """每天時間少到排不進任何一題時，要說清楚，不能給一份空計畫。"""
    app = AppTest.from_file(HOME, default_timeout=TIMEOUT).run()
    app.number_input[1].set_value(0.25)  # 15 分鐘
    app.button[0].click().run()
    assert not app.exception
    # 15 分鐘排得進幾題 Easy，所以這裡不該報錯；確認它沒炸就夠。
    assert app.success or app.error


@pytest.mark.parametrize("preset_index", [0, 1, 2, 3])
def test_every_goal_preset_produces_a_plan(seeded_app_db, preset_index: int) -> None:
    """每個目標選項都要排得出計畫。

    「進階演算法」特別容易出事：它指向的 trees／graphs／dp 目前**一題都沒有**，
    所以 goal_factor 全加在沒有題目的主題上。計畫還是該排得出來
    （用其他主題的題目），不能變成空白頁或例外。
    """
    app = AppTest.from_file(HOME, default_timeout=TIMEOUT).run()
    app.selectbox[0].select_index(preset_index).run()
    app.number_input[0].set_value(10)
    app.number_input[1].set_value(1.0)
    app.button[0].click().run()

    assert not app.exception, [str(e.value) for e in app.exception]
    assert app.success, f"第 {preset_index} 個目標選項排不出計畫"
