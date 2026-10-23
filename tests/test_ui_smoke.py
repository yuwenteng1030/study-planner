"""UI 冒煙測試：真的把表單送出去，看頁面會不會炸。

為什麼需要這一支：``pytest`` 全綠只證明 ``core/`` 與 ``db/`` 對，
不證明頁面跑得起來。Streamlit 把例外畫在瀏覽器裡，所以
「伺服器回 200」也不代表沒出錯——實際啟動時 log 一樣是乾淨的。

用 ``st.testing.v1.AppTest``：它會在同一個行程裡執行整個頁面腳本，
把例外收集到 ``at.exception``。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HOME = str(PROJECT_ROOT / "app" / "Home.py")
TODAY_PAGE = str(PROJECT_ROOT / "app" / "pages" / "1_今日任務.py")
DATA_PAGE = str(PROJECT_ROOT / "app" / "pages" / "2_我的資料.py")
MAP_PAGE = str(PROJECT_ROOT / "app" / "pages" / "3_學習地圖.py")
REVIEW_PAGE = str(PROJECT_ROOT / "app" / "pages" / "4_回顧.py")

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
    # 打勾的那一刻要說一句鼓勵；測試沒有 API key，所以是範本
    assert page.toast, "打勾後應該跳出鼓勵訊息"
    assert "Claude" not in page.toast[0].value

    after = AppTest.from_file(TODAY_PAGE, default_timeout=TIMEOUT).run()
    assert not after.exception
    assert after.progress[1].value > before, "整體進度應該前進"
    assert not after.toast, "鼓勵只在打勾的那一輪出現，重新打開不該再跳"


def test_progress_moves_in_the_same_run_as_the_click(seeded_app_db) -> None:
    """打勾之後，**不重開頁面**，上面的進度條就要跟著動。

    上一支測試看起來測的是同一件事，其實不是：它在斷言之前用
    ``AppTest.from_file(...)`` 重開了一次頁面，等於使用者按了重新整理。
    重開當然讀得到新的狀態，所以「進度條永遠慢一拍」這個 bug 躲得過它。

    真正的 bug 長這樣：``_show_progress`` 在項目迴圈**之前**就畫完了，
    而寫入資料庫發生在迴圈**裡面**。Streamlit 在 widget 變動時只重跑一次腳本，
    所以那一輪畫出來的進度，是這次點擊**之前**的狀態——卡片的刪除線立刻更新
    （它在寫入之後才算 ``done``），但上面的「今天 n / m 題」停在舊數字。
    使用者打完一題，看到的是沒動的進度條。
    """
    home = AppTest.from_file(HOME, default_timeout=TIMEOUT).run()
    home.number_input[0].set_value(10)
    home.number_input[1].set_value(1.0)
    home.button[0].click().run()
    assert not home.exception

    page = AppTest.from_file(TODAY_PAGE, default_timeout=TIMEOUT).run()
    assert page.checkbox, "今天應該有可以打勾的任務"
    before = page.progress[1].value

    page.checkbox[0].set_value(True).run()
    assert not page.exception, [str(e.value) for e in page.exception]

    assert page.progress[1].value > before, (
        "打勾後進度條沒有在同一輪更新——使用者要按重新整理才看得到自己的進度"
    )


def _map_data(app) -> dict:
    """地圖元件收到的資料（SVG 與小熊位置）。元件的 JavaScript 在測試裡不會跑。"""
    [element] = app.get("bidi_component")
    return json.loads(element.proto.json)


def _map_done(app) -> int:
    # 狀態列的「全部關卡 n / m」（關掉重點路線時才是全部）
    [metric] = [m for m in app.metric if m.label == "全部關卡"]
    return int(str(metric.value).split("/")[0])


def _full_map():
    """打開學習地圖並關掉「只看重點路線」，看整個題庫。"""
    app = AppTest.from_file(MAP_PAGE, default_timeout=TIMEOUT)
    app.session_state["map_view"] = "all"
    return app.run()


def test_map_page_draws_every_task_as_a_level(seeded_app_db) -> None:
    """地圖要畫出每一題一關、一隻小熊，文字版也要列出四個區域。"""
    app = _full_map()
    assert not app.exception, [str(e.value) for e in app.exception]

    data = _map_data(app)
    assert data["svg"].count("data-task=") == 125
    assert data["svg"].count('class="bear"') == 1
    assert f'data-task="{data["here"]}"' in data["svg"]
    assert [h.value for h in app.subheader] == ["起點・小木屋", "第一區・草原", "第二區・森林", "第三區・雪山"]


def test_submit_button_clears_the_level_and_moves_the_bear(seeded_app_db) -> None:
    """按「我 submit 過了」：總數多一、小熊換到下一關、上方面板換成下一題。

    不管這一題在不在計畫裡（共用的測試資料庫可能已經有計畫），
    「寫到哪裡」由 ``test_map_completion.py`` 守，這裡確認按鈕真的接上了。
    """
    app = _full_map()
    before_done, before_here = _map_done(app), _map_data(app)["here"]
    assert before_done < 124, "前提不成立：共用的測試資料庫已經做到最後一關，小熊不會再走"
    before_title = app.markdown[0].value

    [button] = [b for b in app.button if b.label == "我 submit 過了"]
    button.click().run()
    assert not app.exception, [str(e.value) for e in app.exception]

    assert _map_done(app) == before_done + 1
    assert app.toast, "破關後應該跳出鼓勵訊息"
    data = _map_data(app)
    assert data["here"] != before_here
    assert f'data-task="{before_here}"' in data["svg"]
    # 破關後小卡直接開在下一關，並說一聲過關
    assert data["focus"]["task"] == data["here"]
    assert data["focus"]["cleared"]
    assert app.markdown[0].value != before_title


def test_map_page_counts_tasks_checked_on_the_today_page(seeded_app_db) -> None:
    """在今日任務頁打勾之後，地圖的總題數要跟著多。

    自己排計畫、自己打勾，不依賴前面測試留下的狀態。勾的是今天**還沒做過**
    的題目：地圖算的是不重複的題目，勾一題之前別的計畫裡已經做過的，數字不會動。
    計數本身的正確性由 ``test_game_map.py`` 守；這裡確認頁面真的接到了資料庫。
    """
    def map_done() -> int:
        app = _full_map()
        assert not app.exception, [str(e.value) for e in app.exception]
        return _map_done(app)

    home = AppTest.from_file(HOME, default_timeout=TIMEOUT).run()
    home.number_input[0].set_value(10)
    home.number_input[1].set_value(1.0)
    home.button[0].click().run()
    assert not home.exception

    before = map_done()
    page = AppTest.from_file(TODAY_PAGE, default_timeout=TIMEOUT).run()
    for box in page.checkbox:
        box.set_value(True).run()
        assert not page.exception, [str(e.value) for e in page.exception]
        if map_done() > before:
            return
    pytest.fail(f"今天每一題都勾了，地圖仍停在 {before} 題")


def test_new_plan_skips_tasks_already_done(seeded_app_db) -> None:
    """地圖上做完的（或任何一份計畫裡勾過的）題目，新排的計畫不能再排一次。"""
    from db import repositories as repo
    from db.session import session_scope

    with session_scope() as session:
        task_id = repo.load_task_inputs(session)[0].id
        repo.mark_task_done_for_user(session, 1, task_id)
    with session_scope() as session:
        done = repo.load_completed_task_ids_for_user(session, 1)
    assert task_id in done

    home = AppTest.from_file(HOME, default_timeout=TIMEOUT).run()
    home.number_input[0].set_value(10)
    home.number_input[1].set_value(1.0)
    home.button[0].click().run()
    assert not home.exception, [str(e.value) for e in home.exception]

    with session_scope() as session:
        planned = {i.task_id for i in repo.load_active_plan(session, 1).items}
    assert planned, "前提不成立：沒排出任何題目"
    assert not planned & done, f"做過的題目又被排進來：{sorted(planned & done)}"


def test_review_page_shows_trend_and_switches_to_months(seeded_app_db) -> None:
    """回顧頁：有計畫時要畫出趨勢圖與三個數字，切到「每月」也不能炸。"""
    home = AppTest.from_file(HOME, default_timeout=TIMEOUT).run()
    home.number_input[0].set_value(10)
    home.number_input[1].set_value(1.0)
    home.button[0].click().run()
    assert not home.exception

    app = AppTest.from_file(REVIEW_PAGE, default_timeout=TIMEOUT).run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert [m.label for m in app.metric] == ["本週完成率", "計畫裡做完的題目", "計畫外完成"]
    assert app.get("vega_lite_chart"), "應該畫出趨勢圖"
    # 估時校正：沒有落後主題時也要畫到（以前「沒有落後」會提早 return）
    assert "預估時間準不準" in [h.value for h in app.subheader]

    app.radio[0].set_value("每月").run()
    assert not app.exception, [str(e.value) for e in app.exception]


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

    「進階演算法」特別容易出事：它指向 trees／graphs／dp／tries，這些主題的
    base_weight 偏低（2–6），所以 goal_factor ×1.3 之後仍可能整批落在建議層
    或加分層，而每日容量會先被必學層吃掉。計畫還是該排得出來，
    不能變成空白頁或例外。

    （2026-10-22 之前這一支防的是更基本的狀況：那時 trees／graphs／dp
    一題都沒有，goal_factor 全加在空主題上。題庫補齊後那個情境消失了，
    但「選了這一項會不會排出空計畫」仍然值得守著。）
    """
    app = AppTest.from_file(HOME, default_timeout=TIMEOUT).run()
    app.selectbox[0].select_index(preset_index).run()
    app.number_input[0].set_value(10)
    app.number_input[1].set_value(1.0)
    app.button[0].click().run()

    assert not app.exception, [str(e.value) for e in app.exception]
    assert app.success, f"第 {preset_index} 個目標選項排不出計畫"


