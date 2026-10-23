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
from sqlalchemy.orm import Session, sessionmaker


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
    """把資料庫升到最新的 schema（Alembic ``upgrade head``）。空資料庫會從頭建起。"""
    upgrade_to_head()


#: 導入 Alembic 那一版的 revision。導入前就存在的資料庫直接標成這一版。
BASELINE_REVISION = "0001"


def _alembic_config():  # noqa: ANN202
    from alembic.config import Config

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    # 用絕對路徑：從別的工作目錄啟動（例如 streamlit run app/Home.py）也找得到
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "db" / "migrations"))
    return cfg


def upgrade_to_head() -> None:
    """跑完所有還沒跑的 migration。兩個行程同時啟動、其中一個撞到對方剛建的表時，重試一次。"""
    from sqlalchemy.exc import OperationalError

    for attempt in range(2):
        try:
            _upgrade_once()
            return
        except OperationalError as exc:
            # 另一個行程剛好在同時建表（already exists）或還抓著寫入鎖（locked）
            transient = "already exists" in str(exc) or "locked" in str(exc)
            if attempt == 1 or not transient:
                raise
            import time

            time.sleep(0.5)


def _upgrade_once() -> None:
    """導入 Alembic（2026-10-23）以前建的資料庫沒有 ``alembic_version`` 表：
    補上當時唯一一個手動加過的欄位，標成 baseline，再往上升。
    用 ``create_all`` 建好的資料庫（測試）本來就是最新的樣子，直接標成最新版。

    SQLite 跑 migration 時**暫時關掉外鍵檢查**：batch 模式改欄位的做法是「建新表、
    搬資料、刪舊表」，外鍵開著的話刪舊表那一步會被擋下（例如 downgrade 拿掉 ``users.tz``）。
    PRAGMA 在交易裡改不了，所以要在 ``begin()`` 之前下，結束後再打開。
    """
    from alembic import command
    from alembic.script import ScriptDirectory
    from sqlalchemy import inspect, text

    cfg = _alembic_config()
    is_sqlite = _engine.dialect.name == "sqlite"
    with _engine.connect() as conn:
        if is_sqlite:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            conn.commit()  # exec 會自動開一個交易，先結束它，下面的 begin() 才開得起來
        try:
            with conn.begin():
                cfg.attributes["connection"] = conn
                inspector = inspect(conn)
                tables = set(inspector.get_table_names())
                if tables and "alembic_version" not in tables:
                    user_columns = (
                        {c["name"] for c in inspector.get_columns("users")} if "users" in tables else set()
                    )
                    if "tz" in user_columns:
                        command.stamp(cfg, ScriptDirectory.from_config(cfg).get_current_head())
                    else:
                        if "plan_items" in tables:
                            item_columns = {c["name"] for c in inspector.get_columns("plan_items")}
                            if "reason_source" not in item_columns:
                                conn.execute(text("ALTER TABLE plan_items ADD COLUMN reason_source VARCHAR(10)"))
                        command.stamp(cfg, BASELINE_REVISION)
                command.upgrade(cfg, "head")
        finally:
            if is_sqlite:
                # 連線會回到連線池，下一個人拿到的必須是外鍵開著的
                conn.exec_driver_sql("PRAGMA foreign_keys=ON")
                conn.commit()


#: 這個行程是否已經升級過 schema。Streamlit 每個瀏覽器分頁一條執行緒，所以要上鎖：
#: 兩條同時第一次開交易，會各跑一次 migration。
_schema_ready = False
_schema_lock = threading.Lock()


def _ensure_schema() -> None:
    """第一次開交易時把資料庫升到最新版（每個行程一次）。

    原本用 ``create_all`` 加一份手寫的 ALTER 清單：只能加可為 NULL 的欄位，改不了
    約束與索引，也沒有版本紀錄。2026-10-23 改用 Alembic，schema 變更一律寫 migration。
    """
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if not _schema_ready:
            upgrade_to_head()
            _sync_catalog()
            _schema_ready = True


def _sync_catalog() -> None:
    """題庫是空的就從 ``data/`` 匯入；展示版每次啟動都重新同步一次。

    為什麼放在這裡：部署到新機器（或剛 clone 下來）時資料庫是空的，原本要先記得跑
    ``python -m scripts.import_data``，忘了就看到「題庫是空的」。展示版的資料庫跟著
    雲端主機留著，題庫更新（改了 tasks.csv）後沒人會去手動匯入，所以每次啟動都 upsert。
    本機的正式資料庫已經有題庫，這裡什麼都不做，題庫更新照舊手動匯入。
    """
    from sqlalchemy import func, select

    from api.deps import is_demo
    from db.models import Task
    from scripts.import_data import upsert_catalog

    from sqlalchemy.exc import IntegrityError, OperationalError

    for attempt in range(2):
        try:
            with SessionLocal() as session, session.begin():
                empty = not session.scalar(select(func.count()).select_from(Task))
                if empty or is_demo():
                    upsert_catalog(session)
            return
        except (IntegrityError, OperationalError):
            # 兩個行程同時從空資料庫啟動：另一個剛好先寫進同一批主題（撞主鍵）或還抓著寫入鎖。
            # 等一下重來一次；那時題庫多半已經在了，upsert 只會更新、不會再插入
            if attempt == 1:
                raise
            import time

            time.sleep(0.5)


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
