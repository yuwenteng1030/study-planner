"""資料庫連線與 session 工廠。

這裡是「換資料庫只改一層」的其中一半：連線字串集中在這個檔案，
查詢語句集中在 ``db/repositories.py``。之後換 PostgreSQL 時，
把 ``DATABASE_URL`` 換掉就好，上層程式不動。
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base

#: 專案根目錄（本檔案的上一層的上一層）。
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DB_PATH = PROJECT_ROOT / "study.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")

_engine: Engine = create_engine(DATABASE_URL, future=True)

#: SQLite 預設不檢查外鍵，要每條連線各自開啟。
#: 不開的話 plan_items.task_id 指到不存在的題目也不會報錯。
if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


def get_engine() -> Engine:
    """取得 engine。測試要替換連線時用得到。"""
    return _engine


def init_db() -> None:
    """建立所有資料表（已存在的不動）。"""
    Base.metadata.create_all(_engine)


#: 這個行程是否已經補過缺的資料表。Streamlit 每個瀏覽器分頁一條執行緒，所以要上鎖：
#: 兩條同時第一次開交易，SQLite 會讓其中一條撞上 ``table already exists``。
_schema_ready = False
_schema_lock = threading.Lock()


#: 既有資料庫補欄位用：``(表, 欄位, SQL 型別)``。只能加可為 NULL 的欄位。
_ADDED_COLUMNS = [("plan_items", "reason_source", "VARCHAR(10)")]


def _add_missing_columns() -> None:
    """``create_all`` 不會改既有的表，所以新欄位在這裡用 ALTER TABLE 補。"""
    from sqlalchemy import inspect, text

    inspector = inspect(_engine)
    with _engine.begin() as conn:
        for table, column, sql_type in _ADDED_COLUMNS:
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column not in existing:
                try:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
                except OperationalError as exc:
                    # 另一個行程剛好先補上了：不是錯誤
                    if "duplicate column" not in str(exc).lower():
                        raise


def _ensure_schema() -> None:
    """第一次開交易時補上缺的資料表。

    新功能加了新表（例如 ``task_completions``）之後，既有的 ``study.db`` 裡沒有它，
    頁面一查就會炸 ``no such table``。``create_all`` 只建**不存在**的表、不改既有
    的表與資料，所以每個行程跑一次是安全的；要改既有欄位時這招不夠，得寫 migration。
    """
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if not _schema_ready:
            init_db()
            _add_missing_columns()
            _schema_ready = True


@contextmanager
def session_scope() -> Iterator[Session]:
    """提供一個交易範圍：正常結束就 commit，出錯就 rollback。

    用法::

        with session_scope() as session:
            session.add(obj)
    """
    _ensure_schema()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
