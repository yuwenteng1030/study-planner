"""學習地圖插畫：座標規則與 SVG 內容。

座標從 ``layout`` 直接驗，不去解析畫出來的路徑；SVG 只驗「是合法 XML、
每一關都在、狀態標記對、連結只放行 HackerRank」——顏色、大小這類純外觀
不寫死在測試裡。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from core.game_map import Stage, build_map, build_stages
from core.map_art import (
    COLUMNS,
    GUTTER,
    NODE_R,
    PER_ROW,
    WIDTH,
    _label_x,
    _wrap,
    layout,
    render_svg,
    safe_url,
    zone_theme,
)

NS = {"svg": "http://www.w3.org/2000/svg"}
HR = "https://www.hackerrank.com/challenges/"
STAGE = ".//*[@data-task]"


def _st(task_id: int, topic: str, zone: int, done: bool = False, **kw) -> Stage:
    return Stage(
        task_id=task_id,
        title=kw.get("title", f"T{task_id}"),
        difficulty=kw.get("difficulty", "Easy"),
        topic_id=topic,
        topic_name=kw.get("topic_name", topic.upper()),
        zone=zone,
        done=done,
    )


def _sample() -> list[Stage]:
    """起點 2 題、第一區 a 主題 7 題（兩排，第二排不滿）＋ b 主題 1 題、第二區 3 題。"""
    return [
        _st(1, "start", 0),
        _st(2, "start", 0),
        *(_st(10 + i, "a", 1) for i in range(7)),
        _st(20, "b", 1),
        *(_st(30 + i, "c", 2) for i in range(3)),
    ]


def _urls(stages: list[Stage]) -> dict[int, str]:
    return {s.task_id: f"{HR}t{s.task_id}" for s in stages}


def _parse(svg: str) -> ET.Element:
    return ET.fromstring(svg)


def _real(topics, tasks, done: set[int] = frozenset()) -> list[Stage]:
    return build_stages(build_map(topics, tasks, set(done)), tasks, set(done))


# ---------------------------------------------------------------------------
# 版面
# ---------------------------------------------------------------------------


def test_every_stage_is_placed_once_in_path_order() -> None:
    stages = _sample()
    placed, _, _, _ = layout(stages)
    assert [p.stage.task_id for p in placed] == [s.task_id for s in stages]


def test_later_zones_are_drawn_higher_up() -> None:
    """由下往上走：後面的區，每一關都在前面所有區的上方（y 比較小）。"""
    placed, bands, _, height = layout(_sample())
    by_zone: dict[int, list[float]] = {}
    for p in placed:
        by_zone.setdefault(p.stage.zone, []).append(p.y)
    assert max(by_zone[1]) < min(by_zone[0])
    assert max(by_zone[2]) < min(by_zone[1])

    assert bands[0].bottom == height and bands[-1].top == 0
    for b in bands:
        ys = by_zone[b.zone]
        assert b.top < min(ys) and max(ys) < b.bottom


def test_each_topic_starts_its_own_row_of_at_most_per_row() -> None:
    """一排只屬於一個主題，左欄的主題名才對得上那一排。"""
    placed, _, labels, _ = layout(_sample())
    rows: dict[float, list[Stage]] = {}
    for p in placed:
        rows.setdefault(p.y, []).append(p.stage)
    for members in rows.values():
        assert len(members) <= PER_ROW
        assert len({s.topic_id for s in members}) == 1
    # a 主題 7 題 → 兩排；主題名對齊它的第一排
    assert sorted(len(m) for m in rows.values() if m[0].topic_id == "a") == [2, 5]
    first_a = next(p for p in placed if p.stage.topic_id == "a")
    assert next(lb for lb in labels if lb.topic_id == "a").y == first_a.y


def test_next_row_starts_on_the_side_where_the_last_row_ended() -> None:
    """S 形折返：上一排停在左半邊，下一排就從最左欄開始；停在右半邊就從最右欄。"""
    placed, _, _, _ = layout(_sample())
    middle = (COLUMNS[0] + COLUMNS[-1]) / 2
    for prev, nxt in zip(placed, placed[1:]):
        if nxt.y != prev.y:
            expected = COLUMNS[0] if prev.x < middle else COLUMNS[-1]
            assert nxt.x == expected, (prev.stage.task_id, nxt.stage.task_id)


def test_nodes_stay_inside_the_picture_and_out_of_the_topic_column() -> None:
    placed, _, _, height = layout(_sample())
    for p in placed:
        assert GUTTER < p.x - NODE_R and p.x + NODE_R < WIDTH
        assert 0 < p.y - NODE_R and p.y + NODE_R < height


def test_zone_label_stays_clear_of_the_path_leaving_the_zone(topics, tasks) -> None:
    """區名在色帶頂端，那裡只有「這區最後一關 → 下一區第一關」一段小路經過。"""
    placed, bands, _, _ = layout(_real(topics, tasks))
    for band, upper in zip(bands, bands[1:]):
        last = [p for p in placed if p.stage.zone == band.zone][-1]
        first = next(p for p in placed if p.stage.zone == upper.zone)
        lo, hi = min(last.x, first.x) - NODE_R, max(last.x, first.x) + NODE_R
        x = _label_x(band, placed, 200)
        assert x + 200 < lo or x > hi, band.zone


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------


def test_svg_has_one_link_per_stage_and_one_bear() -> None:
    stages = _sample()
    root = _parse(render_svg(stages, _urls(stages), here_id=10))
    nodes = root.findall(STAGE, NS)
    assert [int(n.get("data-task")) for n in nodes] == [s.task_id for s in stages]
    assert all(n.tag == f"{{{NS['svg']}}}a" for n in nodes)
    assert nodes[0].get("href") == f"{HR}t1"
    assert nodes[0].get("target") == "_blank"
    assert "noopener" in nodes[0].get("rel")
    assert len(root.findall(".//svg:g[@class='bear']", NS)) == 1


def test_bear_and_halo_mark_the_here_stage_only() -> None:
    stages = _sample()
    root = _parse(render_svg(stages, _urls(stages), here_id=12))
    here = [n.get("data-task") for n in root.findall(STAGE, NS) if "here" in n.get("class").split()]
    assert here == ["12"]


def test_bear_stands_between_columns_clear_of_every_node() -> None:
    """小熊不能蓋住任何一關（包括上一排的關卡），也不能跑出圖或擠進主題欄。"""
    from core.map_art import BEAR_OFFSET

    stages = _sample()
    placed, _, _, _ = layout(stages)
    bear_half = 17 * 0.8  # 小熊含耳朵的半寬（_bear 的座標 × 縮放）
    for here in placed:
        bx = here.x - BEAR_OFFSET
        assert GUTTER - 20 < bx - bear_half and bx + bear_half < WIDTH
        for p in placed:
            if abs(p.y - here.y) < 30:
                assert abs(p.x - bx) > NODE_R + bear_half, (here.stage.task_id, p.stage.task_id)


def test_no_bear_when_there_is_no_here_stage() -> None:
    stages = _sample()
    root = _parse(render_svg(stages, _urls(stages), here_id=None))
    assert root.findall(".//svg:g[@class='bear']", NS) == []


def test_done_stage_shows_a_check_and_open_stage_shows_its_level_number() -> None:
    stages = [
        _st(1, "s", 0, done=True),
        _st(2, "s", 0, difficulty="Medium"),
        _st(3, "s", 0, difficulty="Hard"),
    ]
    root = _parse(render_svg(stages, _urls(stages), here_id=2))
    nodes = root.findall(STAGE, NS)
    marks = {n.get("data-task"): n.find("svg:text[@class='mark']", NS).text for n in nodes}
    # 關卡編號＝路線上的第幾關；難度放在 data-difficulty（外框顏色）
    assert marks == {"1": "✓", "2": "2", "3": "3"}
    assert {n.get("data-task"): n.get("data-difficulty") for n in nodes} == {"1": "E", "2": "M", "3": "H"}
    done = root.find(".//*[@data-task='1']", NS)
    assert "done" in done.get("class").split()


def test_only_hackerrank_https_links_become_links() -> None:
    """地圖不經消毒就放進頁面，``javascript:`` 或別的網站不能變成可點的連結。"""
    assert safe_url(f"{HR}x") == f"{HR}x"
    for bad in ("javascript:alert(1)", "http://www.hackerrank.com/x",
                "https://www.hackerrank.com.evil.test/x", "https://www.hackerrank.com@evil.test/x",
                "https://www.hackerrank.com:8443/x", f"{HR}x) [Login](https://evil.test/p",
                f"{HR}x y", f'{HR}x"onclick=1', "", None):
        assert safe_url(bad) is None, bad

    stages = [_st(1, "s", 0), _st(2, "s", 0)]
    root = _parse(render_svg(stages, {1: "javascript:alert(1)", 2: f"{HR}ok"}, here_id=None))
    bad, good = root.findall(STAGE, NS)
    assert bad.tag == f"{{{NS['svg']}}}g" and bad.get("href") is None
    assert good.get("href") == f"{HR}ok"


def test_titles_names_and_urls_are_escaped() -> None:
    """題名、主題名、網址都來自資料檔，裡面有 ``<``、``&``、``"`` 也不能把 SVG 弄壞。"""
    stages = [_st(1, "s", 0, title='A < "B" & C<script>', topic_name="X & <Y></text><foreignObject>")]
    url = f"{HR}a?b=1&c=2"
    root = _parse(render_svg(stages, {1: url}, here_id=1))
    node = root.find(STAGE, NS)
    assert node.get("href") == url
    assert 'A < "B" & C<script>' in node.find("svg:title", NS).text
    assert root.findall(".//svg:script", NS) == [] and root.findall(".//svg:foreignObject", NS) == []
    topic = [t.text for t in root.iterfind(".//svg:text[@class='topic']", NS)]
    assert topic == _wrap("X & <Y></text><foreignObject>")


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


