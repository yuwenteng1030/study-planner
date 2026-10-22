"""學習地圖的插畫版：把 ``build_map`` 的關卡畫成一條蜿蜒小路的 SVG。

只負責「畫」，不決定任何學習邏輯：關卡順序、區號、題數都來自
``core/game_map.py``。拆成兩步——``layout`` 算座標、``render_svg`` 輸出字串——
是為了讓座標規則能單獨測，不必去解析 SVG。

版面規則（都是顯示選擇，見 BACKLOG D）：

- 由下往上走，起點在最下面，最後一區在最上面，像手遊的關卡圖。
- 每一區是一條色帶，區內每排最多 ``PER_ROW`` 關。
- 排與排之間 S 形折返：下一排從上一排**最後一關的那一側**開始，
  這樣小路不會在兩排之間斜著橫跨整張圖。
- 小熊站在路線上第一個還沒打完的關卡旁邊；觀念關沒有題目，不算。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from html import escape

from core.game_map import Level

WIDTH = 640
PER_ROW = 4
COLUMNS: tuple[float, ...] = (100, 246.7, 393.3, 540)
ROW_HEIGHT = 100
BAND_HEADER = 50  # 色帶上方放區名的高度
NODE_R = 24

#: 區號 → （名稱, 底色, 強調色）。第 4 區以後沿用最後一個配色，名稱另取（見 ``zone_theme``）。
ZONE_THEME: dict[int, tuple[str, str, str]] = {
    0: ("起點・小木屋", "#FFF4D6", "#E8A33A"),
    1: ("第一區・草原", "#E3F4D2", "#6DB34A"),
    2: ("第二區・森林", "#D2EBDD", "#3E8E6A"),
    3: ("第三區・雪山", "#E4F0FA", "#5B8FC7"),
}

GOLD = "#FFD66B"
INK = "#4A3728"


def zone_theme(zone: int) -> tuple[str, str, str]:
    """（名稱, 底色, 強調色）。沒定義的區只借顏色，名稱不能跟第三區撞。"""
    if zone in ZONE_THEME:
        return ZONE_THEME[zone]
    _, bg, accent = ZONE_THEME[max(ZONE_THEME)]
    return f"第 {zone} 區", bg, accent


@dataclass(frozen=True)
class Placed:
    level: Level
    x: float
    y: float


@dataclass(frozen=True)
class Band:
    zone: int
    top: float
    bottom: float


def _rows(count: int) -> int:
    return max(1, math.ceil(count / PER_ROW))


def layout(levels: list[Level]) -> tuple[list[Placed], list[Band], float]:
    """回傳（照路線順序的關卡座標, 每區色帶, 圖高）。

    ``levels`` 要是 ``build_map`` 排好的順序；這裡不重排，只照順序擺。
    """
    zones = sorted({lv.zone for lv in levels})
    by_zone = {z: [lv for lv in levels if lv.zone == z] for z in zones}
    height = sum(BAND_HEADER + _rows(len(by_zone[z])) * ROW_HEIGHT for z in zones)

    placed: list[Placed] = []
    bands: list[Band] = []
    bottom = height
    left_to_right = True
    for z in zones:
        members = by_zone[z]
        rows = _rows(len(members))
        top = bottom - BAND_HEADER - rows * ROW_HEIGHT
        bands.append(Band(zone=z, top=top, bottom=bottom))
        for r in range(rows):
            chunk = members[r * PER_ROW : (r + 1) * PER_ROW]
            cols = COLUMNS if left_to_right else tuple(reversed(COLUMNS))
            y = bottom - ROW_HEIGHT / 2 - 10 - r * ROW_HEIGHT
            placed.extend(Placed(lv, cols[i], y) for i, lv in enumerate(chunk))
            # 下一排從這一排結束的那一側開始
            left_to_right = placed[-1].x < WIDTH / 2
        bottom = top
    return placed, bands, height


def current_level(levels: list[Level]) -> Level | None:
    """路線上第一個還有題目沒做的關卡；全部做完就是最後一關。"""
    playable = [lv for lv in levels if not lv.is_concept_only]
    for lv in playable:
        if lv.done_count < lv.total_count:
            return lv
    return playable[-1] if playable else None


# ---------------------------------------------------------------------------
# 輸出
# ---------------------------------------------------------------------------


def _smooth_path(points: list[tuple[float, float]]) -> str:
    """Catmull-Rom 轉三次貝茲，讓小路是圓滑的曲線而不是折線。"""
    if not points:
        return ""
    d = [f"M{points[0][0]:.1f},{points[0][1]:.1f}"]
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d.append(f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}")
    return " ".join(d)


def _wrap(name: str, limit: int = 13, second: int = 18) -> list[str]:
    """關卡名太長就拆兩行，優先在空白或全形括號前斷；第二行超過 ``second`` 字就截斷加「…」。

    第二行比第一行寬鬆，是為了留住「Programming（一維）」這種區分一維／二維的尾巴。
    """
    if len(name) <= limit:
        return [name]
    cut = max(name.rfind(" ", 0, limit + 1), name.rfind("（", 0, limit + 1))
    if cut <= 0:
        cut = limit
    rest = name[cut:].strip()
    if len(rest) > second:
        rest = rest[: second - 1] + "…"
    return [name[:cut].strip(), rest]


def _bear(x: float, y: float) -> str:
    """原創小熊（圓臉、圓耳、腮紅）。不是任何既有卡通角色。"""
    fur, inner = "#A9744F", "#E9C4A0"
    return (
        f'<g class="bear" transform="translate({x:.1f},{y:.1f})">'
        f'<ellipse cx="0" cy="24" rx="14" ry="4" fill="#000" opacity="0.12"/>'
        f'<ellipse cx="0" cy="12" rx="13" ry="11" fill="{fur}"/>'
        f'<ellipse cx="0" cy="14" rx="7" ry="6" fill="{inner}"/>'
        f'<circle cx="-11" cy="-15" r="6" fill="{fur}"/>'
        f'<circle cx="11" cy="-15" r="6" fill="{fur}"/>'
        f'<circle cx="-11" cy="-15" r="3" fill="{inner}"/>'
        f'<circle cx="11" cy="-15" r="3" fill="{inner}"/>'
        f'<circle cx="0" cy="-4" r="15" fill="{fur}"/>'
        f'<ellipse cx="0" cy="1" rx="7.5" ry="5.5" fill="#F1D9B5"/>'
        f'<ellipse cx="0" cy="-1.5" rx="2.8" ry="2" fill="{INK}"/>'
        f'<circle cx="-5.5" cy="-7" r="1.8" fill="{INK}"/>'
        f'<circle cx="5.5" cy="-7" r="1.8" fill="{INK}"/>'
        f'<ellipse cx="-10" cy="0" rx="3" ry="1.8" fill="#F4A3A3" opacity="0.8"/>'
        f'<ellipse cx="10" cy="0" rx="3" ry="1.8" fill="#F4A3A3" opacity="0.8"/>'
        "</g>"
    )


def _node(p: Placed, accent: str) -> str:
    lv = p.level
    x, y = p.x, p.y
    circ = 2 * math.pi * NODE_R
    parts = [f'<g class="level" data-topic="{escape(lv.topic_id)}">']
    parts.append(f'<circle cx="{x:.1f}" cy="{y + 4:.1f}" r="{NODE_R + 4}" fill="#000" opacity="0.10"/>')

    if lv.is_concept_only:
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{NODE_R}" fill="#FFFDF7" '
            f'stroke="{accent}" stroke-width="3" stroke-dasharray="5 4"/>'
            f'<text x="{x:.1f}" y="{y + 4:.1f}" class="count">觀念</text>'
        )
    else:
        complete = lv.done_count >= lv.total_count
        frac = lv.done_count / lv.total_count
        fill = GOLD if complete else "#FFFFFF"
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{NODE_R + 3}" fill="{fill}"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{NODE_R}" fill="none" stroke="#EDE4D3" stroke-width="6"/>'
        )
        if frac > 0:
            ring = "#E8A33A" if complete else accent
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{NODE_R}" fill="none" stroke="{ring}" '
                f'stroke-width="6" stroke-linecap="round" '
                f'stroke-dasharray="{frac * circ:.1f} {circ:.1f}" '
                f'transform="rotate(-90 {x:.1f} {y:.1f})"/>'
            )
        label = "✓" if complete else f"{lv.done_count}/{lv.total_count}"
        parts.append(f'<text x="{x:.1f}" y="{y + 4:.1f}" class="count">{label}</text>')

    for i, line in enumerate(_wrap(lv.name)):
        parts.append(f'<text x="{x:.1f}" y="{y + NODE_R + 18 + i * 14:.1f}" class="name">{escape(line)}</text>')
    parts.append("</g>")
    return "".join(parts)


def _label_x(band: Band, placed: list[Placed], width: float) -> float:
    """區名放在色帶頂端、小路往上一區走的**另一側**，免得路壓在字上。

    色帶頂端只有一段小路經過：這一區最後一關 → 下一區第一關。
    """
    members = [p for p in placed if p.level.zone == band.zone]
    above = [p for p in placed if p.y < band.top]
    exit_x = (members[-1].x + above[0].x) / 2 if members and above else WIDTH / 2
    return WIDTH - 16 - width if exit_x < WIDTH / 2 else 16


def render_svg(levels: list[Level]) -> str:
    """整張地圖的 SVG 字串。寬度跟著容器縮放。

    頁面用 ``st.image`` 顯示，SVG 會被包成 ``<img>``：裡面的 ``<title>`` 不會
    跳提示、也不會被螢幕閱讀器讀到，所以這裡不放；題數細節在頁面的文字版。
    """
    placed, bands, height = layout(levels)
    here = current_level(levels)
    by_zone_done = {b.zone: (0, 0) for b in bands}
    for lv in levels:
        d, t = by_zone_done[lv.zone]
        by_zone_done[lv.zone] = (d + lv.done_count, t + lv.total_count)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height:.0f}" '
        f'width="100%" style="font-family:system-ui,sans-serif">',
        "<style>"
        f".name{{font-size:12px;fill:{INK};text-anchor:middle}}"
        f".count{{font-size:12px;font-weight:700;fill:{INK};text-anchor:middle}}"
        f".zone{{font-size:14px;font-weight:700;fill:#fff;text-anchor:middle}}"
        "</style>",
    ]

    for b in bands:
        name, bg, accent = zone_theme(b.zone)
        done, total = by_zone_done[b.zone]
        label = f"{name}　{done}/{total}" if total else name
        pill = 24 + len(label) * 13
        pill_x = _label_x(b, placed, pill)
        out.append(
            f'<rect x="0" y="{b.top:.1f}" width="{WIDTH}" height="{b.bottom - b.top:.1f}" fill="{bg}"/>'
            f'<rect x="{pill_x:.1f}" y="{b.top + 12:.1f}" rx="13" width="{pill}" height="26" fill="{accent}"/>'
            f'<text x="{pill_x + pill / 2:.1f}" y="{b.top + 30:.1f}" class="zone">{escape(label)}</text>'
        )

    points = [(p.x, p.y) for p in placed]
    path = _smooth_path(points)
    out.append(
        f'<path d="{path}" fill="none" stroke="#D9C3A0" stroke-width="16" stroke-linecap="round"/>'
        f'<path d="{path}" fill="none" stroke="#FFF8EA" stroke-width="4" '
        f'stroke-dasharray="2 10" stroke-linecap="round"/>'
    )

    for p in placed:
        out.append(_node(p, zone_theme(p.level.zone)[2]))

    if here is not None:
        spot = next(p for p in placed if p.level.topic_id == here.topic_id)
        # 光圈標出是哪一關，小熊站在它左邊（最左一欄也還在畫面內）
        out.append(
            f'<circle class="here" cx="{spot.x:.1f}" cy="{spot.y:.1f}" r="{NODE_R + 10}" fill="none" '
            f'stroke="#F28C8C" stroke-width="3" stroke-dasharray="6 5"/>'
        )
        out.append(_bear(spot.x - 58, spot.y - 6))

    out.append("</svg>")
    return "".join(out)
