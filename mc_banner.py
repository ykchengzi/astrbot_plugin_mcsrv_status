"""Minecraft 服务器状态 Banner 生成（Pillow 可选依赖）。

把服务器状态渲染成一张现代深色风格的横条 PNG：
左侧状态色条 + 圆角图标卡片，右侧状态徽章、地址、MOTD、版本/人数/延迟信息行。

文字渲染支持【逐字符字形回退】：MOTD / 地址中可能包含中文字体缺少的特殊符号
（≫ ★ ▶ 等），会按字体候选顺序自动切换到能显示该字符的字体，避免方块乱码。
依赖 fontTools 做字符集检测；fontTools 缺失时退化为单字体渲染。

Pillow 为可选依赖：未安装时函数内抛 ImportError，由 main.py 捕获后
回退到旧版「图标 + 文本」模式，不影响插件其它功能。
"""
import re
import uuid
from functools import lru_cache
from pathlib import Path

# ---------- 配色（现代深色主题） ----------
_COLOR_BG_TOP = (32, 36, 43)        # 顶部深蓝灰
_COLOR_BG_BOTTOM = (20, 23, 28)     # 底部近黑
_COLOR_HIGHLIGHT = (255, 255, 255)  # 顶部高光
_COLOR_CARD = (43, 48, 56)          # 图标卡片底
_COLOR_CARD_EDGE = (62, 68, 78)     # 卡片边
_COLOR_DIVIDER = (52, 57, 66)       # 分隔线

_COLOR_ONLINE = (74, 222, 128)      # 现代绿（在线）
_COLOR_OFFLINE = (248, 113, 113)    # 现代红（离线）
_COLOR_WHITE = (236, 240, 245)
_COLOR_ADDR = (255, 255, 255)
_COLOR_MOTD = (184, 190, 201)
_COLOR_MUTED = (138, 144, 156)
_COLOR_YELLOW = (250, 204, 21)      # 人数/信息强调
_COLOR_BADGE_TEXT = (12, 16, 20)    # 徽章上的深色文字

# 字体候选（Windows -> Linux），按优先级：中文字体在前，符号补充字体在后
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyhbd.ttc",        # 微软雅黑 Bold
    "C:/Windows/Fonts/msyh.ttc",          # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",        # 黑体
    "C:/Windows/Fonts/seguisym.ttf",      # Segoe UI Symbol（符号补充）
    "C:/Windows/Fonts/seguiemj.ttf",      # Segoe UI Emoji
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf",
]

# 清理 MOTD 里的颜色/格式代码（§x 与 &x）
_COLOR_CODE_RE = re.compile(r"[§&][0-9a-fk-orA-FK-OR]")

# ---------- Banner 尺寸与布局 ----------
_WIDTH = 820
_HEIGHT = 118
_ICON_SIZE = 74
_ICON_X = 26          # 图标左上角 x
_ICON_Y = (_HEIGHT - _ICON_SIZE) // 2
_TEXT_X = 122
_RIGHT_PAD = 20
_ACCENT_W = 6         # 左侧状态色条宽度


def _find_font() -> str:
    """查找第一个存在的字体文件。"""
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return p
    raise FileNotFoundError("未找到可用中文字体")


@lru_cache(maxsize=None)
def _char_sets() -> dict:
    """{字体路径: frozenset(覆盖的字符码点)}。fontTools 缺失或解析失败时为空。"""
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return {}
    sets = {}
    for p in _FONT_CANDIDATES:
        if not Path(p).exists():
            continue
        try:
            f = TTFont(p, fontNumber=0)
            sets[p] = frozenset(f.getBestCmap().keys())
        except Exception:
            continue
    return sets


@lru_cache(maxsize=None)
def _get_font(path: str, size: int):
    """加载指定字体的指定字号（带缓存）。"""
    from PIL import ImageFont

    return ImageFont.truetype(path, size)


def _font_for_char(char: str) -> str:
    """返回能显示该字符的第一个字体路径；无字体覆盖时返回第一个候选。"""
    cp = ord(char)
    for p, cs in _char_sets().items():
        if cp in cs:
            return p
    return _find_font()


