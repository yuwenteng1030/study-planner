"""共用依賴。

多使用者的唯一接縫就在這個檔案。

v0.1–v0.3 期間不實作登入，`get_current_user()` 固定回傳 user_id=1。
但所有資料表都已經帶 user_id、所有查詢都經過 repository 層，
所以開放多使用者時，**查詢與資料表**不用動。

但「只改這一支函式」低估了一件事（2026-10-23 檢視時更正）：Streamlit 沒有 request
物件可以傳進來，身分要從 ``st.user``（登入）或 ``st.session_state`` 讀，所以這支函式
會改成讀那兩者，並在沒登入時擋下頁面。額度（``api_usage``）、時區（``users.tz``）
都已經以 user_id 為單位存在資料庫，不需要另外改。
"""

# 單人模式下使用的固定使用者。開放登入後移除。
_SINGLE_USER_ID = 1


def get_current_user() -> int:
    """取得目前使用者的 user_id。

    所有 router 一律透過這支函式取得身分，不得直接寫死 user_id，
    也不得從 query string 或 request body 讀取 user_id
    （那會讓任何人都能存取別人的資料）。

    Returns:
        目前使用者的 user_id。
    """
    return _SINGLE_USER_ID
