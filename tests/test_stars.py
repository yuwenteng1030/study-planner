"""學習地圖的星等：對應考卷（兩題 Medium＋一題 Hard），只顯示不擋路。"""

from __future__ import annotations

from core.game_map import Level


def _lv(total, done=None):
    return Level(topic_id="t", name="T", zone=1, prereq=(), total=total, done=done or {})


def test_one_star_needs_three_easy_not_all_easy() -> None:
    total = {"Easy": 7, "Medium": 2, "Hard": 1}
    assert _lv(total, {"Easy": 2}).stars == 0
    assert _lv(total, {"Easy": 3}).stars == 1


def test_fewer_easy_than_threshold_means_all_of_them() -> None:
    assert _lv({"Easy": 1, "Medium": 4}, {"Easy": 1}).stars == 1


def test_no_easy_topic_gets_one_star_from_a_medium() -> None:
    assert _lv({"Medium": 4}, {"Medium": 1}).stars == 1


def test_two_stars_at_half_the_medium() -> None:
    total = {"Easy": 3, "Medium": 4, "Hard": 1}
    assert _lv(total, {"Easy": 3, "Medium": 1}).stars == 1
    assert _lv(total, {"Easy": 3, "Medium": 2}).stars == 2


def test_three_stars_need_every_hard() -> None:
    total = {"Easy": 3, "Medium": 2, "Hard": 2}
    assert _lv(total, {"Easy": 3, "Medium": 1, "Hard": 1}).stars == 2
    assert _lv(total, {"Easy": 3, "Medium": 1, "Hard": 2}).stars == 3


def test_without_hard_three_stars_mean_all_medium() -> None:
    total = {"Easy": 5, "Medium": 4}
    assert _lv(total, {"Easy": 3, "Medium": 3}).stars == 2
    assert _lv(total, {"Easy": 3, "Medium": 4}).stars == 3


def test_stars_cannot_skip_a_level() -> None:
    # Hard 全做了，但 Easy 沒做：還是 0 星
    assert _lv({"Easy": 3, "Medium": 2, "Hard": 1}, {"Hard": 1}).stars == 0


def test_concept_only_topic_has_no_stars() -> None:
    assert _lv({}).stars == 0
