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

農村風格（同日第三輪，使用者給了參考圖：俯視的溫馨農村）：

- 整張圖是一片有紋理的草地，各區之間用木頭圍籬分開（小路經過的地方留缺口），區名寫在木牌上。
- 小路是寬的沙土路（深色路緣＋淺色路面）；空格放圍籬農田、麥田、小屋、池塘、樹叢——
  圖形都在 ``core/farm_art.py``，原創向量圖形，光從左上來、影子往右下。

立體感（同日第二輪，使用者要「更像遊戲、立體」）：

- 關卡是**有厚度的圓台**：上面是關卡面，下面一圈較深的側邊，再下面是影子；滑鼠移上去會浮起來。
- 小路是**有厚度的路面**（下面一條深色的路基）。
- 每一區是**漸層的地面**，下緣一條懸崖邊，像一層一層疊上去的地形。
- 整張圖可以**像桌遊盤面一樣往後傾斜**（``tilt=True``，用 CSS 的 3D 透視）；
  點擊與小卡的位置靠瀏覽器算實際座標，傾斜後照樣對得上。
- 小熊會跳、光圈會呼吸；系統設定「減少動態」時不動。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from html import escape
from urllib.parse import urlsplit

from core import farm_art
from core.game_map import Stage

WIDTH = 640
GUTTER = 150  # 左邊放主題名的欄位寬
PER_ROW = 5
COLUMNS: tuple[float, ...] = (200, 300, 400, 500, 600)
#: 重點路線沒有左欄的主題名，關卡往左展開用滿寬度
FOCUS_COLUMNS: tuple[float, ...] = (110, 232, 354, 476, 598)
ROW_HEIGHT = 72  # 小島＋地標（放大 1.3 倍）最高到中心上方約 42，排距要讓它碰不到上一排的編號小牌
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
#: 關卡圓台的厚度（側邊露出來的高度）
DEPTH = 8
#: 路基比路面低多少
ROAD_DEPTH = 5

#: Boss 關（Hard）比一般關卡大多少
BOSS_EXTRA = 4

def scenery_spots(placed: list[Placed], columns: tuple[float, ...]) -> list[tuple[float, float, int]]:
    """（x, y, 區號）：可以放景物的空格。

    只看「沒排滿的那一排」：一排裡關卡之間一定有小路，空格只會出現在這一排的關卡
    範圍之外；另外排除小路往上一排（或上一區）走時會經過的那一側。
    """
    rows: dict[float, list[Placed]] = {}
    for p in placed:
        rows.setdefault(p.y, []).append(p)
    order = {id(p): i for i, p in enumerate(placed)}
    spots = []
    for y, members in rows.items():
        if len(members) >= len(columns):
            continue
        xs = [m.x for m in members]
        lo, hi = min(xs), max(xs)
        # 這一排的前一關與後一關（路從哪裡進來、往哪裡出去）的 x 也算在路的範圍裡
        first, last = min(order[id(m)] for m in members), max(order[id(m)] for m in members)
        for neighbor in (first - 1, last + 1):
            if 0 <= neighbor < len(placed):
                lo, hi = min(lo, placed[neighbor].x), max(hi, placed[neighbor].x)
        for c in columns:
            if c < lo - 40 or c > hi + 40:
                spots.append((c, y, members[0].stage.zone))
    return spots



def _shade(hex_color: str, factor: float) -> str:
    """把顏色調暗（factor < 1）或調亮（factor > 1，往白色靠）。給立體的側邊與高光用。"""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    if factor < 1:
        r, g, b = (int(c * factor) for c in (r, g, b))
    else:
        r, g, b = (int(c + (255 - c) * (factor - 1)) for c in (r, g, b))
    return f"#{r:02X}{g:02X}{b:02X}"


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


def _curve_segments(points: list[tuple[float, float]]) -> list[str]:
    """每兩個相鄰關卡之間的一段三次貝茲（Catmull-Rom 換算），第 i 段從第 i 關到第 i+1 關。

    分段算出來，是為了讓「走過的路」能只重畫其中幾段，而且跟整條小路的弧度完全重疊。
    """
    segments = []
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        segments.append(
            f"M{p1[0]:.1f},{p1[1]:.1f} "
            f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}"
        )
    return segments


