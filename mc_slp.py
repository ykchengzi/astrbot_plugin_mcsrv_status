"""Minecraft Server List Ping (SLP) 直连查询客户端。

不依赖任何第三方 API，直接在本机对目标服务器发起 Minecraft 1.7+ 状态查询协议
（握手 + status request），获取服务器版本、在线人数、MOTD、favicon 等真实数据。

异常分类（便于插件给出精确提示）：
- SlpTimeoutError:            TCP 连接超时（端口未放行 / 服务器未启动 / 防火墙丢弃）
- SlpConnectionRefusedError:  端口拒绝连接（端口未监听）
- SlpDnsError:                DNS 域名解析失败（域名不存在 / DNS 服务器无响应）
- SlpNoResponseError:         TCP 连上但服务器未响应状态查询（可能 enable-status=false）
- SlpParseError:              服务器响应数据无法解析
- SlpError:                   其他网络错误
"""
import asyncio
import json
import random
import socket
import struct
import time

DEFAULT_TIMEOUT = 8.0
MAX_BUF = 65536


class SlpError(Exception):
    """SLP 查询失败基类。"""


class SlpTimeoutError(SlpError):
    """TCP 连接超时。"""


class SlpConnectionRefusedError(SlpError):
    """端口拒绝连接。"""


class SlpDnsError(SlpError):
    """DNS 域名解析失败。"""


class SlpNoResponseError(SlpError):
    """连接成功但未收到状态查询响应。"""


class SlpParseError(SlpError):
    """响应数据无法解析。"""


def _write_varint(n: int) -> bytes:
    if n < 0:
        raise ValueError("VarInt 不支持负数（协议版本请传 0）")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _parse_varint(b: bytes, pos: int):
    num, shift = 0, 0
    while pos < len(b):
        byte = b[pos]
        pos += 1
        num |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return num, pos
        shift += 7
    raise ValueError("VarInt 不完整")


async def slp_query(host: str, port: int, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """向目标服务器发起 SLP 状态查询，返回服务器状态 JSON dict（含 _ping_ms 字段）。

    返回的 dict 在服务器原始字段基础上额外注入：
    - _ping_ms: float，从发送握手包到收到完整响应的往返时间（毫秒）。
    """
    t_connect_start = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout
        )
    except asyncio.TimeoutError:
        raise SlpTimeoutError(f"TCP 连接超时（{timeout:.0f}s）") from None
    except ConnectionRefusedError:
        raise SlpConnectionRefusedError("端口拒绝连接") from None
    except socket.gaierror:
        raise SlpDnsError(f"DNS 解析失败：无法解析域名 {host}") from None
    except OSError as e:
        raise SlpError(f"网络错误: {e}") from e

    try:
        host_b = host.encode("utf-8")
        handshake = (
            _write_varint(0x00)
            + _write_varint(0)  # 协议版本：0 表示"无版本"，状态查询不受版本限制
            + _write_varint(len(host_b)) + host_b
            + struct.pack(">H", port)
            + _write_varint(1)
        )
        t_send = time.monotonic()
        writer.write(_write_varint(len(handshake)) + handshake + b"\x01\x00")
        await writer.drain()

        data = b""
        while len(data) < MAX_BUF:
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout)
            except asyncio.TimeoutError:
                break  # 无更多数据，尝试用已收数据解析
            if not chunk:
                break
            data += chunk
            try:
                plen, pos = _parse_varint(data, 0)
                if len(data) >= pos + plen:
                    break
            except ValueError:
                pass

        if not data:
            raise SlpNoResponseError(
                "服务器未响应状态查询（可能 server.properties 的 enable-status=false）"
            )
        plen, pos = _parse_varint(data, 0)
        payload = data[pos:pos + plen]
        pid, p2 = _parse_varint(payload, 0)
        if pid != 0x00:
            raise SlpParseError(f"非预期包 ID: {pid}")
        jlen, p3 = _parse_varint(payload, p2)
        raw = payload[p3:p3 + jlen].decode("utf-8")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise SlpParseError("响应不是 JSON 对象")
        result["_ping_ms"] = round((time.monotonic() - t_send) * 1000, 1)
        return result
    except (SlpError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
        if isinstance(e, (json.JSONDecodeError, UnicodeDecodeError, ValueError)):
            raise SlpParseError(f"响应解析失败: {e}") from e
        raise
    except asyncio.TimeoutError:
        raise SlpNoResponseError(
            "服务器未响应状态查询（可能 server.properties 的 enable-status=false）"
        ) from None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


# ========== DNS SRV 记录查询（纯标准库，不依赖 dnspython） ==========

def _encode_dns_name(name: str) -> bytes:
    out = bytearray()
    for label in name.split('.'):
        if label:
            out.append(len(label))
            out.extend(label.encode('ascii'))
    out.append(0)
    return bytes(out)


def _decode_dns_name(data: bytes, offset: int) -> tuple:
    labels = []
    original_end = offset
    jumped = False
    jumps = 0
    while True:
        if offset >= len(data):
            break
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if (length & 0xC0) == 0xC0:
            if offset + 1 >= len(data):
                break
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if not jumped:
                original_end = offset + 2
            offset = pointer
            jumped = True
            jumps += 1
            if jumps > 10:
                break
            continue
        offset += 1
        if offset + length > len(data):
            break
        labels.append(data[offset:offset + length].decode('ascii', errors='replace'))
        offset += length
    if not jumped:
        original_end = offset
    return '.'.join(labels), original_end


def query_srv(host: str, dns_server: str = '114.114.114.114', timeout: float = 3.0):
    """查询 _minecraft._tcp.<host> SRV 记录，返回 (target, port) 或 None。

    Minecraft 官方客户端行为：地址未显式带端口时，先查 SRV 记录获取真实
    目标地址和端口（常见于使用 CDN / 代理 / Velocity 的服务器）。
    """
    qname = f'_minecraft._tcp.{host}'
    txid = random.randint(0, 65535)
    header = struct.pack('>HHHHHH', txid, 0x0100, 1, 0, 0, 0)
    question = _encode_dns_name(qname) + struct.pack('>HH', 33, 1)
    packet = header + question

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (dns_server, 53))
        data, _ = sock.recvfrom(4096)
    except Exception:
        return None
    finally:
        sock.close()

    if len(data) < 12:
        return None
    r_txid, flags, qdcount, ancount, _, _ = struct.unpack('>HHHHHH', data[:12])
    if r_txid != txid or (flags & 0x000F) != 0:
        return None

    offset = 12
    for _ in range(qdcount):
        _, offset = _decode_dns_name(data, offset)
        offset += 4

    for _ in range(ancount):
        _, offset = _decode_dns_name(data, offset)
        if offset + 10 > len(data):
            break
        rtype, rclass, ttl, rdlength = struct.unpack('>HHIH', data[offset:offset + 10])
        offset += 10
        if rtype == 33 and rdlength >= 7:
            priority, weight, port = struct.unpack('>HHH', data[offset:offset + 6])
            target, _ = _decode_dns_name(data, offset + 6)
            return target.rstrip('.'), port
        offset += rdlength
    return None
