"""學習地圖插畫：座標規則與 SVG 內容。

座標從 ``layout`` 直接驗，不去解析畫出來的路徑；SVG 只驗「是合法 XML、
每一關都在、狀態標記對」——顏色、大小這類純外觀不寫死在測試裡。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from core.game_map import Level, build_map
from core.map_art import (
    COLUMNS,
    NODE_R,
    PER_ROW,
    WIDTH,
    _label_x,
    _wrap,
    current_level,
    layout,
    render_svg,
    zone_theme,
)

NS = {"svg": "http://www.w3.org/2000/svg"}


def _lv(topic_id: str, zone: int, total: int = 2, done: int = 0, name: str | None = None) -> Level:
    return Level(
        topic_id=topic_id,
        name=name or topic_id.upper(),
        zone=zone,
        prereq=(),
        total={"Easy": total} if total else {},
        done={"Easy": done} if done else {},
    )


def _sample() -> list[Level]:
    """起點 1 關、第一區 5 關（兩排）、第二區 2 關，跟真實地圖一樣有不滿的排。"""
    return [
        _lv("start", 0),
        *(_lv(f"a{i}", 1) for i in range(5)),
        _lv("b0", 2),
        _lv("b1", 2),
    ]


def _parse(svg: str) -> ET.Element:
    return ET.fromstring(svg)


# ---------------------------------------------------------------------------
# 版面
# ---------------------------------------------------------------------------


def test_every_level_is_placed_once_in_path_order() -> None:
    levels = _sample()
    placed, _, _ = layout(levels)
    assert [p.level.topic_id for p in placed] == [lv.topic_id for lv in levels]


def test_later_zones_are_drawn_higher_up() -> None:
    """由下往上走：後面的區，每一關都在前面所有區的上方（y 比較小）。"""
    placed, bands, height = layout(_sample())
    by_zone: dict[int, list[float]] = {}
    for p in placed:
        by_zone.setdefault(p.level.zone, []).append(p.y)
    assert max(by_zone[1]) < min(by_zone[0])
    assert max(by_zone[2]) < min(by_zone[1])

    assert bands[0].bottom == height and bands[-1].top == 0
    for b in bands:
        ys = by_zone[b.zone]
        assert b.top < min(ys) and max(ys) < b.bottom


def test_rows_hold_at_most_per_row_levels() -> None:
    placed, _, _ = layout(_sample())
    rows: dict[float, int] = {}
    for p in placed:
        rows[p.y] = rows.get(p.y, 0) + 1
    assert max(rows.values()) <= PER_ROW


def test_next_row_starts_on_the_side_where_the_last_row_ended() -> None:
    """S 形折返：換排時橫向位移不超過一欄，小路不會斜著橫跨整張圖。

    起點那排只有一關（最左），所以第一區第一排要從左邊開始，而不是右邊。
    """
    placed, _, _ = layout(_sample())
    step = COLUMNS[1] - COLUMNS[0]
    for prev, nxt in zip(placed, placed[1:]):
        if nxt.y != prev.y:
            assert abs(nxt.x - prev.x) <= step + 1, (prev.level.topic_id, nxt.level.topic_id)


def test_all_nodes_fit_inside_the_picture() -> None:
    placed, _, height = layout(_sample())
    for p in placed:
        assert 0 < p.x < WIDTH and 0 < p.y < height


def test_zone_label_stays_clear_of_the_path_leaving_the_zone(topics, tasks) -> None:
    """區名在色帶頂端，那裡只有「這區最後一關 → 下一區第一關」一段小路經過。

    用真實地圖：第二區最後一排只有兩關、停在中間偏右，曾經把區名壓在路下面。
    """
    placed, bands, _ = layout(build_map(topics, tasks, set()))
    for band, upper in zip(bands, bands[1:]):
        last = [p for p in placed if p.level.zone == band.zone][-1]
        first = next(p for p in placed if p.level.zone == upper.zone)
        lo, hi = min(last.x, first.x) - NODE_R, max(last.x, first.x) + NODE_R
        x = _label_x(band, placed, 200)
        assert x + 200 < lo or x > hi, band.zone


# ---------------------------------------------------------------------------
# 小熊站哪
# ---------------------------------------------------------------------------


def test_bear_stands_at_the_first_unfinished_level() -> None:
    levels = [_lv("s", 0, done=2), _lv("concept", 1, total=0), _lv("a", 1, done=1), _lv("b", 1)]
    assert current_level(levels).topic_id == "a"


def test_concept_level_is_never_the_current_level() -> None:
    """觀念關沒有題目，小熊不能停在那裡等一個永遠做不完的關卡。"""
    levels = [_lv("s", 0, done=2), _lv("concept", 1, total=0), _lv("a", 1)]
    assert current_level(levels).topic_id == "a"


def test_bear_waits_at_the_last_level_when_everything_is_done() -> None:
    """全破之後停在最後一個有題目的關卡，就算路線最後一關是觀念關。"""
    levels = [_lv("s", 0, done=2), _lv("a", 1, done=2), _lv("concept", 2, total=0)]
    assert current_level(levels).topic_id == "a"


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------


def test_svg_is_valid_xml_with_one_group_per_level() -> None:
    root = _parse(render_svg(_sample()))
    topics = [g.get("data-topic") for g in root.iterfind(".//svg:g[@class='level']", NS)]
    assert topics == [lv.topic_id for lv in _sample()]
    assert len(root.findall(".//svg:g[@class='bear']", NS)) == 1


def test_completed_level_shows_a_check_and_concept_level_says_so() -> None:
    levels = [_lv("s", 0, done=2), _lv("concept", 1, total=0), _lv("a", 1, done=1)]
    root = _parse(render_svg(levels))
    counts = {
        g.get("data-topic"): g.find("svg:text[@class='count']", NS).text
        for g in root.iterfind(".//svg:g[@class='level']", NS)
    }
    assert counts == {"s": "✓", "concept": "觀念", "a": "1/2"}


def test_names_and_ids_are_escaped() -> None:
    """關卡名與 id 來自資料檔，裡面有 ``<``、``&``、``"`` 也不能把 SVG 弄壞。"""
    levels = [_lv('s"x', 0, name="A < B & C")]
    root = _parse(render_svg(levels))
    names = [t.text for t in root.iterfind(".//svg:text[@class='name']", NS)]
    assert names == ["A < B & C"]
    assert root.find(".//svg:g[@class='level']", NS).get("data-topic") == 's"x'


def test_zones_beyond_the_theme_get_their_own_name() -> None:
    """第 4 區以後借第三區的顏色，但名字不能也叫「第三區・雪山」。"""
    assert zone_theme(4)[0] != zone_theme(3)[0]
    assert zone_theme(4)[1:] == zone_theme(3)[1:]


def test_long_names_wrap_to_two_bounded_lines() -> None:
    assert _wrap("Arrays") == ["Arrays"]
    assert _wrap("Dictionaries and Hashmaps") == ["Dictionaries", "and Hashmaps"]
    # 一維／二維的差別在第二行尾巴，不能被截掉
    assert _wrap("Dynamic Programming（二維）") == ["Dynamic", "Programming（二維）"]
    first, second = _wrap("A" * 40)
    assert len(first) <= 13 and len(second) <= 18 and second.endswith("…")


def test_real_map_renders(topics, tasks) -> None:
    """拿真實題庫畫一次：每個主題都有一關、只有一隻小熊。"""
    levels = build_map(topics, tasks, set())
    root = _parse(render_svg(levels))
    assert len(root.findall(".//svg:g[@class='level']", NS)) == len(topics)
    assert len(root.findall(".//svg:g[@class='bear']", NS)) == 1
