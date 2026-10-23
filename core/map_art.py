"""學習地圖的插畫版：把 ``build_stages`` 的關卡（一關＝一題）畫成一條蜿蜒小路的 SVG。

只負責「畫」，不決定任何學習邏輯：關卡順序、區號、做完沒有都來自
``core/game_map.py``。拆成兩步——``layout`` 算座標、``render_svg`` 輸出字串——
是為了讓座標規則能單獨測，不必去解析 SVG。

版面規則（都是顯示選擇，見 BACKLOG D）：

- 由下往上走，起點在最下面，最後一區在最上面，像手遊的關卡圖。
- 每一區是一條色帶；每個主題自己起新的一排，一排最多 ``PER_ROW`` 題，
  主題名寫在左邊的欄位裡。
- 排與排之間 S 形折返：下一排從上一排**最後一關的那一側**開始。
- 每一關是一個連到 HackerRank 題目的連結；小熊站在路線上第一個還沒做的關卡上。

遊戲化的外觀（2026-10-23，AI 決定，只影響畫面）：

- 關卡上寫**關卡編號**（第幾關），難度用外框顏色表示；Hard 是 **Boss 關**（大一號、戴皇冠）。
- 走過的路變金色：前後兩關都做完的那一段小路畫成金色，一眼看得出走到哪裡。
- 每一區的標題列有自己的裝飾（草原、森林、雪山），起點有城堡、終點有獎盃。
- 重點題組的關卡右上角有一顆星（只在顯示整個題庫時畫，重點路線裡每關都是重點）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from html import escape
from urllib.parse import urlsplit

from core.game_map import Stage

WIDTH = 640
GUTTER = 150  # 左邊放主題名的欄位寬
PER_ROW = 5
COLUMNS: tuple[float, ...] = (200, 300, 400, 500, 600)
#: 重點路線沒有左欄的主題名，關卡往左展開用滿寬度
FOCUS_COLUMNS: tuple[float, ...] = (110, 232, 354, 476, 598)
ROW_HEIGHT = 62
BAND_HEADER = 46  # 色帶上方放區名的高度
NODE_R = 17
BEAR_OFFSET = 50  # 小熊離關卡中心的距離：剛好是兩欄中間

#: 只有連到這個主機的 https 網址會變成連結。題庫是自己維護的 CSV，但地圖是用
#: ``st.components.v2`` 直接放進頁面、不經過消毒，所以連結來源要自己把關。
ALLOWED_HOST = "www.hackerrank.com"

#: 網址裡不該出現的字元：會在 Markdown 連結或 HTML 屬性裡斷開語法。
_URL_FORBIDDEN = set("<>\"'\\`()[] ")

#: 區號 → （名稱, 底色, 強調色）。第 4 區以後沿用最後一個配色，名稱另取（見 ``zone_theme``）。
ZONE_THEME: dict[int, tuple[str, str, str]] = {
    0: ("起點・小木屋", "#FFF4D6", "#E8A33A"),
    1: ("第一區・草原", "#E3F4D2", "#6DB34A"),
    2: ("第二區・森林", "#D2EBDD", "#3E8E6A"),
    3: ("第三區・雪山", "#E4F0FA", "#5B8FC7"),
}

#: 難度 → （節點上的字, 顏色）。
DIFFICULTY_MARK: dict[str, tuple[str, str]] = {
    "Easy": ("E", "#4E9A2E"),
    "Medium": ("M", "#D9861C"),
    "Hard": ("H", "#D0504E"),
}

GOLD = "#FFD66B"
INK = "#4A3728"
#: 走過的路
PATH_DONE = "#F2B84B"

#: Boss 關（Hard）比一般關卡大多少
BOSS_EXTRA = 4

#: 每一區標題列的裝飾（只是畫面）。emoji 用 SVG 文字畫，不用外部圖檔。
ZONE_DECOR: dict[int, str] = {0: "🏰", 1: "🌼 🌾 🌼", 2: "🌲 🍄 🌲", 3: "⛰️ ❄️ ⛰️"}


def zone_theme(zone: int) -> tuple[str, str, str]:
    """（名稱, 底色, 強調色）。沒定義的區只借顏色，名稱不能跟第三區撞。"""
    if zone in ZONE_THEME:
        return ZONE_THEME[zone]
    _, bg, accent = ZONE_THEME[max(ZONE_THEME)]
    return f"第 {zone} 區", bg, accent


@dataclass(frozen=True)
class Placed:
    stage: Stage
    x: float
    y: float


@dataclass(frozen=True)
class Band:
    zone: int
    top: float
    bottom: float


@dataclass(frozen=True)
class TopicLabel:
    """左欄的主題名，對齊這個主題的第一排。"""

    topic_id: str
    name: str
    y: float
    done: int
    total: int


def _rows(count: int) -> int:
    return max(1, math.ceil(count / PER_ROW))


def _topics_in_order(stages: list[Stage]) -> list[list[Stage]]:
    """把連續同主題的關卡分成一組，保持路線順序。"""
    groups: list[list[Stage]] = []
    for stage in stages:
        if groups and groups[-1][0].topic_id == stage.topic_id:
            groups[-1].append(stage)
        else:
            groups.append([stage])
    return groups


def layout(
    stages: list[Stage], *, by_topic: bool = True
) -> tuple[list[Placed], list[Band], list[TopicLabel], float]:
    """回傳（照路線順序的關卡座標, 每區色帶, 主題名, 圖高）。

    ``stages`` 要是 ``build_stages`` 排好的順序；這裡不重排，只照順序擺。
    ``by_topic=False``（重點路線）：同一區的關卡不分主題、每排 ``PER_ROW`` 關連續排，
    也不畫左欄的主題名——重點路線一個主題常常只有 1～2 題，分主題會排成一堆半空的排。
    """
    if by_topic:
        groups = _topics_in_order(stages)
    else:
        groups = []
        for stage in stages:
            if groups and groups[-1][0].zone == stage.zone:
                groups[-1].append(stage)
            else:
                groups.append([stage])
    zones = sorted({s.zone for s in stages})
    by_zone = {z: [g for g in groups if g[0].zone == z] for z in zones}
    height = sum(
        BAND_HEADER + sum(_rows(len(g)) for g in by_zone[z]) * ROW_HEIGHT for z in zones
    )

    columns = COLUMNS if by_topic else FOCUS_COLUMNS
    placed: list[Placed] = []
    bands: list[Band] = []
    labels: list[TopicLabel] = []
    bottom = height
    left_to_right = True
    middle = (columns[0] + columns[-1]) / 2
    for z in zones:
        rows_in_zone = sum(_rows(len(g)) for g in by_zone[z])
        top = bottom - BAND_HEADER - rows_in_zone * ROW_HEIGHT
        bands.append(Band(zone=z, top=top, bottom=bottom))
        row_bottom = bottom
        for group in by_zone[z]:
            if by_topic:
                labels.append(
                    TopicLabel(
                        topic_id=group[0].topic_id,
                        name=group[0].topic_name,
                        y=row_bottom - ROW_HEIGHT / 2,
                        done=sum(s.done for s in group),
                        total=len(group),
                    )
                )
            for r in range(_rows(len(group))):
                chunk = group[r * PER_ROW : (r + 1) * PER_ROW]
                cols = columns if left_to_right else tuple(reversed(columns))
                y = row_bottom - ROW_HEIGHT / 2
                placed.extend(Placed(s, cols[i], y) for i, s in enumerate(chunk))
                # 下一排從這一排結束的那一側開始
                left_to_right = placed[-1].x < middle
                row_bottom -= ROW_HEIGHT
        bottom = top
    return placed, bands, labels, height


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
    """主題名太長就拆兩行，優先在空白或全形括號前斷；第二行超過 ``second`` 字就截斷加「…」。

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


