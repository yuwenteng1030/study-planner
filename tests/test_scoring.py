"""``core/scoring.py`` 的測試，含 ``data/golden_cases.yaml`` 的迴歸案例。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.scoring import (
    GOAL_FACTOR_HIT,
    JD_FACTOR_MAX,
    JD_FACTOR_MIN,
    TIER_BONUS,
    TIER_MUST,
    TIER_RECOMMENDED,
    JDStats,
    TopicInput,
    UserContext,
    rank_topics,
    score_topic,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOPICS_PATH = PROJECT_ROOT / "data" / "topics.yaml"
GOLDEN_PATH = PROJECT_ROOT / "data" / "golden_cases.yaml"


@pytest.fixture(scope="module")
def topics() -> list[TopicInput]:
    """真實的知識地圖。黃金案例要跑在真資料上才有意義。"""
    raw = yaml.safe_load(TOPICS_PATH.read_text(encoding="utf-8"))
    return [
        TopicInput(id=t["id"], name=t["name"], base_weight=int(t["base_weight"]))
        for t in raw
    ]


@pytest.fixture(scope="module")
def golden_cases() -> list[dict]:
    return yaml.safe_load(GOLDEN_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 單一因子
# ---------------------------------------------------------------------------


def test_no_jd_gives_factor_one() -> None:
    """冷啟動的硬要求：沒有 JD 時 jd_factor 必須是 1.0，不是 0。"""
    topic = TopicInput(id="arrays", name="Arrays", base_weight=9)
    result = score_topic(topic, UserContext(), JDStats())
    assert result.jd_factor == JD_FACTOR_MIN == 1.0
    assert result.score == 9.0


def test_jd_factor_is_capped_at_two() -> None:
    """每份 JD 都提到也只能是 2.0，不能無上限成長。"""
    topic = TopicInput(id="arrays", name="Arrays", base_weight=9)
    jd = JDStats(total=10, hits={"arrays": 10})
    assert score_topic(topic, UserContext(), jd).jd_factor == JD_FACTOR_MAX


def test_jd_factor_cannot_overturn_base_weight() -> None:
    """jd_factor 上限 2.0 的理由：不該讓冷門主題贏過核心主題。

    base_weight 3 吃滿 JD 加權（3 × 2.0 = 6.0）仍然要輸給
    base_weight 8 完全沒被提到（8 × 1.0 = 8.0）。

    注意：只把 ``JD_FACTOR_MAX`` 改成 3.0，這個測試**不會**紅——
    ``1 + ratio`` 本來就到不了 3，夾鉗從未生效。只改公式（例如 ``1 + 2 × ratio``）
    也不會紅——夾鉗會開始生效，上限仍是 2。要公式**和**上限一起放寬才會紅。
    見 README 設計決策 2。
    """
    niche = TopicInput(id="niche", name="冷門", base_weight=3)
    core = TopicInput(id="core", name="核心", base_weight=8)
    jd = JDStats(total=5, hits={"niche": 5})
    assert score_topic(niche, UserContext(), jd).score < score_topic(
        core, UserContext(), jd
    ).score


def test_familiar_lowers_but_never_zeroes() -> None:
    """已熟的主題分數要降，但不能變 0——否則永遠排不到複習。"""
    topic = TopicInput(id="arrays", name="Arrays", base_weight=9)
    user = UserContext(levels={"arrays": "familiar"})
    result = score_topic(topic, user, JDStats())
    assert 0 < result.score < 9.0


def test_unknown_level_falls_back_to_normal() -> None:
    """自評程度填了沒見過的值，要當成 normal 而不是爆掉。"""
    topic = TopicInput(id="arrays", name="Arrays", base_weight=9)
    user = UserContext(levels={"arrays": "???"})
    assert score_topic(topic, user, JDStats()).gap_factor == 1.0


def test_goal_hit_applies_boost() -> None:
    topic = TopicInput(id="arrays", name="Arrays", base_weight=9)
    user = UserContext(goal_topics=frozenset({"arrays"}))
    assert score_topic(topic, user, JDStats()).goal_factor == GOAL_FACTOR_HIT


# ---------------------------------------------------------------------------
# 分級
# ---------------------------------------------------------------------------


def test_tiers_cover_everything_exactly_once(topics: list[TopicInput]) -> None:
    ranked = rank_topics(topics)
    assert len(ranked) == len(topics)
    assert {r.priority for r in ranked} <= {TIER_MUST, TIER_RECOMMENDED, TIER_BONUS}
    assert len({r.topic_id for r in ranked}) == len(topics)


def test_tier_proportions(topics: list[TopicInput]) -> None:
    """21 個主題 → 必學 8、建議 7、加分 6。"""
    ranked = rank_topics(topics)
    counts = {tier: 0 for tier in (TIER_MUST, TIER_RECOMMENDED, TIER_BONUS)}
    for item in ranked:
        counts[item.priority] += 1
    assert counts == {TIER_MUST: 8, TIER_RECOMMENDED: 7, TIER_BONUS: 6}


def test_tiers_are_monotonic(topics: list[TopicInput]) -> None:
    """必學層的最低分不能低於建議層的最高分。"""
    ranked = rank_topics(topics)
    must = [r.score for r in ranked if r.priority == TIER_MUST]
    rec = [r.score for r in ranked if r.priority == TIER_RECOMMENDED]
    bonus = [r.score for r in ranked if r.priority == TIER_BONUS]
    assert min(must) >= max(rec)
    assert min(rec) >= max(bonus)


def test_single_topic_still_gets_one_must() -> None:
    """只有一個主題時也要有必學，不能整張清單都是加分。"""
    ranked = rank_topics([TopicInput(id="only", name="唯一", base_weight=5)])
    assert [r.priority for r in ranked] == [TIER_MUST]


def test_empty_input_returns_empty() -> None:
    assert rank_topics([]) == []


def test_ranking_is_deterministic_under_input_shuffle(
    topics: list[TopicInput],
) -> None:
    """輸入順序改變不能影響結果。

    資料庫回傳順序是不保證的，少了這個性質，golden case 會時綠時紅。
    """
    forward = [(r.topic_id, r.priority) for r in rank_topics(topics)]
    backward = [(r.topic_id, r.priority) for r in rank_topics(list(reversed(topics)))]
    assert forward == backward


def test_tie_break_is_by_topic_id() -> None:
    """分數相同時按 id 字典序，結果可重現。"""
    same = [
        TopicInput(id="zebra", name="Z", base_weight=5),
        TopicInput(id="alpha", name="A", base_weight=5),
    ]
    assert [r.topic_id for r in rank_topics(same)] == ["alpha", "zebra"]


# ---------------------------------------------------------------------------
# 黃金案例
# ---------------------------------------------------------------------------


def _build_inputs(case: dict) -> tuple[UserContext, JDStats]:
    user_raw = case.get("user") or {}
    jd_raw = case.get("jd") or {}
    user = UserContext(
        levels=dict(user_raw.get("levels") or {}),
        goal_topics=frozenset(user_raw.get("goal_topics") or []),
    )
    jd = JDStats(
        total=int(jd_raw.get("total") or 0),
        hits=dict(jd_raw.get("hits") or {}),
    )
    return user, jd


def test_golden_cases_file_is_usable(golden_cases: list[dict]) -> None:
    assert golden_cases, "golden_cases.yaml 不能是空的"
    assert len({c["id"] for c in golden_cases}) == len(golden_cases), "案例 id 重複"


@pytest.mark.parametrize(
    "case",
    yaml.safe_load(GOLDEN_PATH.read_text(encoding="utf-8")),
    ids=lambda c: c["id"],
)
def test_golden_case(case: dict, topics: list[TopicInput]) -> None:
    user, jd = _build_inputs(case)
    ranked = rank_topics(topics, user, jd)
    by_id = {r.topic_id: r for r in ranked}
    must = [r.topic_id for r in ranked if r.priority == TIER_MUST]
    expect = case["expect"]

    if "must_exactly" in expect:
        assert sorted(must) == sorted(expect["must_exactly"]), (
            f"{case['id']}：必學層應為 {sorted(expect['must_exactly'])}，"
            f"實際為 {sorted(must)}"
        )

    for topic_id in expect.get("in_must", []):
        assert topic_id in must, f"{case['id']}：{topic_id} 應該在必學層"

    for topic_id in expect.get("not_in_must", []):
        assert topic_id not in must, f"{case['id']}：{topic_id} 不該在必學層"

    if "all_jd_factor_equals" in expect:
        target = float(expect["all_jd_factor_equals"])
        assert all(r.jd_factor == target for r in ranked)

    if "max_jd_factor_equals" in expect:
        target = float(expect["max_jd_factor_equals"])
        assert max(r.jd_factor for r in ranked) == target

    if "min_score_above" in expect:
        floor = float(expect["min_score_above"])
        assert min(r.score for r in ranked) > floor

    for higher, lower in expect.get("ranks_above", []):
        assert by_id[higher].score > by_id[lower].score, (
            f"{case['id']}：{higher} 應該排在 {lower} 前面"
        )
