"""Minecraft 基岩版（Bedrock Edition）RakNet 状态查询客户端。

基岩版不使用 Java 版的 SLP/TCP 协议，而是通过 UDP 发送 RakNet Unconnected Ping
包（0x01），服务器返回 Unconnected Pong 包（0x1C），其中包含以分号分隔的
服务器状态字符串。

默认端口：19132（UDP）。

返回的 dict 与 Java 版 SLP 返回格式兼容（version / description / players / _ping_ms），
额外注入 _bedrock=True 标记，便于上层区分。
"""
import asyncio
import random
import socket
import struct
import time

try:
    from .mc_slp import (
        SlpError,
        SlpNoResponseError,
        SlpParseError,
        SlpTimeoutError,
    )
except ImportError:
    from mc_slp import (
        SlpError,
        SlpNoResponseError,
        SlpParseError,
        SlpTimeoutError,
    )

# RakNet 协议固定 MAGIC 值
_MAGIC = b"\x00\xff\xff\x00\xfe\xfe\xfe\xfe\xfd\xfd\xfd\xfd\x12\x34\x56\x78"

DEFAULT_BEDROCK_PORT = 19132
DEFAULT_TIMEOUT = 5.0


def _build_unconnected_ping() -> bytes:
    """构造 RakNet Unconnected Ping 包（ID 0x01）。"""
    timestamp = int(time.time() * 1000) & 0xFFFFFFFFFFFFFFFF
    client_guid = random.randint(0, 0xFFFFFFFFFFFFFFFF)
    return (
        b"\x01"
        + struct.pack(">Q", timestamp)
        + _MAGIC
        + struct.pack(">Q", client_guid)
    )


def _parse_unconnected_pong(data: bytes, ping_ms: float) -> dict:
    """解析 Unconnected Pong 包（ID 0x1C），返回与 SLP 兼容的状态 dict。

    状态字符串字段（以分号分隔）：
      [0]  Edition（MCPE / MCEE）
      [1]  MOTD 第一行（服务器名称）
      [2]  协议版本号
      [3]  Minecraft 版本名
      [4]  当前玩家数
      [5]  最大玩家数
      [6]  服务器唯一 ID
      [7]  MOTD 第二行（子标题）
      [8]  游戏模式（Survival / Creative 等）
      [9]  游戏模式数字
      [10] IPv4 端口
      [11] IPv6 端口
    """
    if len(data) < 35:
        raise SlpParseError("基岩版响应过短")
    if data[0] != 0x1C:
        raise SlpParseError(f"基岩版非预期包 ID: {data[0]:#x}")

    # data[1:9]   = timestamp (回显)
    # data[9:17]  = server GUID
    # data[17:33] = MAGIC
    # data[33:35] = 状态字符串长度 (uint16 BE)
    str_len = struct.unpack(">H", data[33:35])[0]
    if 35 + str_len > len(data):
        raise SlpParseError("基岩版状态字符串长度超出响应数据")
    status_str = data[35:35 + str_len].decode("utf-8", errors="replace")

    fields = status_str.split(";")

    def _int_field(idx: int, default: int = 0) -> int:
        if idx < len(fields) and fields[idx].strip().lstrip("-").isdigit():
            return int(fields[idx])
        return default

    motd_lines = []
    if len(fields) > 1 and fields[1]:
        motd_lines.append(fields[1])
    if len(fields) > 7 and fields[7]:
        motd_lines.append(fields[7])
    motd = "\n".join(motd_lines)

    version_name = fields[3] if len(fields) > 3 and fields[3] else "?"
    edition = fields[0] if len(fields) > 0 and fields[0] else "MCPE"

    return {
        "version": {"name": f"基岩版 {version_name}（{edition}）"},
        "description": motd,
        "players": {
            "online": _int_field(4),
            "max": _int_field(5),
        },
        "_ping_ms": round(ping_ms, 1),
        "_bedrock": True,
        "_protocol_version": fields[2] if len(fields) > 2 else "",
        "_gamemode": fields[8] if len(fields) > 8 else "",
        "_server_id": fields[6] if len(fields) > 6 else "",
    }


def _bedrock_query_sync(host: str, port: int, timeout: float) -> dict:
    """同步实现：发送 UDP Unconnected Ping，接收并解析 Pong。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        packet = _build_unconnected_ping()
        t_send = time.monotonic()
        sock.sendto(packet, (host, port))
        data, _ = sock.recvfrom(4096)
        ping_ms = (time.monotonic() - t_send) * 1000
        if not data:
            raise SlpNoResponseError("基岩版服务器未响应")
        return _parse_unconnected_pong(data, ping_ms)
    except socket.timeout:
        raise SlpTimeoutError(f"基岩版查询超时（{timeout:.0f}s）") from None
    except (ConnectionRefusedError, OSError) as e:
        # UDP 通常不会 ConnectionRefused，但 ICMP 端口不可达可能触发
        raise SlpError(f"基岩版网络错误: {e}") from e
    finally:
        sock.close()


async def bedrock_query(
    host: str, port: int = DEFAULT_BEDROCK_PORT, timeout: float = DEFAULT_TIMEOUT
) -> dict:
    """异步查询基岩版服务器状态，返回与 Java 版 SLP 兼容的 dict。"""
    return await asyncio.to_thread(_bedrock_query_sync, host, port, timeout)
