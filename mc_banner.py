"""Minecraft 服务器状态 Banner 生成（Pillow 可选依赖）。

把服务器状态渲染成一张横条 PNG 贴图（风格类似 MC 服务器宣传站的 votebanner）：
左侧服务器图标，右侧状态、地址、MOTD、版本、玩家数、延迟。

Pillow 为可选依赖：未安装时函数内抛 ImportError，由 main.py 捕获后
回退到旧版「图标 + 文本」模式，不影响插件其它功能。
"""
import re
import uuid
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

# 中文字体候选（Windows -> Linux），按优先级
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyhbd.ttc",   # 微软雅黑 Bold
    "C:/Windows/Fonts/msyh.ttc",     # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",   # 黑体
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
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


def _truncate(draw, text: str, font, max_width: int) -> str:
    """超宽文本截断并加省略号。"""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"


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
    from PIL import Image, ImageDraw, ImageFont

    font_path = _find_font()
    font_title = ImageFont.truetype(font_path, 22)
    font_body = ImageFont.truetype(font_path, 17)
    font_info = ImageFont.truetype(font_path, 15)

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
    draw.text((_TEXT_X, 14), dot + status_text, font=font_title, fill=status_color)
    dot_w = draw.textlength(dot + status_text + "  ", font=font_title)
    addr_text = _truncate(draw, address, font_title, max_text_width - dot_w)
    draw.text((_TEXT_X + dot_w, 14), addr_text, font=font_title, fill=_COLOR_WHITE)

    y = 48
    if online:
        motd = _clean_motd(motd)
        if motd:
            draw.text(
                (_TEXT_X, y),
                _truncate(draw, motd, font_body, max_text_width),
                font=font_body,
                fill=_COLOR_GRAY,
            )
            y += 30
        parts = []
        if version:
            parts.append(f"版本：{version}")
        parts.append(f"玩家：{players_online}/{players_max}")
        if ping_ms is not None:
            parts.append(f"延迟：{ping_ms}ms")
        info = "    ".join(parts)
        draw.text(
            (_TEXT_X, y),
            _truncate(draw, info, font_info, max_text_width),
            font=font_info,
            fill=_COLOR_YELLOW,
        )
    else:
        err = _clean_motd(error_text) or "无法连接服务器"
        draw.text(
            (_TEXT_X, y),
            _truncate(draw, err, font_body, max_text_width),
            font=font_body,
            fill=_COLOR_GRAY,
        )

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"mcsrv_banner_{uuid.uuid4().hex}.png"
    img.save(path, "PNG")
    return str(path)
