"""農村風格的小圖形：樹、小屋、農田、池塘、花叢、圍籬、木牌。

全部是原創的簡單向量圖形（圓、多邊形、線），不用外部圖檔或字型。立體感只靠兩件事：
**光從左上來**（左上亮、右下暗），以及**每個東西底下都有影子**（往右下偏）。

每個函式回傳一段 SVG 字串，座標是物件的「落地點」（影子的中心附近）。
``pointer-events:none``：景物不能擋住關卡的點擊。
"""

from __future__ import annotations

#: 顏色（溫暖的農村配色）
GRASS = "#8CC152"
GRASS_DARK = "#6FA33D"
GRASS_LIGHT = "#A9D46E"
DIRT = "#E2C08A"
DIRT_EDGE = "#B98D55"
DIRT_LIGHT = "#F0D6A6"
WOOD = "#9A6A3A"
WOOD_DARK = "#6E4826"
LEAF = "#5A9E3F"
LEAF_DARK = "#3D7330"
LEAF_LIGHT = "#82C35A"
TRUNK = "#7A5230"
WATER = "#5AA7D6"
WATER_LIGHT = "#9ED3F0"
SOIL = "#9C7448"
SOIL_DARK = "#7C5A35"
WHEAT = "#E8C150"
WHEAT_DARK = "#C99B2E"
WALL = "#F1E2C0"

#: 屋頂顏色輪流用（每個是（亮面, 暗面））
ROOFS: tuple[tuple[str, str], ...] = (
    ("#D9573F", "#A93D2B"),
    ("#4F86C6", "#356196"),
    ("#E39A3B", "#B8742A"),
    ("#6FAA4E", "#4D823A"),
    ("#8E64B8", "#674590"),
)

_NOPTR = 'style="pointer-events:none"'


def shadow(x: float, y: float, rx: float, ry: float, opacity: float = 0.22) -> str:
    return f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" fill="#000" opacity="{opacity}"/>'


def tree(x: float, y: float, scale: float = 1.0) -> str:
    """圓圓的樹：深色底、亮色樹冠、左上高光，影子往右下。"""
    r = 12 * scale
    return (
        f'<g class="scenery tree" {_NOPTR}>'
        + shadow(x + 5 * scale, y + 9 * scale, r * 1.05, r * 0.45)
        + f'<rect x="{x - 2 * scale:.1f}" y="{y:.1f}" width="{4 * scale:.1f}" height="{7 * scale:.1f}" fill="{TRUNK}"/>'
        f'<circle cx="{x + 2 * scale:.1f}" cy="{y - 6 * scale:.1f}" r="{r:.1f}" fill="{LEAF_DARK}"/>'
        f'<circle cx="{x:.1f}" cy="{y - 8 * scale:.1f}" r="{r * 0.92:.1f}" fill="{LEAF}"/>'
        f'<circle cx="{x - 4 * scale:.1f}" cy="{y - 12 * scale:.1f}" r="{r * 0.4:.1f}" fill="{LEAF_LIGHT}" opacity="0.9"/>'
        "</g>"
    )


def bush(x: float, y: float) -> str:
    return (
        f'<g class="scenery bush" {_NOPTR}>'
        + shadow(x + 3, y + 4, 9, 3.5)
        + f'<circle cx="{x + 1:.1f}" cy="{y:.1f}" r="7" fill="{LEAF_DARK}"/>'
        f'<circle cx="{x:.1f}" cy="{y - 1:.1f}" r="6" fill="{LEAF}"/>'
        f'<circle cx="{x - 2:.1f}" cy="{y - 3:.1f}" r="2.5" fill="{LEAF_LIGHT}"/>'
        "</g>"
    )


