"""學習地圖：分區、題數統計、跨計畫的完成紀錄。

分區的期望值是**手寫**的，來自提案文件裡照 ``topics.yaml`` 手畫的四區圖，
不是拿 ``zones()`` 自己的輸出回頭比——那樣它算錯了測試也會跟著錯。
"""

from __future__ import annotations

import csv
import datetime as _dt
from pathlib import Path

import pytest
import yaml

from core.game_map import build_map, zones
from core.planner import TaskInput, TopicNode, build_plan
from core.scoring import JDStats, UserContext
from db import repositories as repo
from db.models import User

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOPICS_PATH = PROJECT_ROOT / "data" / "topics.yaml"
TASKS_PATH = PROJECT_ROOT / "data" / "tasks.csv"


@pytest.fixture(scope="module")
def topics() -> list[TopicNode]:
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


@pytest.fixture(scope="module")
def tasks() -> list[TaskInput]:
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


# ---------------------------------------------------------------------------
# 分區
# ---------------------------------------------------------------------------


def test_real_map_splits_into_the_four_hand_drawn_zones(topics) -> None:
    expected = {
        0: {"python_basic"},
        1: {"time_complexity", "arrays", "strings", "linked_lists", "recursion",
            "bit_manipulation", "math_number_theory", "misc"},
        2: {"hashmaps", "sorting", "stacks_queues", "trees", "dp_1d", "two_d_arrays"},
        3: {"search", "greedy", "heaps", "graphs", "dp_2d", "tries"},
    }
    got: dict[int, set[str]] = {}
    for topic_id, zone in zones(topics).items():
        got.setdefault(zone, set()).add(topic_id)
    assert got == expected


def test_zone_uses_the_longest_prereq_chain() -> None:
    """``c`` 同時接在 ``a``（第 0 區）與 ``b``（第 1 區）後面，必須在第 2 區。

    取最短鏈的話會變成第 1 區，跟它自己的前置 ``b`` 擠在同一區。
    """
    nodes = [
        TopicNode(id="a", name="A", base_weight=1),
        TopicNode(id="b", name="B", base_weight=1, prereq=("a",)),
        TopicNode(id="c", name="C", base_weight=1, prereq=("a", "b")),
    ]
    assert zones(nodes) == {"a": 0, "b": 1, "c": 2}


def test_cycle_is_an_error_not_a_map() -> None:
    nodes = [
        TopicNode(id="a", name="A", base_weight=1, prereq=("b",)),
        TopicNode(id="b", name="B", base_weight=1, prereq=("a",)),
    ]
    with pytest.raises(ValueError, match="循環"):
        zones(nodes)


def test_unknown_prereq_is_ignored() -> None:
    """跟排程器一樣：指向不存在主題的前置不算數。"""
    nodes = [TopicNode(id="a", name="A", base_weight=1, prereq=("ghost",))]
    assert zones(nodes) == {"a": 0}


# ---------------------------------------------------------------------------
# 題數
# ---------------------------------------------------------------------------


def test_every_task_is_counted_exactly_once(topics, tasks) -> None:
    levels = build_map(topics, tasks, done_task_ids=set())
    assert sum(lv.total_count for lv in levels) == len(tasks)
    assert sum(lv.done_count for lv in levels) == 0


def test_done_tasks_land_in_their_topic_and_difficulty(topics, tasks) -> None:
    hard_arrays = next(t for t in tasks if t.topic_id == "arrays" and t.difficulty == "Hard")
    easy_trees = next(t for t in tasks if t.topic_id == "trees" and t.difficulty == "Easy")

    levels = {lv.topic_id: lv for lv in build_map(topics, tasks, {hard_arrays.id, easy_trees.id})}

    assert levels["arrays"].done == {"Hard": 1}
    assert levels["trees"].done == {"Easy": 1}
    assert sum(lv.done_count for lv in levels.values()) == 2


