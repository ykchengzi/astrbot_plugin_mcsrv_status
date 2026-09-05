"""mcsrvstat.us API 查询逻辑（纯逻辑模块，不依赖 AstrBot，便于独立测试）。

本模块封装了服务器地址解析、配置解析、状态文本格式化等纯逻辑。
AstrBot 插件入口 main.py 直接调用这里的方法。
"""
import base64
import json
import re
import uuid
from pathlib import Path

# mcsrvstat.us API 端点
API_BASE = "https://api.mcsrvstat.us/3/"
ICON_BASE = "https://api.mcsrvstat.us/icon/"
# mcsrvstat.us 要求非空 User-Agent，否则返回 403
USER_AGENT = "AstrBot-MCSrvStatus/1.0.0"

# 合法服务器地址字符：域名 / IP / 端口 / 短横线 / 下划线
_ADDR_RE = re.compile(r"^[a-zA-Z0-9._\-:]+$")


def parse_group_servers(raw: str) -> dict:
    """解析群配置 group_servers（JSON 字符串 -> dict）。

    配置示例: {"123456789": "mc.group1.com", "987654321": "mc.group2.com:25565"}
    解析失败或不是 dict 时返回空 dict。
    """
    if not raw:
        return {}
    raw = str(raw).strip()
    if not raw:
        return {}
    try:
        m = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return m if isinstance(m, dict) else {}


def resolve_server(config: dict, group_id: str | None) -> str:
    """解析应查询的服务器地址。

    优先级：当前 QQ 群配置的专属服务器 > 全局默认服务器。
    config: 插件的 AstrBotConfig（dict 子类）。
    group_id: 当前事件所在群号，私聊时为 None/空。
    """
    if group_id:
        mapping = parse_group_servers(config.get("group_servers", ""))
        key = str(group_id)
        if key in mapping:
            return str(mapping[key]).strip()
    return str(config.get("default_server", "") or "").strip()


def parse_address_from_message(message_str: str) -> str:
    """从指令消息的纯文本中解析出服务器地址参数。

    例如 "/查服 mc.example.com" -> "mc.example.com"；"/查服" -> ""。
    """
    msg = (message_str or "").strip()
    parts = msg.split(maxsplit=1)
    if len(parts) > 1:
        return parts[1].strip()
    return ""


def is_valid_address(address: str) -> bool:
    """校验服务器地址格式是否合法。"""
    return bool(address) and bool(_ADDR_RE.match(address))


def parse_host_port(address: str, default_port: int = 25565) -> tuple[str, int, bool]:
    """把「host」或「host:port」拆分为 (主机, 端口, 是否显式指定了端口)。

    - mc.example.com        -> ("mc.example.com", 25565, False)
    - mc.example.com:30411  -> ("mc.example.com", 30411, True)
    - [2001:db8::1]:25565   -> ("2001:db8::1", 25565, True)（IPv6 带端口）
    - [2001:db8::1]         -> ("2001:db8::1", 25565, False)
    """
    addr = (address or "").strip()
    if not addr:
        return "", default_port, False
    if addr.startswith("["):
        # IPv6: [addr] 或 [addr]:port
        end = addr.find("]")
        if end != -1:
            host = addr[1:end]
            rest = addr[end + 1:]
            if rest.startswith(":") and rest[1:].isdigit():
                return host, int(rest[1:]), True
            return host, default_port, False
    if addr.count(":") == 1:
        host, _, port_s = addr.rpartition(":")
        if port_s.isdigit():
            return host, int(port_s), True
    return addr, default_port, False


def _motd_text(description) -> str:
    """从 SLP 的 description 字段提取纯文本 MOTD（递归处理任意深度嵌套的 extra）。

    支持 str / {"text": ..., "extra": [...]} / [{"text": ...}, ...] 及任意嵌套组合。
    Velocity / BungeeCord 等代理常用多层 extra 实现渐变色 MOTD。
    """
    if description is None:
        return ""
    if isinstance(description, str):
        return description
    if isinstance(description, list):
        return "".join(_motd_text(x) for x in description)
    if isinstance(description, dict):
        parts = []
        if description.get("text"):
            parts.append(description["text"])
        extra = description.get("extra")
        if isinstance(extra, list):
            for item in extra:
                parts.append(_motd_text(item))
        return "".join(parts)
    return str(description)


def format_slp_status(host: str, port: int, data: dict) -> str:
    """把直连 SLP 返回的服务器状态格式化为人类可读文本。"""
    lines = [f"服务器：{host}:{port}", "状态：在线"]

    version = (data.get("version") or {}).get("name")
    if version:
        lines.append(f"版本：{version}")

    motd = _motd_text(data.get("description"))
    if motd:
        lines.append("MOTD：" + motd.replace("\n", "\n    "))

    players = data.get("players") or {}
    lines.append(f"玩家：{players.get('online', 0)}/{players.get('max', '?')}")

    ping = data.get("_ping_ms")
    if ping is not None:
        lines.append(f"延迟：{ping}ms")

    return "\n".join(lines)


def decode_favicon_to_file(favicon: str, out_dir: str | Path) -> str:
    """把 SLP 返回的 favicon（data:image/png;base64,...）解码保存为 PNG 文件，返回路径。

    解码失败或格式不合法时抛出 ValueError。
    """
    if not favicon or "base64," not in favicon:
        raise ValueError("favicon 格式不合法")
    b64 = favicon.split("base64,", 1)[1].strip()
    raw = base64.b64decode(b64, validate=True)
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("favicon 不是有效的 PNG")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"mcsrv_favicon_{uuid.uuid4().hex}.png"
    path.write_bytes(raw)
    return str(path)


def format_status(address: str, data: dict) -> str:
    """把 API 返回的数据格式化为人类可读的状态文本。

    在线: 地址 / 状态 / 版本 / MOTD / 玩家 / 软件 / 地图 / IP:端口
    离线: 地址 / 状态
    """
    if not data.get("online"):
        return f"服务器：{address}\n状态：离线"

    lines = [f"服务器：{address}", "状态：在线"]

    version = data.get("version")
    if version:
        lines.append(f"版本：{version}")

    motd = (data.get("motd") or {}).get("clean") or []
    if motd:
        lines.append("MOTD：" + "\n".join(motd))

    players = data.get("players") or {}
    lines.append(f"玩家：{players.get('online', 0)}/{players.get('max', '?')}")

    software = data.get("software")
    if software:
        lines.append(f"软件：{software}")

    map_name = (data.get("map") or {}).get("clean")
    if map_name:
        lines.append(f"地图：{map_name}")

    ip, port = data.get("ip"), data.get("port")
    if ip and port:
        lines.append(f"IP:端口：{ip}:{port}")

    return "\n".join(lines)