def safe_url(url: str | None) -> str | None:
    """只放行 HackerRank 的 https 網址；其他一律不做成連結。

    看解析出來的主機，不只看字首；也擋掉空白、括號、引號這些會在
    文字版的 Markdown 連結（``[題名](網址)``）裡斷開語法、多生出一個連結的字元。
    """
    if not url or any(c in _URL_FORBIDDEN or ord(c) < 0x20 or c.isspace() for c in url):
        return None
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname == ALLOWED_HOST and parts.port is None:
        return url
    return None


def _bear(x: float, y: float) -> str:
    """原創小熊（圓臉、圓耳、腮紅）。不是任何既有卡通角色。

    ``pointer-events: none``：小熊站在關卡上，點小熊要等於點那一關。
    """
    fur, inner = "#A9744F", "#E9C4A0"
    return (
        f'<g class="bear" transform="translate({x:.1f},{y:.1f}) scale(0.8)" '
        f'style="pointer-events:none">'
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


#: 今天排的關卡外圈（藍：Easy 題本身已經是綠框，綠圈會分不出來）與過期沒做的外圈（紅）。
TODAY_RING = "#2F7FD1"
OVERDUE_RING = "#D9534F"


def _node(p: Placed, url: str | None, is_here: bool, plan_tag: str = "", *,
          level: int = 0, focus: bool = False) -> str:
    s = p.stage
    x, y = p.x, p.y
    letter, color = DIFFICULTY_MARK.get(s.difficulty, ("?", INK))
    boss = s.difficulty == "Hard"
    r = NODE_R + (BOSS_EXTRA if boss else 0)
    status = "已完成" if s.done else "未完成"
    when = {"today": "，今天的任務", "overdue": "，過期還沒做"}.get(plan_tag, "")
    tip = f"第 {level} 關：{s.title}（{s.difficulty}{'・Boss' if boss else ''}，{status}{when}）"

    classes = (
        "stage" + (" done" if s.done else "") + (" here" if is_here else "")
        + (" boss" if boss else "") + (f" {plan_tag}" if plan_tag else "")
    )
    # 有網址就是真的 <a>：就算畫面的 JavaScript 沒跑起來，點下去照樣能開題目
    href = (
        f' href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer"'
        if url
        else ""
    )
    tag = "a" if url else "g"
    parts = [
        f'<{tag} class="{classes}" data-task="{int(s.task_id)}" data-difficulty="{letter}"{href}>',
        f"<title>{escape(tip)}</title>",
        f'<circle cx="{x:.1f}" cy="{y + 3:.1f}" r="{r + 2}" fill="#000" opacity="0.12"/>',
    ]
    if plan_tag:
        ring = TODAY_RING if plan_tag == "today" else OVERDUE_RING
        parts.append(
            f'<circle class="plan-ring" cx="{x:.1f}" cy="{y:.1f}" r="{r + 4}" fill="none" '
            f'stroke="{ring}" stroke-width="3"/>'
        )
    if s.done:
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{GOLD}" stroke="#E8A33A" stroke-width="3"/>'
            f'<circle cx="{x - r / 3:.1f}" cy="{y - r / 3:.1f}" r="{r / 4:.1f}" fill="#FFF6D5" opacity="0.8"/>'
            f'<text x="{x:.1f}" y="{y + 5:.1f}" class="mark" fill="{INK}">✓</text>'
        )
    else:
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="#FFFFFF" stroke="{color}" stroke-width="{4 if boss else 3}"/>'
            f'<text x="{x:.1f}" y="{y + 5:.1f}" class="mark" fill="{color}">{level}</text>'
        )
    if boss:
        parts.append(f'<text x="{x:.1f}" y="{y - r - 3:.1f}" class="crown">👑</text>')
    if focus:
        parts.append(f'<text x="{x + r - 2:.1f}" y="{y - r + 6:.1f}" class="focus-star">⭐</text>')
    parts.append(f"</{tag}>")
    return "".join(parts)


