"""我的資料：把紀錄帶走，或把它刪乾淨。

這一頁不是功能，是義務。這個網站最終要開放給其他人用，而「我的資料能不能
帶走、能不能刪掉」是開放之前的最低門檻——不是等到有人來要才補。

兩個動作的實作都在 ``db/repositories.py``：匯出要含所有計畫（包含已收起來
的），刪除要按 ``progress_events`` → ``plan_items`` → ``plans`` →
``task_completions`` → ``users`` 的順序走。那是 schema 的知識，不該寫在畫面裡。
"""

from __future__ import annotations

import datetime as _dt

import json
import sys
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api.deps import get_current_user  # noqa: E402
from core import clock  # noqa: E402
from db import repositories as repo  # noqa: E402
from db.session import session_scope  # noqa: E402

st.set_page_config(page_title="我的資料", page_icon="📦", layout="centered")

#: 兩個刪除動作各自的確認字串。
#:
#: 為什麼要打字而不是按兩次按鈕：這個按鈕刪掉的是累積好幾週的學習紀錄，
#: 而且沒有還原。手滑點到兩次按鈕是會發生的事，手滑打出四個中文字不會。
CONFIRM_CLEAR = "清除紀錄"
CONFIRM_DROP = "刪除帳號"


def main() -> None:
    st.title("📦 我的資料")
    st.caption(
        "你的學習紀錄屬於你。這一頁可以把它整份下載帶走，也可以把它刪乾淨。"
    )

    user_id = get_current_user()

    # 匯出資料順便當成「現在有多少東西」的統計來源，不必再查一次。
    with session_scope() as session:
        repo.ensure_user(session, user_id)
        export = repo.export_user_data(session, user_id)

    _show_summary(export)
    st.divider()
    _show_export(export)
    st.divider()
    _show_delete(user_id)


def _show_summary(export: dict) -> None:
    plans = export["plans"]
    items = [item for plan in plans for item in plan["items"]]
    done = [i for i in items if i["status"] == "done"]
    minutes = sum(i["actual_minutes"] or 0 for i in done)

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("計畫", f"{len(plans)} 份")
    col_b.metric("完成的項目", f"{len(done)} / {len(items)}")
    col_c.metric("累積練習時間", f"{minutes} 分鐘")
    st.caption(
        f"帳號建立於 {_local_day(export['user']['created_at'], export['user'].get('tz'))}　|　"
        f"事件紀錄 {len(export['progress_events'])} 筆　|　"
        f"地圖上計畫外完成 {len(export['task_completions'])} 題"
    )


def _local_day(iso_utc: str | None, tz: str | None, fmt: str = "%Y/%m/%d") -> str:
    """匯出檔裡的 UTC 時間 → 使用者時區的日期（畫面與檔名用；檔案內容維持 UTC）。"""
    if not iso_utc:
        return "—"
    return clock.to_local_date(_dt.datetime.fromisoformat(iso_utc), tz).strftime(fmt)


def _show_export(export: dict) -> None:
    st.subheader("帶走")
    st.write(
        "下載一份 JSON，包含所有計畫（含已收起來的）、每一項的完成狀態與實際"
        "花費時間、以及全部的事件紀錄。題庫本身不含在內——那是網站的資料，"
        "不是你的——但每一項都帶著題目標題與網址，所以這份檔案離開這個網站"
        "也讀得懂。"
    )

    payload = json.dumps(export, ensure_ascii=False, indent=2)
    st.download_button(
        "下載我的學習紀錄（JSON）",
        data=payload,
        file_name=f"study-planner-export-{_local_day(export['exported_at'], export['user'].get('tz'), '%Y%m%d')}.json",
        mime="application/json",
        type="primary",
    )
    with st.expander("先看看裡面長什麼樣"):
        st.caption("只顯示前 2000 個字元。")
        st.code(payload[:2000], language="json")


def _show_delete(user_id: int) -> None:
    st.subheader("刪掉")
    st.warning(
        "刪除不可還原，也不會留備份。要留一份的話先用上面的下載按鈕。",
        icon="⚠️",
    )

    tab_clear, tab_drop = st.tabs(["清除學習紀錄", "刪除整個帳號"])

    with tab_clear:
        st.write(
            "刪掉所有計畫、所有項目、所有事件紀錄，但保留帳號。"
            "等同於回到「還沒排過任何計畫」的狀態。"
        )
        _confirm_and_delete(
            user_id,
            drop_account=False,
            confirm_word=CONFIRM_CLEAR,
            key="clear",
            button_label="清除我的全部學習紀錄",
        )

    with tab_drop:
        st.write(
            "連帳號那一列一起刪掉。"
        )
        st.info(
            "目前這個版本沒有登入功能，身分固定是同一個人"
            "（`api/deps.py` 的 `get_current_user()`）。"
            "所以刪掉帳號之後再打開這一頁，會自動建立一個全新的空帳號——"
            "「刪除帳號」在單人模式下的意思是**回到全新狀態**，"
            "不是「從此不能再用」。接上登入之後才會變成真正的註銷。",
            icon="ℹ️",
        )
        _confirm_and_delete(
            user_id,
            drop_account=True,
            confirm_word=CONFIRM_DROP,
            key="drop",
            button_label="刪除我的帳號與所有資料",
        )


def _confirm_and_delete(
    user_id: int,
    *,
    drop_account: bool,
    confirm_word: str,
    key: str,
    button_label: str,
) -> None:
    typed = st.text_input(
        f"要繼續的話，在這裡輸入「{confirm_word}」",
        key=f"confirm_{key}",
        placeholder=confirm_word,
    )
    ready = typed.strip() == confirm_word

    clicked = st.button(
        button_label,
        key=f"button_{key}",
        type="secondary",
        help=f"需要先在上面輸入「{confirm_word}」",
    )
    if not clicked:
        return

    # 把關就在這裡，而且**只**在這裡。
    #
    # 原本這顆按鈕寫成 ``disabled=not ready``，但那樣這段檢查就永遠跑不到：
    # 瀏覽器使用者按不動一顆 disabled 的按鈕（``AppTest`` 也直接拒絕點擊），
    # 於是真正的防線長在前端，而後端這段變成測不到的死碼。
    # 與其留一段「看起來有在守、其實跑不到」的程式，不如只留一道關卡，
    # 讓它跑得到、也測得到。代價是按鈕永遠可以按——但按下去會得到一句
    # 明確的錯誤，而不是靜靜地什麼都沒發生。
    if not ready:
        st.error(f"請先輸入「{confirm_word}」再按。沒有刪除任何資料。")
        return

    # 刪除自己開一個交易範圍。這裡刻意**不呼叫** st.rerun()：
    # st.rerun() 是用拋例外實作的，而 session_scope() 看到例外就 rollback，
    # 刪除會被回捲掉但畫面看起來像成功了（同樣的陷阱寫在 1_今日任務.py）。
    with session_scope() as session:
        counts = repo.delete_user_data(session, user_id, drop_account=drop_account)

    st.success(
        "已刪除："
        f"{counts['plans']} 份計畫、"
        f"{counts['plan_items']} 個項目、"
        f"{counts['progress_events']} 筆事件紀錄、"
        f"{counts['task_completions']} 筆計畫外完成"
        + (f"、帳號 {counts['users']} 筆" if drop_account else "")
    )
    st.caption("重新整理這一頁就會看到更新後的數字。")


main()