def test_real_map_renders_every_task(topics, tasks) -> None:
    """拿真實題庫畫一次：每一題都有一關、每一關都是 HackerRank 連結。"""
    stages = _real(topics, tasks)
    urls = {t.id: f"{HR}{t.id}" for t in tasks}
    root = _parse(render_svg(stages, urls, here_id=stages[0].task_id))
    nodes = root.findall(STAGE, NS)
    assert len(nodes) == len(tasks)
    assert all(n.get("href") for n in nodes)



def test_hard_stage_is_a_boss_with_a_crown() -> None:
    stages = [_st(1, "s", 0), _st(2, "s", 0, difficulty="Hard")]
    root = _parse(render_svg(stages, _urls(stages), here_id=None))
    boss = root.find(".//*[@data-task='2']", NS)
    assert "boss" in boss.get("class").split()
    assert boss.find("svg:text[@class='crown']", NS) is not None
    assert root.find(".//*[@data-task='1']", NS).find("svg:text[@class='crown']", NS) is None


def test_only_segments_between_two_done_stages_are_walked() -> None:
    stages = [_st(1, "s", 0, done=True), _st(2, "s", 0, done=True), _st(3, "s", 0), _st(4, "s", 0, done=True)]
    svg = render_svg(stages, _urls(stages), here_id=3)
    assert svg.count('class="walked"') == 1