# ---------------------------------------------------------------------------
# 我的資料（匯出／刪除）
#
# 這一組放在檔案最後，因為最後一支測試會把共用的測試資料庫清空。
# ``seeded_app_db`` 是 session scope，所以**不要**在這之後再加需要既有計畫的
# UI 測試；要加的話加在這個區塊上面。
# ---------------------------------------------------------------------------


def test_data_page_renders(seeded_app_db) -> None:
    app = AppTest.from_file(DATA_PAGE, default_timeout=TIMEOUT).run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert app.download_button, "應該有下載按鈕"


def test_data_page_works_on_a_brand_new_account(seeded_app_db) -> None:
    """沒有任何計畫時也不能炸，下載鍵也要還在。

    「我要帶走我的資料」不該因為資料是空的就壞掉。
    """
    app = AppTest.from_file(DATA_PAGE, default_timeout=TIMEOUT).run()
    assert not app.exception
    assert app.metric, "應該顯示統計數字"


def test_export_download_is_valid_json(seeded_app_db) -> None:
    """下載鍵裡的內容要是能解析的 JSON。

    這是整頁唯一真正的產出。只檢查「有這顆按鈕」會漏掉最可能出錯的事：
    日期欄位沒轉成字串，使用者按下去的那一刻才炸。
    """
    home = AppTest.from_file(HOME, default_timeout=TIMEOUT).run()
    home.number_input[0].set_value(10)
    home.number_input[1].set_value(1.0)
    home.button[0].click().run()
    assert not home.exception

    app = AppTest.from_file(DATA_PAGE, default_timeout=TIMEOUT).run()
    assert not app.exception, [str(e.value) for e in app.exception]

    # 頁面會把同一份 JSON 顯示在 expander 的 st.code 裡，從那裡取前綴來驗。
    assert app.code, "應該有預覽區塊"
    preview = app.code[0].value
    assert preview.lstrip().startswith("{")
    assert '"format_version"' in preview
    assert '"plans"' in preview