def _label_x(band: Band, placed: list[Placed], width: float) -> float:
    """區名放在色帶頂端、小路往上一區走的**另一側**，免得路壓在字上。

    色帶頂端只有一段小路經過：這一區最後一關 → 下一區第一關。
    """
    members = [p for p in placed if p.stage.zone == band.zone]
    above = [p for p in placed if p.y < band.top]
    xs = [p.x for p in placed] or [COLUMNS[0], COLUMNS[-1]]
    middle = (min(xs) + max(xs)) / 2
    exit_x = (members[-1].x + above[0].x) / 2 if members and above else middle
    return WIDTH - 16 - width if exit_x < middle else GUTTER


def render_svg(
    stages: list[Stage],
    urls: dict[int, str],
    here_id: int | None,
    today_ids: frozenset[int] | set[int] = frozenset(),
    overdue_ids: frozenset[int] | set[int] = frozenset(),
    focus_ids: frozenset[int] | set[int] = frozenset(),
    zone_names: dict[int, str] | None = None,
    by_topic: bool = True,
) -> str:
    """整張地圖的 SVG 字串。寬度跟著容器縮放。

    ``here_id`` 是小熊站的關卡（通常是 ``next_stage``）。每一關都是 ``<a>``，
    帶 ``data-task``，頁面的 JavaScript 靠它知道點的是哪一題。
    ``zone_names`` 可以換掉區名（重點路線用「暖身／Medium 主力／進階」）。
    """
    placed, bands, labels, height = layout(stages, by_topic=by_topic)
    by_zone_done = {b.zone: (0, 0) for b in bands}
    for s in stages:
        d, t = by_zone_done[s.zone]
        by_zone_done[s.zone] = (d + s.done, t + 1)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height:.0f}" '
        f'width="100%" style="font-family:system-ui,sans-serif;display:block">',
        "<style>"
        f".topic{{font-size:11px;font-weight:600;fill:{INK}}}"
        f".topic-count{{font-size:10px;fill:{INK};opacity:0.7}}"
        ".mark{font-size:13px;font-weight:800;text-anchor:middle}"
        ".zone{font-size:14px;font-weight:700;fill:#fff;text-anchor:middle}"
        ".crown{font-size:14px;text-anchor:middle;pointer-events:none}"
        ".focus-star{font-size:10px;text-anchor:middle;pointer-events:none}"
        ".decor{font-size:16px;text-anchor:middle;pointer-events:none;opacity:0.9}"
        ".goal{font-size:22px;text-anchor:middle;pointer-events:none}"
        ".stage{cursor:pointer}"
        ".stage:hover circle,.stage:focus circle{filter:brightness(0.93)}"
        "</style>",
    ]

    for b in bands:
        name, bg, accent = zone_theme(b.zone)
        if zone_names and b.zone in zone_names:
            name = zone_names[b.zone]
        done, total = by_zone_done[b.zone]
        label = f"{name}　{done}/{total}"
        pill = 24 + len(label) * 13
        pill_x = _label_x(b, placed, pill)
        out.append(
            f'<rect x="0" y="{b.top:.1f}" width="{WIDTH}" height="{b.bottom - b.top:.1f}" fill="{bg}"/>'
            f'<rect x="{pill_x:.1f}" y="{b.top + 10:.1f}" rx="13" width="{pill}" height="26" fill="{accent}"/>'
            f'<text x="{pill_x + pill / 2:.1f}" y="{b.top + 28:.1f}" class="zone">{escape(label)}</text>'
        )
        # 裝飾緊貼在區名旁邊、朝地圖中間：區名本來就放在小路離開這一區的另一側
        # （_label_x），放在另一側會壓到往上走的那段路
        decor = ZONE_DECOR.get(b.zone, ZONE_DECOR[max(ZONE_DECOR)])
        decor_x = pill_x + pill + 45 if pill_x < WIDTH / 2 else pill_x - 45
        out.append(f'<text x="{decor_x:.1f}" y="{b.top + 30:.1f}" class="decor">{decor}</text>')

    for lab in labels:
        lines = _wrap(lab.name)
        top = lab.y - 6 * len(lines)
        for i, line in enumerate(lines):
            out.append(f'<text x="14" y="{top + i * 13:.1f}" class="topic">{escape(line)}</text>')
        out.append(
            f'<text x="14" y="{top + len(lines) * 13:.1f}" class="topic-count">{lab.done}/{lab.total}</text>'
        )

    points = [(p.x, p.y) for p in placed]
    path = _smooth_path(points)
    out.append(
        f'<path d="{path}" fill="none" stroke="#D9C3A0" stroke-width="12" stroke-linecap="round"/>'
    )
    # 走過的路：前後兩關都做完的那一段畫成金色
    for a, b in zip(placed, placed[1:]):
        if a.stage.done and b.stage.done:
            seg = _smooth_path([(a.x, a.y), (b.x, b.y)])
            out.append(
                f'<path class="walked" d="{seg}" fill="none" stroke="{PATH_DONE}" '
                f'stroke-width="12" stroke-linecap="round"/>'
            )
    out.append(
        f'<path d="{path}" fill="none" stroke="#FFF8EA" stroke-width="3" '
        f'stroke-dasharray="2 9" stroke-linecap="round"/>'
    )

    if placed:
        # 終點的獎盃放在最後一關旁邊（往空的那一側）
        last = placed[-1]
        gx = last.x + 40 if last.x + 40 < WIDTH - 12 else last.x - 40
        out.append(f'<text x="{gx:.1f}" y="{last.y + 8:.1f}" class="goal">🏆</text>')

    for level, p in enumerate(placed, start=1):
        tid = p.stage.task_id
        tag = "today" if tid in today_ids else "overdue" if tid in overdue_ids else ""
        out.append(
            _node(p, safe_url(urls.get(tid)), tid == here_id, tag, level=level, focus=tid in focus_ids)
        )

    spot = next((p for p in placed if p.stage.task_id == here_id), None)
    if spot is not None:
        # 光圈標出是哪一關；小熊站在它左邊的小路上（兩關中間的空檔）。
        # 站在關卡正上方會壓到上一排的關卡——排距只有 ROW_HEIGHT。
        out.append(
            f'<circle class="halo" cx="{spot.x:.1f}" cy="{spot.y:.1f}" r="{NODE_R + 8}" fill="none" '
            f'stroke="#F28C8C" stroke-width="3" stroke-dasharray="6 5" style="pointer-events:none"/>'
        )
        out.append(_bear(spot.x - BEAR_OFFSET, spot.y - 6))

    out.append("</svg>")
    return "".join(out)