def test_focus_stars_and_zone_name_override() -> None:
    stages = [_st(1, "s", 0), _st(2, "s", 0)]
    root = _parse(render_svg(stages, _urls(stages), None, focus_ids={2}, zone_names={0: "暖身"}))
    assert root.find(".//*[@data-task='1']", NS).find("svg:text[@class='focus-star']", NS) is None
    assert root.find(".//*[@data-task='2']", NS).find("svg:text[@class='focus-star']", NS) is not None
    zones = [t.text for t in root.iterfind(".//svg:text[@class='zone']", NS)]
    assert zones and zones[0].startswith("暖身")


def test_zone_decoration_stays_clear_of_the_path_leaving_the_zone(topics, tasks) -> None:
    """裝飾跟區名一樣，不能壓在往上一區走的那段小路上。"""
    stages = _real(topics, tasks)
    placed, bands, _, _ = layout(stages)
    root = _parse(render_svg(stages, _urls(stages), None))
    decors = [float(t.get("x")) for t in root.iterfind(".//svg:text[@class='decor']", NS)]
    for band, x in zip(sorted(bands, key=lambda b: -b.top), decors):
        members = [p for p in placed if p.stage.zone == band.zone]
        above = [p for p in placed if p.y < band.top]
        if not (members and above):
            continue
        lo, hi = sorted((members[-1].x, above[0].x))
        assert not (lo - 20 <= x <= hi + 20), (band.zone, x, lo, hi)


def test_focus_layout_flows_five_per_row_without_topic_labels() -> None:
    stages = [_st(i, "abcdefgh"[i % 8], 0) for i in range(1, 8)]  # 7 關、每關不同主題
    placed, bands, labels, _ = layout(stages, by_topic=False)
    rows = sorted({p.y for p in placed}, reverse=True)
    assert len(rows) == 2 and labels == []  # 5 + 2，不是 7 排
    assert sum(1 for p in placed if p.y == rows[0]) == PER_ROW


def test_walked_segment_follows_the_same_curve_as_the_path() -> None:
    """金色那一段要跟整條小路的那一段完全重疊（轉彎處不能是直線弦）。"""
    from core.map_art import _curve_segments

    stages = [_st(i, "s", 0, done=i <= 7) for i in range(1, 9)]  # 跨排，有轉彎
    svg = render_svg(stages, _urls(stages), None)
    root = _parse(svg)
    walked = [p.get("d") for p in root.iterfind(".//svg:path[@class='walked']", NS)]
    placed, *_ = layout(stages)
    expected = _curve_segments([(p.x, p.y) for p in placed])[:6]
    assert walked == expected


def test_trophy_never_sits_on_the_bear() -> None:
    # 5 關一排、從左到右：最後一關在最右邊，右邊放不下獎盃
    stages = [_st(i, "s", 0, done=i < 5) for i in range(1, 6)]
    root = _parse(render_svg(stages, _urls(stages), here_id=5))
    assert root.find(".//svg:text[@class='goal']", NS) is None
    root = _parse(render_svg(stages, _urls(stages), here_id=None))
    assert root.find(".//svg:text[@class='goal']", NS) is not None
