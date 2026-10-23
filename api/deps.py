"""共用依賴。

多使用者的唯一接縫就在這個檔案。

v0.1–v0.3 期間不實作登入，`get_current_user()` 固定回傳 user_id=1。
但所有資料表都已經帶 user_id、所有查詢都經過 repository 層，
所以開放多使用者時，**查詢與資料表**不用動。

但「只改這一支函式」低估了一件事（2026-10-23 檢視時更正）：Streamlit 沒有 request
物件可以傳進來，身分要從 ``st.user``（登入）或 ``st.session_state`` 讀，所以這支函式
會改成讀那兩者，並在沒登入時擋下頁面。額度（``api_usage``）、時區（``users.tz``）
都已經以 user_id 為單位存在資料庫，不需要另外改。

**公開展示版**（2026-10-24）：環境變數 ``STUDY_PLANNER_DEMO=1`` 時，每個瀏覽器分頁
一個訪客帳號，存在 ``st.session_state``（見 ``db/demo.py``）。這正好驗證了上面那段：
換身分來源只改了這支函式，頁面與查詢一行都沒動。
"""

from __future__ import annotations

import os

# 單人模式下使用的固定使用者。開放登入後移除。
_SINGLE_USER_ID = 1

#: 打開公開展示版的環境變數。Streamlit Community Cloud 的 secrets 最上層的鍵也會變成環境變數。
DEMO_ENV = "STUDY_PLANNER_DEMO"

#: 訪客帳號 id 存在 session_state 的哪個鍵。
_VISITOR_KEY = "visitor_user_id"


def is_demo() -> bool:
    """是不是公開展示版。"""
    return os.environ.get(DEMO_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def get_current_user() -> int:
    """取得目前使用者的 user_id。

    所有 router 一律透過這支函式取得身分，不得直接寫死 user_id，
    也不得從 query string 或 request body 讀取 user_id
    （那會讓任何人都能存取別人的資料）。

    展示版的訪客 id 只放在伺服器端的 ``session_state``，不放網址：放網址的話，
    把網址貼給別人就等於把自己的帳號交出去。代價是重新整理頁面會換一個新訪客。

    Returns:
        目前使用者的 user_id。
    """
    if not is_demo():
        return _SINGLE_USER_ID

    import streamlit as st

    from db.session import session_scope

    user_id = st.session_state.get(_VISITOR_KEY)
    with session_scope() as session:
        from core import clock
        from db import demo
        from db.models import User

        # 分頁開太久，帳號可能已經被當成過期訪客清掉了：換一個新的，不要讓頁面撞外鍵
        if user_id is None or session.get(User, user_id) is None:
            demo.purge_stale_visitors(session)
            user_id = demo.create_visitor(session, clock.today_in(clock.DEFAULT_TZ))
            st.session_state[_VISITOR_KEY] = user_id
    return user_id


def show_demo_notice() -> None:
    """展示版在側邊欄說明這是什麼、資料留多久。單人模式什麼都不畫。"""
    if not is_demo():
        return
    import streamlit as st

    from db.demo import VISITOR_TTL

    get_current_user()  # 先把訪客帳號建好，頁面接下來讀到的就是有範例資料的帳號
    hours = int(VISITOR_TTL.total_seconds() // 3600)
    st.sidebar.info(
        "🎈 **公開展示版**\n\n"
        "你拿到一個自己的訪客帳號，裡面已經有一份進行到第 4 天的範例計畫"
        "（昨天漏了一題，看看它被排到哪裡）。隨便點、隨便刪都不會影響別人。\n\n"
        f"資料保留 {hours} 小時；重新整理頁面會換一個新帳號。"
        "說明文字用預存的，不呼叫 Claude。"
    )
