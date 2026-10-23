"""把 ``data/topics.yaml`` 與 ``data/tasks.csv`` 匯入資料庫。

可以重複執行：以 ``topics.id`` 與 ``tasks.url`` 當識別鍵做 upsert，
不會因為跑第二次而產生重複資料。

用法::

    python -m scripts.import_data            # 匯入
    python -m scripts.import_data --reset    # 先清空再匯入
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml
from sqlalchemy import func, select, text

# 讓這支檔案可以用 `python scripts/import_data.py` 直接執行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.models import Base, Task, Topic, User  # noqa: E402
from db.session import PROJECT_ROOT, get_engine, init_db, session_scope  # noqa: E402

TOPICS_PATH = PROJECT_ROOT / "data" / "topics.yaml"
TASKS_PATH = PROJECT_ROOT / "data" / "tasks.csv"

#: v0.1 的單一使用者。多使用者上線前，api/deps.py 固定回傳這個 id。
SEED_USER = {
    "id": 1,
    "email": "owner@localhost",
    "display_name": "我",
}


def load_topics() -> list[dict]:
    """讀 topics.yaml。"""
    with TOPICS_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, list):
        raise ValueError(f"{TOPICS_PATH} 的最外層應該是 list，讀到 {type(data)}")
    return data


def load_tasks() -> list[dict]:
    """讀 tasks.csv。"""
    with TASKS_PATH.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _check_topic_graph(topics: list[dict]) -> None:
    """匯入前先確認前置關係沒有指向不存在的主題。

    循環偵測由 ``core.planner._prereq_closure()`` 在排程時負責（會拋錯），
    這裡只攔最常犯的錯：主題改名之後 prereq 沒跟著改。
    """
    ids = {t["id"] for t in topics}
    for topic in topics:
        for prereq in topic.get("prereq") or []:
            if prereq not in ids:
                raise ValueError(f"主題 {topic['id']} 的前置 {prereq!r} 不存在")


def _check_key_points(topics: list[dict]) -> None:
    """每一條重點都必須是字串。

    YAML 裡沒加引號、中間有「冒號＋空白」的一行（``key=lambda x: (a, b)``）
    會被讀成 dict，不會報錯。它一路進到資料庫與計畫項目的快照，最後在今日任務頁
    ``"、".join(...)`` 的那一刻才炸——而且炸在交易裡，連重排與打勾都一起被 rollback。
    """
    for topic in topics:
        for point in topic.get("key_points") or []:
            if not isinstance(point, str):
                raise ValueError(
                    f"主題 {topic['id']} 的重點不是字串（YAML 裡有冒號的那行要加引號）：{point!r}"
                )


def _check_task_urls(tasks: list[dict]) -> None:
    """題目的 url 必須是乾淨的 http(s) 絕對網址。

    在**匯入**這一關檢查，而不是在顯示的時候：url 會被放進 markdown 連結
    （``[題目](url)``），而且之後可能出現在別的畫面。在入口擋一次，
    所有顯示的地方就都安全了；在顯示處擋則每多一個畫面就要多擋一次。

    會擋掉的東西：``javascript:`` 之類的 scheme、相對路徑，
    以及含有 ``)`` 這類會把 markdown 連結語法截斷的字元。
    """
    for row in tasks:
        url = (row.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(
                f"題目 {row.get('title')!r} 的 url 必須是 http(s) 絕對網址：{url!r}"
            )
        bad = [c for c in "()<>\" " if c in url]
        if bad:
            raise ValueError(
                f"題目 {row.get('title')!r} 的 url 含有會破壞 markdown 連結的"
                f"字元 {bad}：{url!r}"
            )


def import_all(reset: bool = False) -> dict[str, int]:
    """匯入主題與題庫，回傳各表的筆數。"""
    topics = load_topics()
    tasks = load_tasks()
    _check_topic_graph(topics)
    _check_key_points(topics)
    _check_task_urls(tasks)

    topic_ids = {t["id"] for t in topics}
    unknown = {t["topic"] for t in tasks} - topic_ids
    if unknown:
        raise ValueError(f"tasks.csv 用到地圖上沒有的主題：{sorted(unknown)}")

    if reset:
        Base.metadata.drop_all(get_engine())
        # 版本表也要清掉，否則 Alembic 以為已經是最新版，什麼表都不建
        with get_engine().begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    init_db()

    with session_scope() as session:
        # --- 使用者 ---
        if session.get(User, SEED_USER["id"]) is None:
            session.add(User(**SEED_USER))

        # --- 主題 ---
        for row in topics:
            topic = session.get(Topic, row["id"])
            values = {
                "name": row["name"],
                "domain": row.get("domain", "python"),
                "base_weight": int(row["base_weight"]),
                "prereq": list(row.get("prereq") or []),
                "key_points": list(row.get("key_points") or []),
            }
            if topic is None:
                session.add(Topic(id=row["id"], **values))
            else:
                for key, value in values.items():
                    setattr(topic, key, value)

        session.flush()

        # --- 題庫 ---
        for row in tasks:
            url = row["url"].strip()
            task = session.scalar(select(Task).where(Task.url == url))
            values = {
                "source": row["source"].strip(),
                "title": row["title"].strip(),
                "topic_id": row["topic"].strip(),
                "prereq": (row.get("prereq") or "").strip() or None,
                "difficulty": row["difficulty"].strip(),
                "est_minutes": int(row["est_minutes"]),
                "static_note": (row.get("static_note") or "").strip() or None,
                "focus_stage": int(row["focus"]) if (row.get("focus") or "").strip() else None,
            }
            if task is None:
                session.add(Task(url=url, **values))
            else:
                for key, value in values.items():
                    setattr(task, key, value)

    with session_scope() as session:
        return {
            "topics": session.scalar(select(func.count()).select_from(Topic)) or 0,
            "tasks": session.scalar(select(func.count()).select_from(Task)) or 0,
            "users": session.scalar(select(func.count()).select_from(User)) or 0,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="匯入主題與題庫")
    parser.add_argument(
        "--reset", action="store_true", help="先清空所有資料表再匯入"
    )
    args = parser.parse_args()

    counts = import_all(reset=args.reset)
    print("匯入完成：")
    for table, count in counts.items():
        print(f"  {table:12s} {count:4d} 筆")


if __name__ == "__main__":
    main()
