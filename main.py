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

from .mc_slp import (
    SlpConnectionRefusedError,
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
    "2.1.1",
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
        if isinstance(e, SlpTimeoutError):
            return f"服务器 {host}:{port} 连接超时：端口未放行或服务器未启动。"
        if isinstance(e, SlpConnectionRefusedError):
            return f"服务器 {host}:{port} 拒绝连接：端口未监听。"
        if isinstance(e, SlpNoResponseError):
            return f"服务器 {host}:{port} 未响应状态查询：请检查 server.properties 的 enable-status=true。"
        if isinstance(e, SlpParseError):
            return f"服务器 {host}:{port} 返回了无法解析的数据：{e}"
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

        try:
            data = await slp_query(connect_host, connect_port)
        except SlpError as e:
            # 直连失败：可选回退 mcsrvstat.us API
            if self.config.get("fallback_api", False):
                try:
                    data = await self._fetch_api_json(address)
                except Exception as e2:
                    logger.error(f"API 兜底查询 {address} 失败: {e2}")
                    yield event.plain_result(
                        f"直连失败：{self._slp_error_text(e, host, connect_port)}；"
                        f"API 兜底也失败：{e2}"
                    )
                    return
                chain = [
                    Comp.Image.fromURL(ICON_BASE + address),
                    Comp.Plain("\n" + format_status(address, data)),
                ]
                yield event.chain_result(chain)
                return
            logger.error(f"直连查询 {address} 失败: {e}")
            yield event.plain_result(self._slp_error_text(e, host, connect_port))
            return

        # 直连成功：第一行输出服务器图标，随后是状态文本
        chain = [
            Comp.Image.fromFileSystem(self._icon_file(data.get("favicon"))),
            Comp.Plain("\n" + format_slp_status(host, connect_port, data)),
        ]
        yield event.chain_result(chain)

    async def terminate(self):
        """插件被卸载/停用时的清理钩子。"""
        pass