def house(x: float, y: float, variant: int = 0) -> str:
    """俯視的小屋：四面斜屋頂，左邊與上面亮、右邊與下面暗，屋脊一條線，前面露出一截牆。"""
    light, dark = ROOFS[variant % len(ROOFS)]
    w, h = 30, 24
    left, top = x - w / 2, y - h
    right, bottom = x + w / 2, y
    ridge_y = top + h / 2
    return (
        f'<g class="scenery house" {_NOPTR}>'
        f'<rect x="{left + 5:.1f}" y="{top + 6:.1f}" width="{w}" height="{h + 4}" fill="#000" opacity="0.2" rx="2"/>'
        # 牆（屋簷下露出來的一截）
        f'<rect x="{left + 2:.1f}" y="{bottom - 2:.1f}" width="{w - 4}" height="6" fill="{WALL}"/>'
        f'<rect x="{x - 3:.1f}" y="{bottom - 1:.1f}" width="6" height="5" fill="{WOOD_DARK}"/>'
        # 屋頂四個斜面
        f'<polygon points="{left},{top} {right},{top} {right - 8},{ridge_y} {left + 8},{ridge_y}" fill="{light}"/>'
        f'<polygon points="{left},{bottom} {right},{bottom} {right - 8},{ridge_y} {left + 8},{ridge_y}" fill="{dark}"/>'
        f'<polygon points="{left},{top} {left + 8},{ridge_y} {left},{bottom}" fill="{light}" opacity="0.85"/>'
        f'<polygon points="{right},{top} {right - 8},{ridge_y} {right},{bottom}" fill="{dark}" opacity="0.9"/>'
        f'<line x1="{left + 8}" y1="{ridge_y}" x2="{right - 8}" y2="{ridge_y}" stroke="{dark}" stroke-width="1.5"/>'
        "</g>"
    )


def field(x: float, y: float, kind: int = 0, w: float = 58, h: float = 34) -> str:
    """圍籬圍起來的一塊田：深色土、幾排作物（綠色菜苗或金色麥子），四角有木樁。"""
    left, top = x - w / 2, y - h / 2
    rows = []
    if kind % 2 == 0:
        # 一排一排的菜苗
        for row in range(3):
            cy = top + 8 + row * 9
            for col in range(6):
                cx = left + 7 + col * 9
                rows.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="2.6" fill="{LEAF}"/>')
                rows.append(f'<circle cx="{cx - 0.8:.1f}" cy="{cy - 0.8:.1f}" r="1" fill="{LEAF_LIGHT}"/>')
        soil = SOIL
    else:
        # 麥田：直條紋
        for col in range(7):
            cx = left + 5 + col * 8
            rows.append(
                f'<rect x="{cx:.1f}" y="{top + 4:.1f}" width="5" height="{h - 8:.1f}" fill="{WHEAT}" rx="2"/>'
                f'<rect x="{cx + 3:.1f}" y="{top + 4:.1f}" width="2" height="{h - 8:.1f}" fill="{WHEAT_DARK}" rx="1"/>'
            )
        soil = SOIL_DARK
    posts = "".join(
        f'<rect x="{px - 1.5:.1f}" y="{py - 3:.1f}" width="3" height="5" fill="{WOOD_DARK}"/>'
        for px, py in ((left, top), (left + w, top), (left, top + h), (left + w, top + h))
    )
    return (
        f'<g class="scenery field" {_NOPTR}>'
        f'<rect x="{left + 3:.1f}" y="{top + 4:.1f}" width="{w}" height="{h}" fill="#000" opacity="0.16" rx="3"/>'
        f'<rect x="{left:.1f}" y="{top:.1f}" width="{w}" height="{h}" fill="{soil}" rx="3"/>'
        + "".join(rows)
        + f'<rect x="{left:.1f}" y="{top:.1f}" width="{w}" height="{h}" fill="none" stroke="{WOOD}" stroke-width="2" rx="3"/>'
        + posts
        + "</g>"
    )


def pond(x: float, y: float) -> str:
    return (
        f'<g class="scenery pond" {_NOPTR}>'
        f'<ellipse cx="{x:.1f}" cy="{y + 2:.1f}" rx="26" ry="13" fill="{GRASS_DARK}"/>'
        f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="24" ry="11" fill="{WATER}"/>'
        f'<ellipse cx="{x - 7:.1f}" cy="{y - 3:.1f}" rx="9" ry="3" fill="{WATER_LIGHT}" opacity="0.8"/>'
        f'<circle cx="{x + 13:.1f}" cy="{y + 3:.1f}" r="3" fill="{LEAF}"/>'
        "</g>"
    )


