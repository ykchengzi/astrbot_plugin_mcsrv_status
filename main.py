"""AstrBot 插件：Minecraft 服务器状态查询（直连模式）。

指令：/查服 [服务器地址]
- 不带参数时，按「当前群专属服务器 > 全局默认服务器」的优先级取配置的服务器。
- 默认使用【直连查询】：AstrBot 所在机器直接对目标服务器发起 Minecraft 原生
  SLP 状态查询，不依赖任何第三方 API，返回服务器图标、版本、在线人数、MOTD。
- 可配置 fallback_api 在直连失败时回退 mcsrvstat.us API（默认关闭）。

配置（插件配置弹窗）：
- default_server: 全局默认服务器地址（host 或 host:port）。
- group_servers: 按 QQ 群设置默认服务器，JSON 格式 {"群号": "服务器地址"}。
- fallback_api: 直连失败时是否回退第三方 API（默认 false）。
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
)

_DEFAULT_ICON = Path(__file__).parent / "assets" / "icon_default.png"


@register(
    "mcsrv_status",
    "YKChengZi",
    "查询 Minecraft 服务器的在线状态、版本、玩家数量等信息（默认直连查询，不依赖第三方 API）",
    "2.3.0",
    "https://github.com/ykchengzi/astrbot_plugin_mcsrv_status",
)
class McSrvStatusPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}

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
                f"无法解析服务器地址「{host}」：域名不存在或 DNS 服务器无响应。\n"
                f"请检查地址拼写是否正确，或尝试使用 IP 地址。"
            )
        if isinstance(e, SlpTimeoutError):
            return (
                f"连接 {host}:{port} 超时：服务器可能未启动、端口未放行，"
                f"或防火墙丢弃了数据包。\n请确认服务器正在运行，且 {port} 端口已对外开放。"
            )
        if isinstance(e, SlpConnectionRefusedError):
            return (
                f"连接 {host}:{port} 被拒绝：该端口没有服务在监听。\n"
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

        host, port, had_explicit_port = parse_host_port(address)
        # 用户未显式指定端口时，按 Minecraft 官方客户端行为查询 DNS SRV 记录
        # （常见于 CDN / Velocity 代理服务器，SRV 会指向真实地址和端口）
        connect_host, connect_port = host, port
        if not had_explicit_port:
            try:
                srv = await asyncio.to_thread(query_srv, host)
                if srv:
                    connect_host, connect_port = srv
            except Exception as e:
                logger.warning(f"SRV 查询 {host} 失败，使用默认端口: {e}")

        # 基岩版回退端口：用户显式指定了端口则沿用，否则用基岩版默认 19132
        bedrock_port = connect_port if had_explicit_port else DEFAULT_BEDROCK_PORT

        data = None
        is_bedrock = False
        java_err = None

        # 第一步：Java 版 SLP 直连查询
        try:
            data = await slp_query(connect_host, connect_port)
        except SlpError as e:
            java_err = e
            # 第二步：Java 版失败，自动回退基岩版 RakNet 查询
            try:
                data = await bedrock_query(connect_host, bedrock_port)
                is_bedrock = True
            except SlpError as bedrock_err:
                # 第三步：Java 版和基岩版都失败，可选回退第三方 API
                if self.config.get("fallback_api", False):
                    try:
                        data = await self._fetch_api_json(address)
                    except Exception as e2:
                        logger.error(f"API 兜底查询 {address} 失败: {e2}")
                        yield event.plain_result(
                            f"Java 版直连失败：{self._slp_error_text(java_err, host, connect_port)}\n"
                            f"基岩版直连也失败：{self._slp_error_text(bedrock_err, host, bedrock_port)}\n"
                            f"API 兜底也失败：{e2}"
                        )
                        return
                    chain = [
                        Comp.Image.fromURL(ICON_BASE + address),
                        Comp.Plain("\n" + format_status(address, data)),
                    ]
                    yield event.chain_result(chain)
                    return
                logger.error(
                    f"Java 版查询 {address} 失败: {java_err}；"
                    f"基岩版查询也失败: {bedrock_err}"
                )
                yield event.plain_result(
                    f"Java 版直连失败：{self._slp_error_text(java_err, host, connect_port)}\n"
                    f"基岩版直连也失败：{self._slp_error_text(bedrock_err, host, bedrock_port)}"
                )
                return

        # 查询成功（Java 版或基岩版）：第一行输出服务器图标，随后是状态文本
        display_port = bedrock_port if is_bedrock else connect_port
        # 基岩版服务器不返回 favicon，使用内置默认图标
        icon_path = (
            str(_DEFAULT_ICON) if is_bedrock else self._icon_file(data.get("favicon"))
        )
        chain = [
            Comp.Image.fromFileSystem(icon_path),
            Comp.Plain("\n" + format_slp_status(host, display_port, data)),
        ]
        yield event.chain_result(chain)

    async def terminate(self):
        """插件被卸载/停用时的清理钩子。"""
        pass
