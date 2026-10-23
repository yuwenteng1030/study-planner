"""Claude 呼叫的每日額度，存在 ``api_usage`` 表。

原本存在記憶體：伺服器重開就歸零，多個 worker 各算各的。現在每次預留都寫進資料庫，
「每天」照使用者的時區算（跟畫面上的「今天」同一天）。

每次預留／退回都開**自己的**短交易：呼叫端是在等 Claude 回覆的前後呼叫它，
不能借用頁面的交易——那會在等網路時一直抓著 SQLite 的寫入鎖。
"""

from __future__ import annotations

from sqlalchemy import case, update
from sqlalchemy.exc import IntegrityError

from db import repositories as repo
from db.models import ApiUsage
from db.session import session_scope

#: 兩個請求同時搶同一列時，比較後寫入（compare-and-set）失敗就重試幾次。
_RETRIES = 5


class DbLedger:
    """``llm.client.QuotaLedger`` 的資料庫版。"""

    def reserve(self, user_id: int, kind: str, wanted: int, limit: int) -> int:
        if wanted <= 0:
            return 0
        for _ in range(_RETRIES):
            try:
                with session_scope() as session:
                    day = repo.user_today(session, user_id)
                    row = session.get(ApiUsage, (user_id, day, kind))
                    if row is None:
                        row = ApiUsage(user_id=user_id, day=day, kind=kind, count=0)
                        session.add(row)
                        session.flush()  # 另一個請求剛建了同一列 → IntegrityError → 重試
                    current = row.count
                    granted = max(0, min(wanted, limit - current))
                    if granted == 0:
                        return 0
                    # 只有在「我讀到的數字」還沒被別人改過時才寫入
                    changed = session.execute(
                        update(ApiUsage)
                        .where(
                            ApiUsage.user_id == user_id,
                            ApiUsage.day == day,
                            ApiUsage.kind == kind,
                            ApiUsage.count == current,
                        )
                        .values(count=current + granted)
                        .execution_options(synchronize_session=False)
                    ).rowcount
                    if changed == 1:
                        return granted
            except IntegrityError:
                continue
        # 一直搶不到就當作沒有額度：退回預存文字，不會因此出錯
        return 0

    def refund(self, user_id: int, kind: str, count: int) -> None:
        if count <= 0:
            return
        with session_scope() as session:
            day = repo.user_today(session, user_id)
            # 一句 UPDATE 在資料庫裡算，不先讀出來再寫回：中間別人的預留不會被覆蓋掉
            session.execute(
                update(ApiUsage)
                .where(ApiUsage.user_id == user_id, ApiUsage.day == day, ApiUsage.kind == kind)
                .values(
                    count=case((ApiUsage.count > count, ApiUsage.count - count), else_=0)
                )
                .execution_options(synchronize_session=False)
            )