def flowers(x: float, y: float, seed: int = 0) -> str:
    """一小叢花。``seed`` 決定顏色排列，讓每一叢不一樣（但每次畫都一樣）。"""
    colors = ("#F28CB1", "#FFFFFF", "#FFD34E", "#E86A5C")
    dots = []
    for i, (dx, dy) in enumerate(((-6, 0), (0, -4), (6, 1), (-2, 5), (4, 6))):
        c = colors[(i + seed) % len(colors)]
        dots.append(f'<circle cx="{x + dx:.1f}" cy="{y + dy:.1f}" r="2.3" fill="{c}"/>')
    return f'<g class="scenery flowers" {_NOPTR}>' + "".join(dots) + "</g>"


def fence(x1: float, x2: float, y: float) -> str:
    """一段橫的木頭圍籬（兩條橫木＋一根根木樁），區與區之間的分界。"""
    posts = []
    x = x1
    while x <= x2:
        posts.append(
            f'<rect x="{x - 1.5:.1f}" y="{y - 7:.1f}" width="3" height="10" fill="{WOOD_DARK}"/>'
        )
        x += 16
    return (
        f'<g class="fence" {_NOPTR}>'
        f'<rect x="{x1:.1f}" y="{y - 5:.1f}" width="{x2 - x1:.1f}" height="2" fill="{WOOD}"/>'
        f'<rect x="{x1:.1f}" y="{y - 1:.1f}" width="{x2 - x1:.1f}" height="2" fill="{WOOD}"/>'
        + "".join(posts)
        + "</g>"
    )


def sign(x: float, y: float, width: float) -> str:
    """木牌底座（區名寫在上面）：有厚度的木板＋兩根柱子。文字由呼叫端另外畫。"""
    return (
        f'<rect x="{x + 10:.1f}" y="{y + 20:.1f}" width="4" height="12" fill="{WOOD_DARK}"/>'
        f'<rect x="{x + width - 14:.1f}" y="{y + 20:.1f}" width="4" height="12" fill="{WOOD_DARK}"/>'
        f'<rect x="{x + 2:.1f}" y="{y + 4:.1f}" width="{width}" height="26" rx="6" fill="#000" opacity="0.2"/>'
        f'<rect x="{x:.1f}" y="{y + 3:.1f}" width="{width}" height="26" rx="6" fill="{WOOD_DARK}"/>'
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{width}" height="26" rx="6" fill="{WOOD}"/>'
        f'<rect x="{x + 4:.1f}" y="{y + 3:.1f}" width="{width - 8}" height="3" rx="1.5" fill="#B88550" opacity="0.8"/>'
    )


def grass_pattern(pattern_id: str = "grass") -> str:
    """草地的紋理（一小格重複鋪）：幾撮深淺不同的草，讓大片綠色不會太平。"""
    return (
        f'<pattern id="{pattern_id}" width="36" height="36" patternUnits="userSpaceOnUse">'
        f'<rect width="36" height="36" fill="{GRASS}"/>'
        f'<path d="M5 9 l2 -4 l2 4 M22 25 l2 -4 l2 4 M28 6 l1.5 -3 l1.5 3" stroke="{GRASS_DARK}" '
        f'stroke-width="1.4" fill="none" stroke-linecap="round"/>'
        f'<path d="M14 18 l1.5 -3 l1.5 3 M3 30 l1.5 -3 l1.5 3" stroke="{GRASS_LIGHT}" '
        f'stroke-width="1.2" fill="none" stroke-linecap="round"/>'
        "</pattern>"
    )


def scenery_piece(kind: int, x: float, y: float) -> str:
    """空格裡放哪一種景物：田、小屋＋樹、池塘、樹林、麥田……輪流放。"""
    k = kind % 6
    if k == 0:
        return field(x, y, kind=0)
    if k == 1:
        return house(x - 8, y + 8, variant=kind) + tree(x + 22, y + 10, 0.8)
    if k == 2:
        return pond(x, y + 2) + flowers(x + 30, y - 10, seed=kind)
    if k == 3:
        return tree(x - 16, y + 8) + tree(x + 12, y + 12, 0.85) + bush(x + 2, y - 10)
    if k == 4:
        return field(x, y, kind=1)
    return house(x, y + 8, variant=kind) + flowers(x - 26, y + 4, seed=kind) + bush(x + 24, y + 6)


# ---------------------------------------------------------------------------
# 關卡地標：每一關是一座浮空小島，上面放一個小建築（依主題換造型）
# ---------------------------------------------------------------------------