def road_samples(points: list[tuple[float, float]], steps: int = 12) -> list[tuple[float, float]]:
    """把整條小路（Catmull-Rom 曲線）取樣成一串點，用來檢查景物有沒有壓到路。"""
    out: list[tuple[float, float]] = list(points[:1])
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        for k in range(1, steps + 1):
            t = k / steps
            a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3
            out.append((
                a * p1[0] + b * c1[0] + c * c2[0] + d * p2[0],
                a * p1[1] + b * c1[1] + c * c2[1] + d * p2[1],
            ))
    return out


#: 小景物離路中心、離關卡中心至少要多遠（路寬 20、關卡半徑最大 21＋厚度）
ROAD_CLEARANCE = 22
NODE_CLEARANCE = 46  # 小島半徑 24～27，地標放大後高到中心上方約 42


def filler_spots(
    placed: list[Placed], bands: list[Band], columns: tuple[float, ...], here: Placed | None = None
) -> list[tuple[float, float]]:
    """兩排之間的草地上可以放小樹叢、花的位置：每兩欄中間、每兩排中間各試一個點，
    離小路、關卡、區名那一列都夠遠才放。小熊站的位置也避開。"""
    if not placed:
        return []
    road = road_samples([(p.x, p.y) for p in placed])
    ys = sorted({p.y for p in placed})
    candidates = [((a + b) / 2, (y1 + y2) / 2) for a, b in zip(columns, columns[1:])
                  for y1, y2 in zip(ys, ys[1:])]
    candidates += [(x, ys[0] - ROW_HEIGHT / 2) for x in columns]  # 最上面一排的上方
    spots = []
    for x, y in candidates:
        if any(b.top <= y <= b.top + BAND_HEADER for b in bands):
            continue  # 區名那一列留給木牌與圍籬
        if min((x - rx) ** 2 + (y - ry) ** 2 for rx, ry in road) < ROAD_CLEARANCE**2:
            continue
        if any((x - p.x) ** 2 + (y - p.y) ** 2 < NODE_CLEARANCE**2 for p in placed):
            continue
        if here is not None and abs(x - (here.x - BEAR_OFFSET)) < 28 and abs(y - here.y) < 36:
            continue
        spots.append((x, y))
    return spots


