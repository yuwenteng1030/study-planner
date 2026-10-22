"""資料庫連線與 session 工廠。

這裡是「換資料庫只改一層」的其中一半：連線字串集中在這個檔案，
查詢語句集中在 ``db/repositories.py``。之後換 PostgreSQL 時，
把 ``DATABASE_URL`` 換掉就好，上層程式不動。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
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


@contextmanager
def session_scope() -> Iterator[Session]:
    """提供一個交易範圍：正常結束就 commit，出錯就 rollback。

    用法::

        with session_scope() as session:
            session.add(obj)
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
