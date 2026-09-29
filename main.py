"""AstrBot 插件：Minecraft 服务器状态查询（直连模式）。

两种使用方式：
1. 指令：/查服 [服务器地址]
2. 自然语言：把查询注册为 LLM 函数工具（function tool），用户直接说
   「服开着吗」「现在多少人」「卡不卡」，大模型自动调用本插件查询并回答。

不带地址时，按「当前群专属服务器 > 全局默认服务器」的优先级取配置的服务器。
默认使用【直连查询】：AstrBot 所在机器直接对目标服务器发起 Minecraft 原生
SLP（Java）/ RakNet（基岩）状态查询，不依赖任何第三方 API。
可配置 fallback_api 在直连失败时回退 mcsrvstat.us API（默认关闭）。

配置（插件配置弹窗）：
- default_server: 全局默认服务器地址（host 或 host:port）。
- group_servers: 按 QQ 群设置默认服务器，JSON 格式 {"群号": "服务器地址"}。
- fallback_api: 直连失败时是否回退第三方 API（默认 false）。
- show_banner / show_icon / show_details / split_message: 输出内容与发送方式自定义。
"""
import asyncio
import tempfile
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

from .mc_bedrock import DEFAULT_BEDROCK_PORT, bedrock_query
from .mc_slp import (
    SlpConnectionRefusedError,
    SlpDnsError,
    SlpError,
    SlpNoResponseError,
    SlpParseError,
    SlpTimeoutError,
    query_srv,
    slp_query,
)
from .mcsrv_logic import (
    ICON_BASE,
    USER_AGENT,
    decode_favicon_to_file,
    format_slp_status,
    format_status,
    is_valid_address,
    parse_address_from_message,
    parse_host_port,
    resolve_server,
    slp_summary,
)

_DEFAULT_ICON = Path(__file__).parent / "assets" / "icon_default.png"

try:
    from .mc_banner import generate_status_banner

    HAS_BANNER = True
except ImportError:
    HAS_BANNER = False


def _banner_result(
    address: str,
    *,
    online: bool,
    icon_path: str,
    data: dict | None = None,
    error_text: str = "",
) -> list | None:
    """生成状态 Banner 消息链；失败（无 Pillow / 无字体 / 其他异常）返回 None。"""
    if not HAS_BANNER:
        return None
    try:
        summary = slp_summary(data) if data else {}
        banner_path = generate_status_banner(
            address,
            online=online,
            icon_path=icon_path,
            version=summary.get("version", ""),
            motd=summary.get("motd", ""),
            players_online=summary.get("players_online", 0),
            players_max=summary.get("players_max", "?"),
            ping_ms=summary.get("ping_ms"),
            error_text=error_text,
            out_dir=tempfile.gettempdir(),
        )
        return [Comp.Image.fromFileSystem(banner_path)]
    except ImportError:
        return None
    except Exception as e:
        logger.warning(f"Banner 生成失败，回退旧模式: {e}")
        return None


