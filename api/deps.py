"""共用依賴。

多使用者的唯一接縫就在這個檔案。

v0.1–v0.3 期間不實作登入，`get_current_user()` 固定回傳 user_id=1。
但所有資料表都已經帶 user_id、所有查詢都經過 repository 層，
因此 12 月要開放多使用者時，只需要改這一支函式（加上一個登入頁），
其餘程式碼完全不動。
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