def _segments(text: str) -> list:
    """把文本按「同一字体」切成连续段，供逐段渲染。"""
    segs = []
    cur_text = ""
    cur_font = None
    for ch in text:
        f = _font_for_char(ch)
        if f == cur_font:
            cur_text += ch
        else:
            if cur_text:
                segs.append((cur_font, cur_text))
            cur_text = ch
            cur_font = f
    if cur_text:
        segs.append((cur_font, cur_text))
    return segs


def _text_width(draw, text: str, size: int) -> float:
    """逐字符用对应字体计算文本总宽度。"""
    w = 0.0
    for f, seg in _segments(text):
        w += draw.textlength(seg, font=_get_font(f, size))
    return w


def _fit_text(draw, text: str, size: int, max_width: int) -> str:
    """超宽文本截断并加省略号（按字符实际渲染宽度计算）。"""
    if _text_width(draw, text, size) <= max_width:
        return text
    while text and _text_width(draw, text + "…", size) > max_width:
        text = text[:-1]
    return text + "…"


def _wrap_text(draw, text: str, size: int, max_width: int, max_lines: int) -> list:
    """按渲染宽度把文本折成多行；超出 max_lines 的最后一行加省略号。"""
    text = (text or "").strip()
    if not text:
        return []
    # 优先按显式换行切，再按宽度折
    raw_lines = text.split("\n")
    lines = []
    for raw in raw_lines:
        cur = ""
        for ch in raw:
            if cur and _text_width(draw, cur + ch, size) > max_width:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
    if len(lines) <= max_lines:
        return lines
    last = lines[max_lines - 1]
    while last and _text_width(draw, last + "…", size) > max_width:
        last = last[:-1]
    return lines[: max_lines - 1] + [last + "…"]


def _draw_text(draw, xy, text: str, size: int, fill) -> float:
    """分段渲染文本（自动字形回退），返回实际总宽度。"""
    x, y = xy
    for f, seg in _segments(text):
        font = _get_font(f, size)
        draw.text((x, y), seg, font=font, fill=fill)
        x += draw.textlength(seg, font=font)
    return x - xy[0]