def test_time_complexity_is_drawn_as_a_concept_level(topics, tasks) -> None:
    levels = {lv.topic_id: lv for lv in build_map(topics, tasks, set())}
    assert levels["time_complexity"].is_concept_only
    assert not levels["sorting"].is_concept_only


def test_levels_come_out_zone_by_zone_most_important_first(topics, tasks) -> None:
    """區內照 base_weight 由高到低，不照輸入順序。

    輸入刻意倒過來餵：資料庫讀出來是 id 字母序，曾經就這樣直接畫上畫面
    （``arrays``、``bit_manipulation``、``linked_lists``……），測試卻因為
    直接讀 yaml 而沒發現。
    """
    levels = build_map(list(reversed(topics)), tasks, set())
    assert [lv.zone for lv in levels] == sorted(lv.zone for lv in levels)
    assert levels[0].topic_id == "python_basic"

    zone1 = [lv.topic_id for lv in levels if lv.zone == 1]
    # arrays 9 > strings 8 > time_complexity 7 > 其餘
    assert zone1[:3] == ["arrays", "strings", "time_complexity"]


def test_equal_weight_levels_fall_back_to_id_order() -> None:
    """同區、同權重時照 id 排，不照輸入順序——否則畫面順序會隨資料庫而變。"""
    nodes = [
        TopicNode(id="root", name="R", base_weight=1),
        TopicNode(id="zeta", name="Z", base_weight=5, prereq=("root",)),
        TopicNode(id="alpha", name="A", base_weight=5, prereq=("root",)),
    ]
    assert [lv.topic_id for lv in build_map(nodes, [], set())] == ["root", "alpha", "zeta"]


# ---------------------------------------------------------------------------
# 完成紀錄：跨所有計畫，但不跨使用者
# ---------------------------------------------------------------------------


def _plan(session, user_id: int):
    today = _dt.date.today()
    core_plan = build_plan(
        topics=repo.load_topic_nodes(session),
        tasks=repo.load_task_inputs(session),
        start_date=today,
        days=3,
        daily_minutes=60,
        user=UserContext(),
        jd=JDStats(),
    )
    return repo.create_plan(
        session,
        user_id=user_id,
        goal_text="地圖測試",
        parsed_goal={},
        start_date=today,
        days=3,
        daily_minutes=60,
        core_plan=core_plan,
    )


def test_completed_tasks_survive_making_a_new_plan(db_session) -> None:
    """重排一份新計畫（舊的會被收起來），地圖上做過的題目不能歸零。"""
    session = db_session
    session.add_all([User(id=1, email="a@example.com", display_name="A"), User(id=2, email="b@example.com", display_name="B")])
    session.flush()

    first = _plan(session, 1)
    item = first.items[0]
    repo.mark_item_done(session, item)
    session.flush()

    _plan(session, 1)  # 收起第一份
    session.flush()
    assert repo.load_active_plan(session, 1).id != first.id

    assert repo.load_completed_task_ids_for_user(session, 1) == {item.task_id}


def test_other_users_progress_is_not_counted(db_session) -> None:
    session = db_session
    session.add_all([User(id=1, email="a@example.com", display_name="A"), User(id=2, email="b@example.com", display_name="B")])
    session.flush()

    other = _plan(session, 2)
    repo.mark_item_done(session, other.items[0])
    session.flush()

    assert repo.load_completed_task_ids_for_user(session, 1) == set()
    assert repo.load_completed_task_ids_for_user(session, 2) == {other.items[0].task_id}


def test_unchecked_and_skipped_items_are_not_completed(db_session) -> None:
    session = db_session
    session.add(User(id=1, email="a@example.com", display_name="A"))
    session.flush()

    plan = _plan(session, 1)
    done, undone, skipped = plan.items[:3]
    repo.mark_item_done(session, done)
    repo.mark_item_done(session, undone)
    repo.mark_item_undone(session, undone)
    skipped.status = "skipped"
    session.flush()

    assert repo.load_completed_task_ids_for_user(session, 1) == {done.task_id}