def _smooth_path(points: list[tuple[float, float]]) -> str:
    """Catmull-Rom 轉三次貝茲，讓小路是圓滑的曲線而不是折線。"""
    if not points:
        return ""
    head = f"M{points[0][0]:.1f},{points[0][1]:.1f}"
    return " ".join([head] + [seg.split(" ", 1)[1] for seg in _curve_segments(points)])


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
          level: int = 0, focus: bool = False, adventuring: bool = False) -> str:
    """一關＝一座浮空小島：島上的地標依主題換造型（Boss 關是城堡），關卡編號寫在島前的小牌上。"""
    s = p.stage
    x, y = p.x, p.y
    letter, color = DIFFICULTY_MARK.get(s.difficulty, ("?", INK))
    boss = s.difficulty == "Hard"
    status = "已完成" if s.done else "未完成"
    when = {"today": "，今天的任務", "overdue": "，過期還沒做"}.get(plan_tag, "")
    tip = (
        f"第 {level} 關：{s.title}（{s.difficulty}{'・Boss' if boss else ''}，{status}{when}"
        f"{'，冒險中' if adventuring else ''}）"
    )

    classes = (
        "stage" + (" done" if s.done else "") + (" here" if is_here else "")
        + (" boss" if boss else "") + (" adventuring" if adventuring else "")
        + (f" {plan_tag}" if plan_tag else "")
    )
    # 有網址就是真的 <a>：就算畫面的 JavaScript 沒跑起來，點下去照樣能開題目
    href = (
        f' href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer"'
        if url
        else ""
    )
    tag = "a" if url else "g"
    rx = 27 if boss else 24
    iy = y + 4  # 小島草地的中心
    parts = [
        f'<{tag} class="{classes}" data-task="{int(s.task_id)}" data-difficulty="{letter}"{href}>',
        f"<title>{escape(tip)}</title>",
        # 透明的點擊範圍：整座小島加上地標都點得到
        f'<rect x="{x - rx:.1f}" y="{y - 34:.1f}" width="{2 * rx}" height="58" fill="#000" opacity="0"/>',
    ]
    if plan_tag:
        ring = TODAY_RING if plan_tag == "today" else OVERDUE_RING
        parts.append(
            f'<ellipse class="plan-ring" cx="{x:.1f}" cy="{iy:.1f}" rx="{rx + 6}" ry="16" fill="none" '
            f'stroke="{ring}" stroke-width="3"/>'
        )
    top = "#EBD27A" if s.done else farm_art.GRASS_LIGHT
    parts.append(
        '<g class="platform">'
        + farm_art.island(x, iy, rx=rx, ry=11, top=top)
        + farm_art.island_accessory(x, iy, s.task_id)
        + farm_art.landmark(farm_art.landmark_kind(s.topic_id), x, iy + 2, boss=boss, variant=s.task_id)
    )
    # 關卡編號的小牌：做完是金色打勾，沒做是白底、外框是難度顏色
    plate_fill, plate_rim, text_fill = (GOLD, "#C98A1E", INK) if s.done else ("#FFFFFF", color, color)
    parts.append(
        f'<circle cx="{x + 1:.1f}" cy="{y + 19:.1f}" r="10" fill="#000" opacity="0.2"/>'
        f'<circle cx="{x:.1f}" cy="{y + 17:.1f}" r="10" fill="{plate_fill}" stroke="{plate_rim}" stroke-width="2.5"/>'
        f'<text x="{x:.1f}" y="{y + 21:.1f}" class="mark" fill="{text_fill}">{"✓" if s.done else level}</text>'
        "</g>"
    )
    if s.done:
        # 做完的關卡插一面小金旗
        parts.append(
            f'<line x1="{x + rx - 6:.1f}" y1="{iy - 2:.1f}" x2="{x + rx - 6:.1f}" y2="{iy - 20:.1f}" '
            f'stroke="{farm_art.WOOD_DARK}" stroke-width="1.5"/>'
            f'<polygon points="{x + rx - 6:.1f},{iy - 20:.1f} {x + rx + 4:.1f},{iy - 17:.1f} '
            f'{x + rx - 6:.1f},{iy - 14:.1f}" fill="{GOLD}" stroke="#C98A1E" stroke-width="0.8"/>'
        )
    if boss:
        parts.append(f'<text x="{x - 14:.1f}" y="{y + 22:.1f}" class="crown">👑</text>')
    if focus:
        parts.append(f'<text x="{x + rx - 4:.1f}" y="{y - 6:.1f}" class="focus-star">⭐</text>')
    if adventuring:
        # 冒險中的旗子＋計時（秒數由頁面的 JavaScript 即時更新 .adv-time）
        # 放在小島右側、跟小島同高：放上面會蓋到上一排的編號小牌和自己的地標
        fx = x + rx + 2
        parts.append(
            f'<g class="adventure-flag" style="pointer-events:none">'
            f'<polygon points="{fx:.1f},{iy:.1f} {fx + 6:.1f},{iy - 5:.1f} {fx + 6:.1f},{iy + 5:.1f}" fill="#2F7FD1"/>'
            f'<rect x="{fx + 5:.1f}" y="{iy - 9:.1f}" width="50" height="18" rx="9" fill="#2F7FD1"/>'
            f'<text x="{fx + 30:.1f}" y="{iy + 4:.1f}" class="adv-time">🧭 冒險中</text>'
            "</g>"
        )
    parts.append(f"</{tag}>")
    return "".join(parts)


