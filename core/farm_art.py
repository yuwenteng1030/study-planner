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