STONE = "#C9C2B5"
STONE_DARK = "#9A9286"
CLIFF = "#A8774A"
CLIFF_DARK = "#7E5634"


def island(x: float, y: float, rx: float = 26, ry: float = 12, top: str = GRASS_LIGHT) -> str:
    """浮空小島：上面是草地橢圓，下面是一圈土壤懸崖（越往下越窄），底下有影子。"""
    depth = 11
    return (
        shadow(x + 5, y + depth + 12, rx * 0.9, ry * 0.55, 0.2)
        # 懸崖：一個往下收的形狀，左邊受光、右邊背光
        + f'<path d="M{x - rx:.1f},{y:.1f} Q{x - rx * 0.8:.1f},{y + depth + 6:.1f} {x:.1f},{y + depth + 9:.1f} '
        f'Q{x + rx * 0.8:.1f},{y + depth + 6:.1f} {x + rx:.1f},{y:.1f} Z" fill="{CLIFF}"/>'
        + f'<path d="M{x:.1f},{y + depth + 9:.1f} Q{x + rx * 0.8:.1f},{y + depth + 6:.1f} {x + rx:.1f},{y:.1f} '
        f'L{x + rx * 0.2:.1f},{y + 2:.1f} Z" fill="{CLIFF_DARK}" opacity="0.6"/>'
        + f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" fill="{top}" '
        f'stroke="{GRASS_DARK}" stroke-width="1.5"/>'
        + f'<ellipse cx="{x - rx * 0.25:.1f}" cy="{y - ry * 0.3:.1f}" rx="{rx * 0.55:.1f}" ry="{ry * 0.45:.1f}" '
        f'fill="#FFFFFF" opacity="0.18"/>'
    )


def _windmill(x: float, y: float) -> str:
    return (
        f'<polygon points="{x - 7},{y} {x + 7},{y} {x + 4},{y - 22} {x - 4},{y - 22}" fill="{WALL}"/>'
        f'<polygon points="{x + 1},{y} {x + 7},{y} {x + 4},{y - 22} {x + 1},{y - 22}" fill="#D8C59D"/>'
        f'<polygon points="{x - 6},{y - 22} {x + 6},{y - 22} {x},{y - 29}" fill="#C0533C"/>'
        f'<rect x="{x - 2}" y="{y - 6}" width="4" height="6" fill="{WOOD_DARK}"/>'
        f'<g stroke="{WOOD_DARK}" stroke-width="2.4" stroke-linecap="round">'
        f'<line x1="{x - 11}" y1="{y - 33}" x2="{x + 11}" y2="{y - 13}"/>'
        f'<line x1="{x + 11}" y1="{y - 33}" x2="{x - 11}" y2="{y - 13}"/></g>'
        f'<circle cx="{x}" cy="{y - 23}" r="2" fill="{WOOD_DARK}"/>'
    )


def _well(x: float, y: float) -> str:
    return (
        f'<ellipse cx="{x}" cy="{y - 2}" rx="9" ry="4" fill="{STONE_DARK}"/>'
        f'<rect x="{x - 9}" y="{y - 10}" width="18" height="8" fill="{STONE}"/>'
        f'<rect x="{x + 1}" y="{y - 10}" width="8" height="8" fill="{STONE_DARK}" opacity="0.5"/>'
        f'<ellipse cx="{x}" cy="{y - 10}" rx="9" ry="4" fill="{WATER}"/>'
        f'<rect x="{x - 9}" y="{y - 24}" width="2" height="14" fill="{WOOD_DARK}"/>'
        f'<rect x="{x + 7}" y="{y - 24}" width="2" height="14" fill="{WOOD_DARK}"/>'
        f'<polygon points="{x - 12},{y - 22} {x + 12},{y - 22} {x},{y - 30}" fill="#B8513A"/>'
        f'<polygon points="{x},{y - 30} {x + 12},{y - 22} {x},{y - 22}" fill="#8F3D2B"/>'
    )


_BARN_COLORS = (("#C8473A", "#9E3529"), ("#4F86C6", "#356196"), ("#6FAA4E", "#4D823A"))
_TENT_COLORS = (("#E9B44C", "#C88F2E"), ("#E86A5C", "#B94B40"), ("#5AA7D6", "#3A82B0"))
_CAP_COLORS = (("#D9473C", "#A9352C"), ("#8E64B8", "#674590"), ("#E39A3B", "#B8742A"))