def _exit_gap(band: Band, placed: list[Placed]) -> tuple[float, float] | None:
    """色帶上緣被小路穿過的 x 範圍（這一區最後一關 → 上一區第一關），圍籬要在這裡留缺口。"""
    members = [p for p in placed if p.stage.zone == band.zone]
    above = [p for p in placed if p.y < band.top]
    if not (members and above):
        return None
    lo, hi = sorted((members[-1].x, above[0].x))
    return lo - 30, hi + 30


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
    tilt: bool = False,
    adventure_id: int | None = None,
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
        f'width="100%" class="board{" tilt" if tilt else ""}" '
        f'style="font-family:system-ui,sans-serif;display:block">',
        "<style>"
        f".topic{{font-size:11px;font-weight:600;fill:{INK}}}"
        f".topic-count{{font-size:10px;fill:{INK};opacity:0.7}}"
        ".mark{font-size:11px;font-weight:800;text-anchor:middle}"
        ".zone{font-size:14px;font-weight:700;fill:#FFF6E0;text-anchor:middle}"
        ".crown{font-size:12px;text-anchor:middle;pointer-events:none}"
        ".adv-time{font-size:9.5px;font-weight:700;fill:#fff;text-anchor:middle;font-variant-numeric:tabular-nums}"
        "@keyframes advpulse{0%,100%{transform:translateY(0)}50%{transform:translateY(-3px)}}"
        ".adventure-flag{animation:advpulse 1.2s ease-in-out infinite}"
        ".focus-star{font-size:10px;text-anchor:middle;pointer-events:none}"
        ".goal{font-size:22px;text-anchor:middle;pointer-events:none}"
        ".stage{cursor:pointer}"
        # 滑鼠移上去，圓台浮起來（影子不動）
        ".stage .platform{transition:transform .15s ease-out}"
        ".stage:hover .platform,.stage:focus .platform{transform:translateY(-4px)}"
        "@keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-5px)}}"
        # 動物：大部分時間站著，偶爾跳一下
        "@keyframes hop{0%,72%,100%{transform:translateY(0)}80%{transform:translateY(-5px)}88%{transform:translateY(0)}}"
        ".npc-hop{animation:hop 2.4s ease-in-out infinite}"
        ".npc{cursor:help}"
        "@keyframes breathe{0%,100%{opacity:.95}50%{opacity:.35}}"
        ".bear-bob{animation:bob 1.6s ease-in-out infinite}"
        ".halo{animation:breathe 1.8s ease-in-out infinite}"
        "@media (prefers-reduced-motion: reduce){.bear-bob,.halo,.adventure-flag,.npc-hop{animation:none}"
        ".stage .platform{transition:none}}"
        "</style>",
    ]
    out.append(
        "<defs>"
        + farm_art.grass_pattern("grass")
        # 關卡面：中間亮、邊緣暗的放射漸層，看起來是微微鼓起的圓面
        + '<radialGradient id="faceOpen" cx="0.38" cy="0.32" r="0.75">'
        '<stop offset="0" stop-color="#FFFFFF"/><stop offset="1" stop-color="#E9DDC2"/></radialGradient>'
        '<radialGradient id="faceDone" cx="0.38" cy="0.32" r="0.75">'
        '<stop offset="0" stop-color="#FFEFA8"/><stop offset="1" stop-color="#E5A93A"/></radialGradient>'
        "</defs>"
    )
    # 整張圖一片草地；每一區疊一層很淡的色調，分得出區又不搶戲
    out.append(f'<rect x="0" y="0" width="{WIDTH}" height="{height:.0f}" fill="url(#grass)"/>')

    for b in bands:
        name, bg, accent = zone_theme(b.zone)
        if zone_names and b.zone in zone_names:
            name = zone_names[b.zone]
        done, total = by_zone_done[b.zone]
        label = f"{name}　{done}/{total}"
        pill = 24 + len(label) * 13
        pill_x = _label_x(b, placed, pill)
        out.append(
            f'<rect x="0" y="{b.top:.1f}" width="{WIDTH}" height="{b.bottom - b.top:.1f}" '
            f'fill="{accent}" opacity="0.10"/>'
        )
        if by_topic:
            # 左欄的主題名底下墊一條羊皮紙色，草地紋理上的字才讀得清楚
            out.append(
                f'<rect x="6" y="{b.top + BAND_HEADER - 4:.1f}" width="{GUTTER - 18}" '
                f'height="{b.bottom - b.top - BAND_HEADER:.1f}" rx="8" fill="#F7EEDB" opacity="0.88"/>'
            )
        # 區與區之間的木頭圍籬，小路穿過的地方留缺口
        if b.top > 0:
            gap = _exit_gap(b, placed)
            if gap is None:
                out.append(farm_art.fence(8, WIDTH - 8, b.top + 4))
            else:
                lo, hi = gap
                if lo > 24:
                    out.append(farm_art.fence(8, lo, b.top + 4))
                if hi < WIDTH - 24:
                    out.append(farm_art.fence(hi, WIDTH - 8, b.top + 4))
        # 區名寫在木牌上
        out.append(
            farm_art.sign(pill_x, b.top + 9, pill)
            + f'<text x="{pill_x + pill / 2:.1f}" y="{b.top + 27:.1f}" class="zone">{escape(label)}</text>'
        )
        # 木牌旁邊放一小組景物（朝地圖中間）：區名本來就放在小路離開這一區的另一側
        # （_label_x），放在另一側會壓到往上走的那段路
        decor_x = pill_x + pill + 45 if pill_x < WIDTH / 2 else pill_x - 45
        greeter = ("rabbit", "sheep", "cow", "pig")[b.zone % 4]
        out.append(
            f'<g class="decor" data-x="{decor_x:.1f}">'
            + farm_art.tree(decor_x - 16, b.top + 34, 0.75)
            + farm_art.npc(greeter, decor_x + 6, b.top + 36, delay=b.zone * 0.8)
            + farm_art.flowers(decor_x + 26, b.top + 28, seed=b.zone)
            + "</g>"
        )

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
        # 沙土路：往右下偏一點的深色路緣當影子，上面是寬的路面，中間一條踩出來的淺色
        f'<path d="{path}" fill="none" stroke="{farm_art.DIRT_EDGE}" stroke-width="24" stroke-linecap="round" '
        f'stroke-linejoin="round" transform="translate(1,{ROAD_DEPTH - 2})" opacity="0.9"/>'
        f'<path d="{path}" fill="none" stroke="{farm_art.DIRT}" stroke-width="20" stroke-linecap="round" '
        f'stroke-linejoin="round"/>'
    )
    # 走過的路：前後兩關都做完的那一段畫成金色（用整條路的同一段曲線，弧度才對得上）
    for (a, b), seg in zip(zip(placed, placed[1:]), _curve_segments(points)):
        if a.stage.done and b.stage.done:
            out.append(
                f'<path class="walked" d="{seg}" fill="none" stroke="{PATH_DONE}" '
                f'stroke-width="20" stroke-linecap="round"/>'
            )
    out.append(
        f'<path d="{path}" fill="none" stroke="{farm_art.DIRT_LIGHT}" stroke-width="6" '
        f'stroke-linecap="round" opacity="0.7"/>'
    )

    # 空格裡的景物：有影子的樹、石頭、花，讓盤面不空
    columns_used = FOCUS_COLUMNS if not by_topic else COLUMNS
    for i, (sx, sy, zone) in enumerate(scenery_spots(placed, columns_used)):
        out.append(farm_art.scenery_piece(i + zone, sx, sy, side=-1 if sx > WIDTH - 90 else 1))
    # 兩排之間的草地：小樹叢、花、小樹輪流放
    here_spot = next((p for p in placed if p.stage.task_id == here_id), None)
    for i, (fx, fy) in enumerate(filler_spots(placed, bands, columns_used, here_spot)):
        kind = i % 3
        if kind == 1:
            # 每三格有一格是動物，種類輪流換
            animal = farm_art.NPC_ORDER[(i // 3) % len(farm_art.NPC_ORDER)]
            out.append(farm_art.npc(animal, fx, fy + 8, delay=(i * 0.37) % 2.4))
        else:
            out.append(
                farm_art.bush(fx, fy + 4) if (i // 3) % 2 == 0 and kind == 0
                else farm_art.flowers(fx, fy, seed=i) if kind == 2
                else farm_art.tree(fx, fy + 6, 0.6)
            )

    if placed:
        # 終點的獎盃放在最後一關旁邊。小熊站在關卡左邊（x - BEAR_OFFSET），
        # 所以右邊放不下、而小熊又正好站在最後一關時，就不畫獎盃，免得疊在小熊身上
        last = placed[-1]
        if last.x + 40 < WIDTH - 12:
            gx: float | None = last.x + 40
        elif last.stage.task_id != here_id:
            gx = last.x - 40
        else:
            gx = None
        if gx is not None:
            out.append(f'<text x="{gx:.1f}" y="{last.y + 8:.1f}" class="goal">🏆</text>')

    for level, p in enumerate(placed, start=1):
        tid = p.stage.task_id
        tag = "today" if tid in today_ids else "overdue" if tid in overdue_ids else ""
        out.append(
            _node(p, safe_url(urls.get(tid)), tid == here_id, tag, level=level,
                  focus=tid in focus_ids, adventuring=tid == adventure_id)
        )

    spot = next((p for p in placed if p.stage.task_id == here_id), None)
    if spot is not None:
        # 光圈標出是哪一關；小熊站在它左邊的小路上（兩關中間的空檔）。
        # 站在關卡正上方會壓到上一排的關卡——排距只有 ROW_HEIGHT。
        out.append(
            f'<ellipse class="halo" cx="{spot.x:.1f}" cy="{spot.y + 4:.1f}" rx="34" ry="19" fill="none" '
            f'stroke="#F28C8C" stroke-width="3" stroke-dasharray="6 5" style="pointer-events:none"/>'
        )
        out.append(f'<g class="bear-bob">{_bear(spot.x - BEAR_OFFSET, spot.y - 6)}</g>')

    out.append("</svg>")
    return "".join(out)
