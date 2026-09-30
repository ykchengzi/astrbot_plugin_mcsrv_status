"""AstrBot 插件：Minecraft 服务器状态查询（直连，支持指令与自然语言）。"""
import asyncio
import tempfile
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

from .mc_bedrock import DEFAULT_BEDROCK_PORT, bedrock_query
from .mc_slp import (
    SlpConnectionRefusedError, SlpDnsError, SlpError, SlpNoResponseError,
    SlpParseError, SlpTimeoutError, query_srv, slp_query,
)
from .mcsrv_logic import (
    ICON_BASE, USER_AGENT, decode_favicon_to_file, format_slp_status,
    format_status, is_valid_address, parse_address_from_message,
    parse_host_port, resolve_server, slp_summary,
)

_DEFAULT_ICON = Path(__file__).parent / "assets" / "icon_default.png"

try:
    from .mc_banner import generate_status_banner

    HAS_BANNER = True
except ImportError:
    HAS_BANNER = False


def _banner_result(address, *, online, icon_path, data=None, error_text=""):
    if not HAS_BANNER:
        return None
    try:
        s = slp_summary(data) if data else {}
        p = generate_status_banner(
            address, online=online, icon_path=icon_path,
            version=s.get("version", ""), motd=s.get("motd", ""),
            players_online=s.get("players_online", 0),
            players_max=s.get("players_max", "?"),
            ping_ms=s.get("ping_ms"), error_text=error_text,
            out_dir=tempfile.gettempdir(),
        )
        return [Comp.Image.fromFileSystem(p)]
    except ImportError:
        return None
    except Exception as e:
        logger.warning(f"Banner 生成失败，回退旧模式: {e}")
        return None


