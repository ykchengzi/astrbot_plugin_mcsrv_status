"""Minecraft 服务器状态 Banner 生成（Pillow 可选依赖）。

把服务器状态渲染成一张横条 PNG 贴图（风格类似 MC 服务器宣传站的 votebanner）：
左侧服务器图标，右侧状态、地址、MOTD、版本、玩家数、延迟。

文字渲染支持【逐字符字形回退】：MOTD / 地址中可能包含微软雅黑等中文字体
缺少的特殊符号（≫ ★ ▶ 等），会按字体候选顺序自动切换到能显示该字符的字体
（Segoe UI Symbol / DejaVu Sans / Noto 等），避免出现方块乱码。
依赖 fontTools 做字符集检测；fontTools 缺失时退化为单字体渲染。

Pillow 为可选依赖：未安装时函数内抛 ImportError，由 main.py 捕获后
回退到旧版「图标 + 文本」模式，不影响插件其它功能。
"""
import re
import uuid
from functools import lru_cache
from pathlib import Path

# MC 风格配色
_COLOR_BG_TOP = (47, 47, 47)      # 深灰
_COLOR_BG_BOTTOM = (25, 25, 25)   # 更深灰
_COLOR_BORDER_OUT = (13, 13, 13)  # 外框
_COLOR_BORDER_IN = (90, 90, 90)   # 内框
_COLOR_ICON_FRAME = (60, 60, 60)  # 图标框
_COLOR_GREEN = (85, 255, 85)      # MC 绿色（在线）
_COLOR_RED = (255, 85, 85)        # MC 红色（离线）
_COLOR_WHITE = (255, 255, 255)
_COLOR_GRAY = (170, 170, 170)
_COLOR_YELLOW = (255, 255, 85)    # MC 黄色

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

# Banner 尺寸
_WIDTH = 760
_HEIGHT = 100
_ICON_SIZE = 64
_ICON_AREA = 88  # 左侧图标显示区边长
_TEXT_X = 116


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


def _segments(text: str, size: int) -> list:
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
    for f, seg in _segments(text, size):
        w += draw.textlength(seg, font=_get_font(f, size))
    return w


def _fit_text(draw, text: str, size: int, max_width: int) -> str:
    """超宽文本截断并加省略号（按字符实际渲染宽度计算）。"""
    if _text_width(draw, text, size) <= max_width:
        return text
    while text and _text_width(draw, text + "…", size) > max_width:
        text = text[:-1]
    return text + "…"


def _draw_text(draw, xy, text: str, size: int, fill) -> float:
    """分段渲染文本（自动字形回退），返回实际总宽度。"""
    x, y = xy
    for f, seg in _segments(text, size):
        font = _get_font(f, size)
        draw.text((x, y), seg, font=font, fill=fill)
        x += draw.textlength(seg, font=font)
    return x - xy[0]


def _clean_motd(text: str) -> str:
    """去掉 MOTD 中的颜色/格式代码并清理空白。"""
    text = _COLOR_CODE_RE.sub("", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _load_icon(path: str):
    """加载图标并缩放到 64x64。失败时返回 None。"""
    from PIL import Image

    try:
        img = Image.open(path).convert("RGBA")
        if img.size != (_ICON_SIZE, _ICON_SIZE):
            img = img.resize((_ICON_SIZE, _ICON_SIZE), Image.LANCZOS)
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

    在线：状态 + 地址 / MOTD / 版本、玩家、延迟。
    离线：状态 + 地址 / 错误简述。
    Pillow 未安装时抛 ImportError。
    """
    from PIL import Image, ImageDraw

    img = _vertical_gradient((_WIDTH, _HEIGHT), _COLOR_BG_TOP, _COLOR_BG_BOTTOM)
    draw = ImageDraw.Draw(img)

    # 外框 + 内框（MC 面板风格）
    draw.rectangle([0, 0, _WIDTH - 1, _HEIGHT - 1], outline=_COLOR_BORDER_OUT, width=3)
    draw.rectangle([3, 3, _WIDTH - 4, _HEIGHT - 4], outline=_COLOR_BORDER_IN, width=1)

    # 左侧图标区
    icon_off = (_ICON_AREA - _ICON_SIZE) // 2
    icon_x = icon_off + 4
    icon_y = (_HEIGHT - _ICON_SIZE) // 2
    draw.rectangle(
        [icon_x - 3, icon_y - 3, icon_x + _ICON_SIZE + 2, icon_y + _ICON_SIZE + 2],
        outline=_COLOR_ICON_FRAME,
        width=2,
    )
    icon = _load_icon(icon_path) if icon_path else None
    if icon:
        img.paste(icon, (icon_x, icon_y), icon)
    else:
        draw.rectangle(
            [icon_x, icon_y, icon_x + _ICON_SIZE - 1, icon_y + _ICON_SIZE - 1],
            fill=(80, 80, 80),
            outline=(120, 120, 120),
            width=2,
        )

    max_text_width = _WIDTH - _TEXT_X - 16

    # 行1：状态 + 地址
    status_color = _COLOR_GREEN if online else _COLOR_RED
    status_text = "在线" if online else "离线"
    dot = "● "
    _draw_text(draw, (_TEXT_X, 14), dot + status_text, 22, status_color)
    dot_w = _text_width(draw, dot + status_text + "  ", 22)
    addr_text = _fit_text(draw, address, 22, int(max_text_width - dot_w))
    _draw_text(draw, (_TEXT_X + dot_w, 14), addr_text, 22, _COLOR_WHITE)

    y = 48
    if online:
        motd = _clean_motd(motd)
        if motd:
            _draw_text(
                draw,
                (_TEXT_X, y),
                _fit_text(draw, motd, 17, max_text_width),
                17,
                _COLOR_GRAY,
            )
            y += 30
        parts = []
        if version:
            parts.append(f"版本：{version}")
        parts.append(f"玩家：{players_online}/{players_max}")
        if ping_ms is not None:
            parts.append(f"延迟：{ping_ms}ms")
        info = "    ".join(parts)
        _draw_text(
            draw,
            (_TEXT_X, y),
            _fit_text(draw, info, 15, max_text_width),
            15,
            _COLOR_YELLOW,
        )
    else:
        err = _clean_motd(error_text) or "无法连接服务器"
        _draw_text(
            draw,
            (_TEXT_X, y),
            _fit_text(draw, err, 17, max_text_width),
            17,
            _COLOR_GRAY,
        )

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"mcsrv_banner_{uuid.uuid4().hex}.png"
    img.save(path, "PNG")
    return str(path)
