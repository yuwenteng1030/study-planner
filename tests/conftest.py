"""測試共用設定。

**最重要的一件事**：在 import 任何 ``db.session`` 之前就把 ``DATABASE_URL``
指到暫存檔。``db/session.py`` 在 import 時就建立 engine，所以晚設沒用——
而測試如果連到專案根目錄的 ``study.db``，跑一次測試就會把實際的學習進度
洗掉一次。

pytest 會在載入測試模組之前先載入 conftest，所以這裡設定得及。
"""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

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
    from db.models import Base, Task, Topic, User
    from db.session import SessionLocal, get_engine

    Base.metadata.create_all(get_engine())

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