@register(
    "mcsrv_status",
    "YKChengZi",
    "查询 Minecraft 服务器的在线状态、版本、玩家数量等信息（直连查询，支持自然语言）",
    "2.6.0",
    "https://github.com/ykchengzi/astrbot_plugin_mcsrv_status",
)
class McSrvStatusPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}

    # ---------------- 地址解析 ----------------
    def _resolve_address(self, event: AstrMessageEvent) -> str:
        """按「指令参数 > 群专属 > 全局默认」解析要查询的服务器地址。"""
        address = parse_address_from_message(event.message_str)
        if not address:
            address = resolve_server(self.config, event.get_group_id())
        return address

    async def _fetch_api_json(self, address: str) -> dict:
        """mcsrvstat.us API 兜底查询（可选）。"""
        import aiohttp

        url = "https://api.mcsrvstat.us/3/" + address
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    def _icon_file(self, favicon: str | None) -> str:
        """返回图标本地路径：优先服务器 favicon，否则内置默认图标。"""
        if favicon:
            try:
                return decode_favicon_to_file(favicon, tempfile.gettempdir())
            except Exception as e:
                logger.warning(f"favicon 解码失败，使用默认图标: {e}")
        return str(_DEFAULT_ICON)

    @staticmethod
    def _slp_error_text(e: SlpError, host: str, port: int) -> str:
        if isinstance(e, SlpDnsError):
            return (
                f"无法解析服务器地址「{host}」：域名不存在或 DNS 服务器无响应。"
                f"请检查地址拼写是否正确，或尝试使用 IP 地址。"
            )
        if isinstance(e, SlpTimeoutError):
            return (
                f"连接 {host}:{port} 超时：服务器可能未启动、端口未放行，"
                f"或防火墙丢弃了数据包。请确认服务器正在运行，且 {port} 端口已对外开放。"
            )
        if isinstance(e, SlpConnectionRefusedError):
            return (
                f"连接 {host}:{port} 被拒绝：该端口没有服务在监听。"
                f"请检查端口是否正确、服务器是否已启动，或是否使用了 SRV 代理端口。"
            )
        if isinstance(e, SlpNoResponseError):
            return (
                f"{host}:{port} 连接成功但未返回状态信息："
                f"可能 server.properties 中 enable-status=false，或服务器正在启动中。"
            )
        if isinstance(e, SlpParseError):
            return (
                f"{host}:{port} 返回了无法识别的数据："
                f"可能不是 Minecraft Java 版服务器，或服务器版本过旧。"
            )
        return f"查询 {host}:{port} 失败：{e}"

    # ---------------- 统一查询核心（指令与 LLM 工具共用，保证结果一致、准确） ----------------
    async def _perform_query(self, address: str) -> dict:
        """执行完整查询：SRV 解析 → Java SLP → 基岩 RakNet →（可选）第三方 API。

        返回 dict：
          online(bool)、is_bedrock(bool)、source("java"/"bedrock"/"api")、
          address(原始输入)、host、port、data(原始响应)、error(离线错误文本)。
        """
        host, port, had_explicit_port = parse_host_port(address)
        connect_host, connect_port = host, port
        # 用户未显式指定端口时，按官方客户端行为查询 DNS SRV 记录
        if not had_explicit_port:
            try:
                srv = await asyncio.to_thread(query_srv, host)
                if srv:
                    connect_host, connect_port = srv
            except Exception as e:
                logger.warning(f"SRV 查询 {host} 失败，使用默认端口: {e}")

        bedrock_port = connect_port if had_explicit_port else DEFAULT_BEDROCK_PORT

        # 第一步：Java 版 SLP
        try:
            data = await slp_query(connect_host, connect_port)
            return {
                "online": True, "is_bedrock": False, "source": "java",
                "address": address, "host": connect_host, "port": connect_port,
                "data": data, "error": None,
            }
        except SlpError as java_err:
            # 第二步：回退基岩版 RakNet
            try:
                data = await bedrock_query(connect_host, bedrock_port)
                return {
                    "online": True, "is_bedrock": True, "source": "bedrock",
                    "address": address, "host": connect_host, "port": bedrock_port,
                    "data": data, "error": None,
                }
            except SlpError as bedrock_err:
                pass

        err_text = (
            f"Java 版直连失败：{self._slp_error_text(java_err, host, connect_port)}\n"
            f"基岩版直连也失败：{self._slp_error_text(bedrock_err, host, bedrock_port)}"
        )

        # 第三步：可选第三方 API 兜底
        if self.config.get("fallback_api", False):
            try:
                data = await self._fetch_api_json(address)
                if data.get("online"):
                    return {
                        "online": True, "is_bedrock": False, "source": "api",
                        "address": address, "host": host, "port": connect_port,
                        "data": data, "error": None,
                    }
                err_text += "\n第三方 API（mcsrvstat.us）同样报告该服务器离线。"
            except Exception as e2:
                logger.error(f"API 兜底查询 {address} 失败: {e2}")
                err_text += f"\nAPI 兜底也失败：{e2}"

        logger.error(
            f"Java 版查询 {address} 失败: {java_err}；基岩版查询也失败: {bedrock_err}"
        )
        return {
            "online": False, "is_bedrock": False, "source": None,
            "address": address, "host": host, "port": connect_port,
            "data": None, "error": err_text,
        }

    # ---------------- LLM 函数工具（自然语言查服） ----------------
    @filter.llm_tool(name="query_minecraft_server")
    async def query_minecraft_server_tool(
        self, event: AstrMessageEvent, server_address: str = ""
    ):
        """查询 Minecraft（我的世界）服务器的实时状态：是否在线、版本、在线人数、MOTD 和网络延迟。当用户询问某个 MC 服务器开没开、是否在线、现在有多少人、人多不多、卡不卡、延迟高不高、服务器状态如何时调用此工具。
        Args:
            server_address(string): 服务器地址，支持域名或 IP、可带端口（例如 mc.example.com 或 mc.example.com:25565）；用户没有明确给出地址时留空，将自动使用该群或全局配置的默认服务器
        """
        address = (server_address or "").strip()
        if not address:
            address = resolve_server(self.config, event.get_group_id())
        if not address:
            yield event.plain_result(
                "查询失败：未提供服务器地址，且插件未配置默认服务器。"
            )
            return
        if not is_valid_address(address):
            yield event.plain_result(f"查询失败：服务器地址「{address}」格式不合法。")
            return

        result = await self._perform_query(address)
        yield event.plain_result(self._format_tool_result(result))

    @staticmethod
    def _format_tool_result(result: dict) -> str:
        """把查询结果整理成给大模型阅读的、准确的结构化纯文本（不含图片）。"""
        address = result["address"]
        if not result["online"]:
            return (
                f"Minecraft 服务器「{address}」当前【离线/无法连接】。\n"
                f"{result['error']}"
            )
        data = result["data"]
        if result["source"] == "api":
            body = format_status(address, data)
        else:
            body = format_slp_status(result["host"], result["port"], data)
        edition = "基岩版 Bedrock" if result["is_bedrock"] else "Java 版"
        return (
            f"以下为 Minecraft 原生状态查询的实时结果（{edition}，直连获取，数据准确）：\n"
            + body
        )

    # ---------------- 指令 /查服 ----------------
    @filter.command("查服")
    async def query_server(self, event: AstrMessageEvent):
        """查询 Minecraft 服务器在线状态。用法：/查服 [服务器地址]"""
        address = self._resolve_address(event)
        if not address:
            yield event.plain_result(
                "未指定服务器。用法：/查服 <服务器地址>；"
                "或在插件配置中设置 default_server（全局默认）和 "
                'group_servers（按群设置，格式 {"群号": "服务器地址"}）。'
            )
            return
        if not is_valid_address(address):
            yield event.plain_result(f"服务器地址「{address}」格式不合法。")
            return

        result = await self._perform_query(address)
        show_banner = self.config.get("show_banner", True)
        show_icon = self.config.get("show_icon", True)
        show_details = self.config.get("show_details", True)
        split = self.config.get("split_message", True)

        if not result["online"]:
            err_text = result["error"]
            banner = (
                _banner_result(
                    address, online=False, icon_path=str(_DEFAULT_ICON),
                    error_text=err_text,
                )
                if show_banner
                else None
            )
            if not banner and not show_details:
                yield event.plain_result("查询失败（Banner / 详情均已关闭，无可输出内容）。")
                return
            if banner and show_details and split:
                yield event.chain_result(banner)
                yield event.chain_result([Comp.Plain(err_text)])
                return
            second = [Comp.Plain(err_text)] if show_details else []
            yield event.chain_result((banner or []) + second)
            return

        # 在线
        data = result["data"]
        is_bedrock = result["is_bedrock"]
        if result["source"] == "api":
            icon_path = str(_DEFAULT_ICON)
            details_text = format_status(address, data)
        else:
            icon_path = (
                str(_DEFAULT_ICON) if is_bedrock
                else self._icon_file(data.get("favicon"))
            )
            details_text = format_slp_status(result["host"], result["port"], data)

        banner = (
            _banner_result(address, online=True, icon_path=icon_path, data=data)
            if show_banner
            else None
        )
        second = []
        if show_icon:
            if result["source"] == "api":
                second.append(Comp.Image.fromURL(ICON_BASE + address))
            else:
                second.append(Comp.Image.fromFileSystem(icon_path))
        if show_details:
            second.append(
                Comp.Plain(("\n" if second else "") + details_text)
            )
        if not banner and not second:
            yield event.plain_result("未配置任何输出项（Banner / 图标 / 详情均已关闭）。")
            return
        if banner and second and split:
            yield event.chain_result(banner)
            yield event.chain_result(second)
            return
        yield event.chain_result((banner or []) + second)

    async def terminate(self):
        """插件被卸载/停用时的清理钩子。"""
        pass