def _barn(x: float, y: float, variant: int = 0) -> str:
    wall, wall_dark = _BARN_COLORS[variant % len(_BARN_COLORS)]
    return (
        f'<rect x="{x - 11}" y="{y - 14}" width="22" height="14" fill="{wall}"/>'
        f'<rect x="{x + 2}" y="{y - 14}" width="9" height="14" fill="{wall_dark}"/>'
        f'<polygon points="{x - 13},{y - 14} {x + 13},{y - 14} {x},{y - 25}" fill="#7A3B2C"/>'
        f'<polygon points="{x},{y - 25} {x + 13},{y - 14} {x},{y - 14}" fill="#5E2C20"/>'
        f'<rect x="{x - 4}" y="{y - 9}" width="8" height="9" fill="{WALL}"/>'
        f'<path d="M{x - 4},{y - 9} L{x + 4},{y} M{x + 4},{y - 9} L{x - 4},{y}" stroke="{wall}" stroke-width="1.2"/>'
    )


def _tent(x: float, y: float, variant: int = 0) -> str:
    canvas, canvas_dark = _TENT_COLORS[variant % len(_TENT_COLORS)]
    return (
        f'<polygon points="{x - 13},{y} {x},{y - 22} {x + 13},{y}" fill="{canvas}"/>'
        f'<polygon points="{x},{y - 22} {x + 13},{y} {x + 2},{y}" fill="{canvas_dark}"/>'
        f'<polygon points="{x - 4},{y} {x},{y - 10} {x + 4},{y}" fill="{WOOD_DARK}"/>'
        f'<line x1="{x}" y1="{y - 22}" x2="{x}" y2="{y - 28}" stroke="{WOOD_DARK}" stroke-width="1.5"/>'
        f'<polygon points="{x},{y - 28} {x + 7},{y - 26} {x},{y - 24}" fill="#D9573F"/>'
    )


def _observatory(x: float, y: float) -> str:
    return (
        f'<rect x="{x - 9}" y="{y - 16}" width="18" height="16" fill="{STONE}"/>'
        f'<rect x="{x + 1}" y="{y - 16}" width="8" height="16" fill="{STONE_DARK}" opacity="0.6"/>'
        f'<path d="M{x - 11},{y - 16} A11,11 0 0 1 {x + 11},{y - 16} Z" fill="#4F86C6"/>'
        f'<path d="M{x},{y - 27} A11,11 0 0 1 {x + 11},{y - 16} L{x},{y - 16} Z" fill="#356196"/>'
        f'<rect x="{x - 1.5}" y="{y - 26}" width="3" height="9" fill="#BFD7EF"/>'
        f'<rect x="{x - 3}" y="{y - 7}" width="6" height="7" fill="{WOOD_DARK}"/>'
    )


def _haystack(x: float, y: float) -> str:
    return (
        f'<ellipse cx="{x - 5}" cy="{y - 7}" rx="10" ry="9" fill="{WHEAT}"/>'
        f'<ellipse cx="{x + 6}" cy="{y - 5}" rx="8" ry="7" fill="{WHEAT_DARK}"/>'
        f'<ellipse cx="{x - 8}" cy="{y - 11}" rx="4" ry="3" fill="#F6DC86"/>'
        f'<path d="M{x - 12},{y - 6} l4 -2 M{x - 4},{y - 12} l3 -3 M{x + 4},{y - 7} l3 -2" stroke="{WHEAT_DARK}" stroke-width="1"/>'
    )


def _mushroom_house(x: float, y: float, variant: int = 0) -> str:
    cap, cap_dark = _CAP_COLORS[variant % len(_CAP_COLORS)]
    return (
        f'<rect x="{x - 6}" y="{y - 12}" width="12" height="12" fill="{WALL}" rx="2"/>'
        f'<rect x="{x}" y="{y - 12}" width="6" height="12" fill="#D8C59D" rx="2"/>'
        f'<path d="M{x - 14},{y - 12} Q{x},{y - 34} {x + 14},{y - 12} Z" fill="{cap}"/>'
        f'<path d="M{x},{y - 23} Q{x + 9},{y - 22} {x + 14},{y - 12} L{x},{y - 12} Z" fill="{cap_dark}"/>'
        f'<circle cx="{x - 6}" cy="{y - 18}" r="2.2" fill="#FFFFFF"/><circle cx="{x + 3}" cy="{y - 22}" r="1.8" fill="#FFFFFF"/>'
        f'<rect x="{x - 2}" y="{y - 7}" width="4" height="7" fill="{WOOD_DARK}" rx="1.5"/>'
    )