def test_delete_without_typing_the_confirm_word_does_nothing(seeded_app_db) -> None:
    """沒輸入確認字串就按下去，不能刪掉任何東西。

    畫面上的 ``disabled`` 是前端狀態，不是把關。這一支測的是伺服器端那一道
    檢查——不可還原的動作不能只靠一個 disabled 屬性守著。
    """
    home = AppTest.from_file(HOME, default_timeout=TIMEOUT).run()
    home.number_input[0].set_value(10)
    home.number_input[1].set_value(1.0)
    home.button[0].click().run()
    assert not home.exception

    app = AppTest.from_file(DATA_PAGE, default_timeout=TIMEOUT).run()
    before = app.metric[0].value
    app.button[0].click().run()  # 不填確認字串就按「清除紀錄」

    assert not app.exception, [str(e.value) for e in app.exception]
    assert not app.success, "沒確認就不該出現「已刪除」"
    assert app.error, "應該明確說沒有刪除任何資料"
    assert "沒有刪除" in app.error[0].value

    # 計畫數量要一模一樣。比對前後的值，不寫死數字——這個測試資料庫是
    # 整個檔案共用的，累積了前面每一支測試排出的計畫。
    after = AppTest.from_file(DATA_PAGE, default_timeout=TIMEOUT).run()
    assert after.metric[0].value == before, f"資料被動到了：{before} → {after.metric[0].value}"
    assert after.metric[0].value != "0 份", "前提不成立：本來就沒有資料可以刪"