@register(
    "mcsrv_status", "YKChengZi",
    "查询 Minecraft 服务器的在线状态、版本、玩家数量等信息（直连，支持自然语言）",
    "2.6.1", "https://github.com/ykchengzi/astrbot_plugin_mcsrv_status",
)
class McSrvStatusPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}

    def _resolve_address(self, event):
        address = parse_address_from_message(event.message_str)
        if not address:
            address = resolve_server(self.config, event.get_group_id())
        return address

    async def _fetch_api_json(self, address):
        import aiohttp

        url = "https://api.mcsrvstat.us/3/" + address
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    def _icon_file(self, favicon):
        if favicon:
            try:
                return decode_favicon_to_file(favicon, tempfile.gettempdir())
            except Exception as e:
                logger.warning(f"favicon 解码失败，使用默认图标: {e}")
        return str(_DEFAULT_ICON)

    @staticmethod
    def _slp_error_text(e, host, port):
        if isinstance(e, SlpDnsError):
            return f"无法解析地址「{host}」：域名不存在或 DNS 无响应，请检查拼写。"
        if isinstance(e, SlpTimeoutError):
            return f"连接 {host}:{port} 超时：服务器未启动、端口未放行或被防火墙拦截。"
        if isinstance(e, SlpConnectionRefusedError):
            return f"连接 {host}:{port} 被拒绝：该端口无服务监听，请核对端口。"
        if isinstance(e, SlpNoResponseError):
            return f"{host}:{port} 未返回状态：可能 enable-status=false 或正在启动。"
        if isinstance(e, SlpParseError):
            return f"{host}:{port} 返回数据无法识别：可能非 Java 服务器。"
        return f"查询 {host}:{port} 失败：{e}"

    async def _perform_query(self, address):
        host, port, explicit = parse_host_port(address)
        ch, cp = host, port
        if not explicit:
            try:
                srv = await asyncio.to_thread(query_srv, host)
                if srv:
                    ch, cp = srv
            except Exception as e:
                logger.warning(f"SRV 查询 {host} 失败，使用默认端口: {e}")

        bp = cp if explicit else DEFAULT_BEDROCK_PORT

        try:
            data = await slp_query(ch, cp)
            return {"online": True, "is_bedrock": False, "source": "java",
                    "address": address, "host": ch, "port": cp,
                    "data": data, "error": None}
        except SlpError as je:
            try:
                data = await bedrock_query(ch, bp)
                return {"online": True, "is_bedrock": True, "source": "bedrock",
                        "address": address, "host": ch, "port": bp,
                        "data": data, "error": None}
            except SlpError as be:
                pass

        err = (f"Java 版直连失败：{self._slp_error_text(je, host, cp)}\n"
               f"基岩版直连也失败：{self._slp_error_text(be, host, bp)}")

        if self.config.get("fallback_api", False):
            try:
                data = await self._fetch_api_json(address)
                if data.get("online"):
                    return {"online": True, "is_bedrock": False, "source": "api",
                            "address": address, "host": host, "port": cp,
                            "data": data, "error": None}
                err += "\n第三方 API（mcsrvstat.us）同样报告该服务器离线。"
            except Exception as e2:
                logger.error(f"API 兜底查询 {address} 失败: {e2}")
                err += f"\nAPI 兜底也失败：{e2}"

        logger.error(f"Java 版查询 {address} 失败: {je}；基岩版也失败: {be}")
        return {"online": False, "is_bedrock": False, "source": None,
                "address": address, "host": host, "port": cp,
                "data": None, "error": err}

    @filter.llm_tool(name="query_minecraft_server")
    async def query_minecraft_server_tool(self, event, server_address: str = ""):
        """查询 Minecraft（我的世界）服务器实时状态：是否在线、版本、在线人数、MOTD、延迟。用户问服务器开没开、多少人、人多不多、卡不卡、延迟高不高、状态如何时调用。
        Args:
            server_address(string): 服务器地址，可带端口；用户未给出时留空，自动用默认服务器
        """
        address = (server_address or "").strip()
        if not address:
            address = resolve_server(self.config, event.get_group_id())
        if not address:
            yield event.plain_result("查询失败：未提供地址，且未配置默认服务器。")
            return
        if not is_valid_address(address):
            yield event.plain_result(f"查询失败：地址「{address}」格式不合法。")
            return

        result = await self._perform_query(address)
        yield event.plain_result(self._format_tool_result(result))

    @staticmethod
    def _format_tool_result(result):
        address = result["address"]
        if not result["online"]:
            return f"Minecraft 服务器「{address}」当前【离线/无法连接】。\n{result['error']}"
        data = result["data"]
        if result["source"] == "api":
            body = format_status(address, data)
        else:
            body = format_slp_status(result["host"], result["port"], data)
        edition = "基岩版 Bedrock" if result["is_bedrock"] else "Java 版"
        return f"以下为 Minecraft 原生状态查询实时结果（{edition}，直连，数据准确）：\n" + body

    @filter.command("查服")
    async def query_server(self, event):
        """用法：/查服 [服务器地址]"""
        address = self._resolve_address(event)
        if not address:
            yield event.plain_result(
                "未指定服务器。用法：/查服 <地址>；或在配置中设置 "
                'default_server 和 group_servers（{"群号": "地址"}）。'
            )
            return
        if not is_valid_address(address):
            yield event.plain_result(f"地址「{address}」格式不合法。")
            return

        result = await self._perform_query(address)
        show_banner = self.config.get("show_banner", True)
        show_icon = self.config.get("show_icon", True)
        show_details = self.config.get("show_details", True)
        split = self.config.get("split_message", True)

        if not result["online"]:
            err_text = result["error"]
            banner = (_banner_result(
                address, online=False, icon_path=str(_DEFAULT_ICON),
                error_text=err_text) if show_banner else None)
            if not banner and not show_details:
                yield event.plain_result("查询失败（Banner / 详情均已关闭）。")
                return
            if banner and show_details and split:
                yield event.chain_result(banner)
                yield event.chain_result([Comp.Plain(err_text)])
                return
            second = [Comp.Plain(err_text)] if show_details else []
            yield event.chain_result((banner or []) + second)
            return

        data = result["data"]
        if result["source"] == "api":
            icon_path = str(_DEFAULT_ICON)
            details_text = format_status(address, data)
        else:
            icon_path = (str(_DEFAULT_ICON) if result["is_bedrock"]
                         else self._icon_file(data.get("favicon")))
            details_text = format_slp_status(
                result["host"], result["port"], data,
                display_address=result["address"])

        banner = (_banner_result(
            address, online=True, icon_path=icon_path, data=data)
            if show_banner else None)
        second = []
        if show_icon:
            if result["source"] == "api":
                second.append(Comp.Image.fromURL(ICON_BASE + address))
            else:
                second.append(Comp.Image.fromFileSystem(icon_path))
        if show_details:
            second.append(Comp.Plain(("\n" if second else "") + details_text))
        if not banner and not second:
            yield event.plain_result("未配置任何输出项（Banner / 图标 / 详情均关闭）。")
            return
        if banner and second and split:
            yield event.chain_result(banner)
            yield event.chain_result(second)
            return
        yield event.chain_result((banner or []) + second)

    async def terminate(self):
        pass