def _clean_motd(text: str) -> str:
    """去掉 MOTD 中的颜色/格式代码并清理空白（保留显式换行）。"""
    text = _COLOR_CODE_RE.sub("", text or "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _rounded_mask(size, radius):
    """生成圆角矩形 alpha mask。"""
    from PIL import Image, ImageDraw

    mask = Image.new("L", size, 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return mask


def _load_icon(path: str):
    """加载图标并缩放到 _ICON_SIZE（圆角裁剪由外层卡片 mask 完成）。失败返回 None。"""
    from PIL import Image

    try:
        img = Image.open(path).convert("RGBA")
        if img.size != (_ICON_SIZE, _ICON_SIZE):
            img = img.resize((_ICON_SIZE, _ICON_SIZE), Image.LANCZOS)
        # 裁成圆角
        mask = _rounded_mask((_ICON_SIZE, _ICON_SIZE), 12)
        img.putalpha(mask)
        return img
    except Exception:
        return None


def _vertical_gradient(size, top, bottom):
    """生成垂直渐变底色。"""
    from PIL import Image

    w, h = size
    img = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        img.putpixel(
            (0, y),
            (
                int(top[0] + (bottom[0] - top[0]) * t),
                int(top[1] + (bottom[1] - top[1]) * t),
                int(top[2] + (bottom[2] - top[2]) * t),
            ),
        )
    return img.resize((w, h))


def _ping_color(ping_ms):
    """按延迟返回颜色：绿 <100，黄 <220，红更高。"""
    if ping_ms is None:
        return _COLOR_MUTED
    if ping_ms < 100:
        return _COLOR_ONLINE
    if ping_ms < 220:
        return _COLOR_YELLOW
    return _COLOR_OFFLINE


def _draw_badge(draw, x, y, text: str, color, size: int = 17):
    """绘制圆角胶囊状态徽章，返回徽章右边缘 x。"""
    pad_x = 10
    h = size + 10
    w = _text_width(draw, text, size) + pad_x * 2
    draw.rounded_rectangle(
        [x, y, x + w, y + h], radius=h // 2, fill=color
    )
    _draw_text(draw, (x + pad_x, y + (h - size) // 2 - 1), text, size, _COLOR_BADGE_TEXT)
    return x + w


def generate_status_banner(
    address: str,
    *,
    online: bool,
    icon_path: str | None = None,
    version: str = "",
    motd: str = "",
    players_online: int | str = 0,
    players_max: int | str = "?",
    ping_ms: int | None = None,
    error_text: str = "",
    out_dir: str | Path,
) -> str:
    """生成状态 Banner PNG，返回生成的文件路径。

    在线：状态徽章 + 地址 / MOTD / 版本、玩家、延迟。
    离线：状态徽章 + 地址 / 错误原因（自动折行）。
    Pillow 未安装时抛 ImportError。
    """
    from PIL import Image, ImageDraw

    accent = _COLOR_ONLINE if online else _COLOR_OFFLINE

    # 背景渐变 + 顶部柔和高光
    img = _vertical_gradient((_WIDTH, _HEIGHT), _COLOR_BG_TOP, _COLOR_BG_BOTTOM)
    glow = Image.new("RGBA", (_WIDTH, _HEIGHT // 2), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for y in range(_HEIGHT // 2):
        a = int(14 * (1 - y / (_HEIGHT // 2)))
        gd.line([(0, y), (_WIDTH, y)], fill=(255, 255, 255, a))
    img = img.convert("RGBA")
    img.alpha_composite(glow)

    draw = ImageDraw.Draw(img)

    # 左侧状态色条（圆角贴边效果：直接矩形）
    draw.rectangle([0, 0, _ACCENT_W - 1, _HEIGHT - 1], fill=accent)

    # 图标圆角卡片
    card_box = [_ICON_X - 6, _ICON_Y - 6, _ICON_X + _ICON_SIZE + 5, _ICON_Y + _ICON_SIZE + 5]
    draw.rounded_rectangle(card_box, radius=14, fill=_COLOR_CARD, outline=_COLOR_CARD_EDGE, width=1)
    icon = _load_icon(icon_path) if icon_path else None
    if icon:
        img.alpha_composite(icon, (_ICON_X, _ICON_Y))
    else:
        ph = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), _COLOR_CARD)
        img.alpha_composite(ph, (_ICON_X, _ICON_Y))

    max_text_width = _WIDTH - _TEXT_X - _RIGHT_PAD

    # 行1：状态徽章 + 地址
    badge_text = ("● 在线") if online else ("● 离线")
    badge_right = _draw_badge(draw, _TEXT_X, 15, badge_text, accent, size=16)
    addr_gap = 12
    addr_size = 21
    addr = _fit_text(draw, address, addr_size, int(max_text_width - (badge_right - _TEXT_X) - addr_gap))
    _draw_text(draw, (badge_right + addr_gap, 16), addr, addr_size, _COLOR_ADDR)

    if online:
        # 行2：MOTD
        motd = _clean_motd(motd)
        y2 = 52
        if motd:
            motd_lines = _wrap_text(draw, motd, 15, max_text_width, 1)
            _draw_text(draw, (_TEXT_X, y2), motd_lines[0], 15, _COLOR_MOTD)
        # 分隔线
        draw.line([_TEXT_X, 80, _TEXT_X + max_text_width, 80], fill=_COLOR_DIVIDER, width=1)
        # 行3：版本 · 玩家 · 延迟（分段着色）
        y3 = 88
        x = _TEXT_X
        size3 = 14
        if version:
            x_end = _draw_text(draw, (x, y3), f"版本 {version}", size3, _COLOR_MUTED)
            x += x_end + 14
            draw.ellipse([x - 8, y3 + 6, x - 4, y3 + 10], fill=_COLOR_MUTED)
            x += 6
        players_txt = f"玩家 {players_online}/{players_max}"
        x_end = _draw_text(draw, (x, y3), players_txt, size3, _COLOR_YELLOW)
        x += x_end + 14
        draw.ellipse([x - 8, y3 + 6, x - 4, y3 + 10], fill=_COLOR_MUTED)
        x += 6
        if ping_ms is not None:
            _draw_text(draw, (x, y3), f"延迟 {ping_ms}ms", size3, _ping_color(ping_ms))
    else:
        # 离线：错误原因折行（小字，最多 3 行）
        err = _clean_motd(error_text) or "无法连接服务器"
        lines = _wrap_text(draw, err, 14, max_text_width, 3)
        y = 50
        for ln in lines:
            _draw_text(draw, (_TEXT_X, y), ln, 14, _COLOR_MOTD)
            y += 21

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"mcsrv_banner_{uuid.uuid4().hex}.png"
    img.convert("RGB").save(path, "PNG")
    return str(path)