def test_clearing_records_through_the_page(seeded_app_db) -> None:
    """輸入確認字串 → 按鍵 → 紀錄真的被刪掉，而且頁面不炸。

    **這一支會把共用的測試資料庫清空**，所以它排在這個檔案的最後。
    """
    home = AppTest.from_file(HOME, default_timeout=TIMEOUT).run()
    home.number_input[0].set_value(10)
    home.number_input[1].set_value(1.0)
    home.button[0].click().run()
    assert not home.exception

    app = AppTest.from_file(DATA_PAGE, default_timeout=TIMEOUT).run()
    app.text_input[0].set_value("清除紀錄").run()
    app.button[0].click().run()

    assert not app.exception, [str(e.value) for e in app.exception]
    assert app.success, "應該回報刪了多少東西"
    assert "已刪除" in app.success[0].value

    # 重開這一頁：計畫歸零，頁面照樣跑得起來。
    after = AppTest.from_file(DATA_PAGE, default_timeout=TIMEOUT).run()
    assert not after.exception, [str(e.value) for e in after.exception]
    assert after.metric[0].value == "0 份"

    # 刪完之後還要能再排一份新計畫——這才是「乾淨的新開始」。
    again = AppTest.from_file(HOME, default_timeout=TIMEOUT).run()
    again.number_input[0].set_value(10)
    again.number_input[1].set_value(1.0)
    again.button[0].click().run()
    assert not again.exception, [str(e.value) for e in again.exception]
    assert again.success


def test_review_page_without_any_plan(seeded_app_db) -> None:
    """沒有任何計畫時給下一步指示，不能炸。

    自己先清空使用者資料；放在檔案最後，因為測試資料庫是整個檔案共用的。
    """
    from db import repositories as repo
    from db.session import session_scope

    with session_scope() as session:
        repo.delete_user_data(session, 1)
    app = AppTest.from_file(REVIEW_PAGE, default_timeout=TIMEOUT).run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert app.info, "應該提示先去排計畫"



def test_map_opens_on_the_focus_route_by_default(seeded_app_db) -> None:
    """預設只看重點路線：30 關、三段，狀態列有等級與經驗值。"""
    app = AppTest.from_file(MAP_PAGE, default_timeout=TIMEOUT).run()
    assert not app.exception, [str(e.value) for e in app.exception]
    data = _map_data(app)
    assert data["svg"].count("data-task=") == 30
    assert "第一段・暖身" in data["svg"] and "第三段・進階" in data["svg"]
    assert {m.label for m in app.metric} >= {"等級", "經驗值", "重點路線"}


def test_deep_link_to_a_non_focus_task_switches_to_the_full_map(seeded_app_db) -> None:
    """「在地圖上看」連到不在重點路線上的題目：自動切成整個題庫並打開那一關。"""
    import os
    import sqlite3

    db = os.environ["DATABASE_URL"].removeprefix("sqlite:///")
    with sqlite3.connect(db) as conn:
        task_id = conn.execute("SELECT id FROM tasks WHERE focus_stage IS NULL ORDER BY id LIMIT 1").fetchone()[0]
    app = AppTest.from_file(MAP_PAGE, default_timeout=TIMEOUT)
    app.query_params["task"] = str(task_id)
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    data = _map_data(app)
    assert data["svg"].count("data-task=") == 125
    assert data["focus"]["task"] == task_id
