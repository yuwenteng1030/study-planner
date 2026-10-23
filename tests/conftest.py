"""測試共用設定。

**最重要的一件事**：在 import 任何 ``db.session`` 之前就把 ``DATABASE_URL``
指到暫存檔。``db/session.py`` 在 import 時就建立 engine，所以晚設沒用——
而測試如果連到專案根目錄的 ``study.db``，跑一次測試就會把實際的學習進度
洗掉一次。

pytest 會在載入測試模組之前先載入 conftest，所以這裡設定得及。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.planner import TaskInput, TopicNode

import csv
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOPICS_PATH = PROJECT_ROOT / "data" / "topics.yaml"
TASKS_PATH = PROJECT_ROOT / "data" / "tasks.csv"

# 必須在任何 db.session 的 import 之前執行。
_TMP_DIR = tempfile.mkdtemp(prefix="study-planner-test-")
_TEST_DB = Path(_TMP_DIR) / "ui-test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"

# 測試一律不呼叫外部服務。有 key 也拿掉，否則測試結果會依環境而變。
os.environ.pop("ANTHROPIC_API_KEY", None)


@pytest.fixture(scope="session")
def seeded_app_db() -> Path:
    """建好資料表並灌入真實的 topics.yaml 與 tasks.csv。

    給 UI 測試用：Streamlit 的頁面會自己去連 ``db.session``，
    沒辦法像單元測試那樣塞一個假的 session 進去。
    """
    from db.models import Task, Topic, User
    from db.session import SessionLocal, init_db

    # 走跟網站一樣的路：Alembic migration，不是 create_all
    init_db()

    with SessionLocal() as session:
        if session.query(Topic).count() == 0:
            for row in yaml.safe_load(
                (PROJECT_ROOT / "data" / "topics.yaml").read_text(encoding="utf-8")
            ):
                session.add(
                    Topic(
                        id=row["id"],
                        name=row["name"],
                        base_weight=int(row["base_weight"]),
                        prereq=list(row.get("prereq") or []),
                        key_points=list(row.get("key_points") or []),
                    )
                )
            with (PROJECT_ROOT / "data" / "tasks.csv").open(encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    session.add(
                        Task(
                            source=row["source"],
                            title=row["title"],
                            url=row["url"],
                            topic_id=row["topic"],
                            prereq=row["prereq"] or None,
                            difficulty=row["difficulty"],
                            est_minutes=int(row["est_minutes"]),
                            static_note=row["static_note"] or None,
                        )
                    )
            session.add(
                User(id=1, email="ui-test@example.com", display_name="UI 測試")
            )
            session.commit()

    return _TEST_DB


def seed_global_data(session) -> None:
    """灌入真實的 ``topics.yaml`` 與 ``tasks.csv``（全域資料，不含使用者）。

    刻意**不建** ``users`` 那一列：匯出與刪除的測試要自己控制帳號在不在，
    否則「查不到人要拋錯」那一條根本測不到。
    """
    from db.models import Task, Topic

    for row in yaml.safe_load(TOPICS_PATH.read_text(encoding="utf-8")):
        session.add(
            Topic(
                id=row["id"],
                name=row["name"],
                base_weight=int(row["base_weight"]),
                prereq=list(row.get("prereq") or []),
                key_points=list(row.get("key_points") or []),
            )
        )
    with TASKS_PATH.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            session.add(
                Task(
                    source=row["source"],
                    title=row["title"],
                    url=row["url"],
                    topic_id=row["topic"],
                    prereq=row["prereq"] or None,
                    difficulty=row["difficulty"],
                    est_minutes=int(row["est_minutes"]),
                    static_note=row["static_note"] or None,
                )
            )
    session.flush()


@pytest.fixture
def db_session(tmp_path: Path) -> Iterator["object"]:
    """每個測試一個乾淨的暫存資料庫，已灌好全域資料、沒有使用者。

    **外鍵檢查必須打開。** 刪除順序的測試完全依賴它：關掉的話順序寫錯
    只會留下指向空氣的孤兒列，測試照樣全綠——那正是這組測試要防的事。
    """
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from db.models import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'user-data.db'}", future=True)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as session:
        seed_global_data(session)
        session.commit()
        yield session


@pytest.fixture(scope="session")
def topics() -> list["TopicNode"]:
    """真實的 ``topics.yaml``（地圖與插畫的測試共用）。"""
    from core.planner import TopicNode

    rows = yaml.safe_load(TOPICS_PATH.read_text(encoding="utf-8"))
    return [
        TopicNode(
            id=r["id"],
            name=r["name"],
            base_weight=int(r["base_weight"]),
            prereq=tuple(r.get("prereq") or ()),
        )
        for r in rows
    ]


@pytest.fixture(scope="session")
def tasks() -> list["TaskInput"]:
    """真實的 ``tasks.csv``，id 照列序從 1 起。"""
    from core.planner import TaskInput

    with TASKS_PATH.open(encoding="utf-8") as handle:
        return [
            TaskInput(
                id=i,
                title=r["title"],
                topic_id=r["topic"],
                difficulty=r["difficulty"],
                est_minutes=int(r["est_minutes"]),
            )
            for i, r in enumerate(csv.DictReader(handle), start=1)
        ]