def _signpost(x: float, y: float) -> str:
    return (
        f'<rect x="{x - 1.5}" y="{y - 26}" width="3" height="26" fill="{WOOD_DARK}"/>'
        f'<polygon points="{x - 2},{y - 25} {x + 13},{y - 25} {x + 17},{y - 21} {x + 13},{y - 17} {x - 2},{y - 17}" fill="{WOOD}"/>'
        f'<polygon points="{x + 2},{y - 15} {x - 13},{y - 15} {x - 17},{y - 11} {x - 13},{y - 7} {x + 2},{y - 7}" fill="#B88550"/>'
    )


def _campfire(x: float, y: float) -> str:
    return (
        f'<g stroke="{WOOD_DARK}" stroke-width="3" stroke-linecap="round">'
        f'<line x1="{x - 8}" y1="{y - 1}" x2="{x + 8}" y2="{y - 5}"/><line x1="{x - 8}" y1="{y - 5}" x2="{x + 8}" y2="{y - 1}"/></g>'
        f'<path d="M{x - 6},{y - 4} Q{x - 7},{y - 14} {x},{y - 22} Q{x + 7},{y - 14} {x + 6},{y - 4} Z" fill="#F08A2E"/>'
        f'<path d="M{x - 3},{y - 4} Q{x - 3},{y - 11} {x},{y - 15} Q{x + 3},{y - 11} {x + 3},{y - 4} Z" fill="#FFD34E"/>'
        f'<circle cx="{x + 9}" cy="{y - 6}" r="3" fill="{STONE}"/><circle cx="{x - 10}" cy="{y - 4}" r="2.5" fill="{STONE_DARK}"/>'
    )


def _lighthouse(x: float, y: float) -> str:
    return (
        f'<polygon points="{x - 7},{y} {x + 7},{y} {x + 4},{y - 24} {x - 4},{y - 24}" fill="#FFFFFF"/>'
        f'<polygon points="{x - 6.3},{y - 5} {x + 6.3},{y - 5} {x + 5.6},{y - 10} {x - 5.6},{y - 10}" fill="#D9473C"/>'
        f'<polygon points="{x - 4.9},{y - 15} {x + 4.9},{y - 15} {x + 4.4},{y - 19} {x - 4.4},{y - 19}" fill="#D9473C"/>'
        f'<polygon points="{x + 1},{y} {x + 7},{y} {x + 4},{y - 24} {x + 1},{y - 24}" fill="#000" opacity="0.12"/>'
        f'<rect x="{x - 4}" y="{y - 29}" width="8" height="5" fill="#FFE27A"/>'
        f'<polygon points="{x - 5},{y - 29} {x + 5},{y - 29} {x},{y - 34}" fill="#D9473C"/>'
    )


def _castle(x: float, y: float) -> str:
    """Boss 關：兩座塔的小城堡，紅旗。"""
    c = STONE
    d = STONE_DARK
    return (
        f'<rect x="{x - 12}" y="{y - 18}" width="24" height="18" fill="{c}"/>'
        f'<rect x="{x + 2}" y="{y - 18}" width="10" height="18" fill="{d}" opacity="0.6"/>'
        f'<rect x="{x - 16}" y="{y - 26}" width="8" height="26" fill="{c}"/>'
        f'<rect x="{x + 8}" y="{y - 26}" width="8" height="26" fill="{d}"/>'
        + "".join(
            f'<rect x="{bx}" y="{y - 29}" width="2.5" height="3" fill="{c if bx < x else d}"/>'
            for bx in (x - 16, x - 12.5, x - 10.5, x + 8, x + 11.5, x + 13.5)
        )
        + f'<path d="M{x - 4},{y} L{x - 4},{y - 8} A4,4 0 0 1 {x + 4},{y - 8} L{x + 4},{y} Z" fill="{WOOD_DARK}"/>'
        f'<line x1="{x - 12}" y1="{y - 26}" x2="{x - 12}" y2="{y - 37}" stroke="{WOOD_DARK}" stroke-width="1.5"/>'
        f'<polygon points="{x - 12},{y - 37} {x - 3},{y - 34} {x - 12},{y - 31}" fill="#D9473C"/>'
    )


#: 主題 → 地標。同一個主題的關卡長得一樣，看得出「這一段在練什麼」。
_LANDMARKS = {
    "windmill": _windmill, "well": _well, "barn": _barn, "tent": _tent,
    "observatory": _observatory, "haystack": _haystack, "mushroom": _mushroom_house,
    "signpost": _signpost, "campfire": _campfire, "lighthouse": _lighthouse,
}
TOPIC_LANDMARK = {
    "python_basic": "tent", "arrays": "barn", "strings": "windmill", "hashmaps": "well",
    "sorting": "haystack", "search": "lighthouse", "greedy": "campfire",
    "stacks_queues": "mushroom", "linked_lists": "signpost", "recursion": "observatory",
    "trees": "mushroom", "graphs": "signpost", "heaps": "haystack", "dp_1d": "observatory",
    "dp_2d": "lighthouse", "two_d_arrays": "barn", "bit_manipulation": "well",
    "math_number_theory": "windmill", "tries": "tent", "misc": "campfire",
}


def landmark_kind(topic_id: str) -> str:
    """主題對應的地標；沒列到的主題依名字固定挑一個（每次都一樣）。"""
    if topic_id in TOPIC_LANDMARK:
        return TOPIC_LANDMARK[topic_id]
    names = sorted(_LANDMARKS)
    return names[sum(map(ord, topic_id)) % len(names)]


#: 有配色變化的地標（其餘的造型本身就夠有辨識度）
_COLOR_VARIANTS = {"barn", "tent", "mushroom"}


def island_accessory(x: float, y: float, variant: int) -> str:
    """小島邊上的小擺設，依關卡輪流換：樹叢、花、石頭、南瓜（或什麼都不放）。讓同主題的關卡不會一模一樣。"""
    k = variant % 5
    if k == 1:
        return bush(x - 17, y + 2)
    if k == 2:
        return flowers(x + 15, y + 3, seed=variant)
    if k == 3:
        return (
            f'<g {_NOPTR}>' + shadow(x - 15, y + 5, 5, 2)
            + f'<ellipse cx="{x - 16:.1f}" cy="{y + 2:.1f}" rx="5" ry="3.5" fill="{STONE}"/>'
            f'<ellipse cx="{x - 17:.1f}" cy="{y + 1:.1f}" rx="2" ry="1.2" fill="#FFFFFF" opacity="0.5"/></g>'
        )
    if k == 4:
        return (
            f'<g {_NOPTR}>' + shadow(x + 16, y + 6, 5, 2)
            + f'<ellipse cx="{x + 15:.1f}" cy="{y + 3:.1f}" rx="5" ry="4" fill="#E8862E"/>'
            f'<path d="M{x + 15:.1f},{y - 1:.1f} v-3" stroke="{LEAF_DARK}" stroke-width="1.5"/></g>'
        )
    return ""


def landmark(kind: str, x: float, y: float, *, boss: bool = False, variant: int = 0) -> str:
    """畫在小島上的地標，``(x, y)`` 是地標的底部中心。Boss 關一律是城堡。

    ``variant``（通常是題目 id）決定配色與小擺設，讓同主題的關卡有小小的不同。
    """
    if boss:
        draw = _castle
    elif kind in _COLOR_VARIANTS:
        base = _LANDMARKS[kind]
        draw = lambda x, y: base(x, y, variant)  # noqa: E731
    else:
        draw = _LANDMARKS.get(kind, _tent)
    # 以底部中心放大 1.3 倍：地圖縮在畫面上時，原尺寸的小建築太小看不清楚
    return (
        f'<g class="landmark" transform="translate({x:.1f},{y:.1f}) scale({LANDMARK_SCALE}) '
        f'translate({-x:.1f},{-y:.1f})">{draw(x, y)}</g>'
    )


#: 地標放大倍率（以底部中心）
LANDMARK_SCALE = 1.3
#: 最高的地標（城堡含旗子）從底部往上的高度，放大前
LANDMARK_MAX_HEIGHT = 37
