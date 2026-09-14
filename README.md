# astrbot_plugin_mcsrv_status

查询 Minecraft 服务器（Java 版与基岩版）的在线状态、版本、玩家数量、MOTD 等信息。

## 功能

- 默认**直连查询**（Minecraft 原生 SLP 协议），不依赖第三方 API，国内服务器也能准确查询
- Java 版直连失败时自动回退基岩版 RakNet（UDP 19132）查询
- 支持服务器 SRV 记录解析（CDN / Velocity 代理服务器自动识别）
- 返回状态 Banner 贴图（服务器图标 + 状态 + 地址 + MOTD + 版本 + 人数 + 延迟），未安装 Pillow 时自动回退「图标 + 文本」模式
- 可配置全局默认服务器，并可为不同 QQ 群设置各自的默认服务器
- 可配置 fallback_api 在直连失败时回退 mcsrvstat.us API（默认关闭）
- 输出高度可自定义：Banner / 图标 / 文字详情可分别开关，并可选择拆分发送

## 安装

1. 下载本插件（GitHub 仓库或 AstrBot Cloud 插件市场）
2. 在 AstrBot 插件管理面板中安装并启用
3. 在插件配置弹窗中填写默认服务器（可选，见下）

## 配置

在插件配置弹窗（WebUI）中设置：

| 配置项 | 类型 | 说明 |
| --- | --- | --- |
| `default_server` | string | 全局默认服务器地址，如 `mc.example.com` 或 `mc.example.com:25565` |
| `group_servers` | JSON | 按 QQ 群设置默认服务器，格式 `{"群号": "服务器地址"}`，例：`{"123456789": "mc.group1.com:25565"}` |
| `fallback_api` | bool | 直连失败时是否回退 mcsrvstat.us API，默认 `false`（不依赖第三方） |
| `show_banner` | bool | 是否显示状态 Banner 贴图，默认 `true` |
| `show_icon` | bool | 是否显示服务器图标，默认 `true` |
| `show_details` | bool | 是否显示文字详情（地址/版本/人数/延迟），默认 `true` |
| `split_message` | bool | 是否将 Banner 与「图标 + 文字」分两条消息发送，默认 `true`；`false` 时合并为一条消息 |

## 使用

群聊或私聊发送：

```
/查服                 # 按「当前群专属服务器 > 全局默认服务器」查询
/查服 mc.example.com  # 查询指定服务器（默认端口 25565）
/查服 mc.example.com:12345  # 查询指定端口
```

### 返回示例（在线）

```
[状态 Banner 贴图]
服务器：mc.ambercat.top     状态：在线
MOTD：≫ Amber Cat 橙猫服~ [1.9～26.2]
版本：Paper 26.2   玩家：1/23333   延迟：48ms
```

### 返回示例（直连失败时按原因提示）

```
[离线 Banner 贴图]
服务器：mc.example.com:25565
状态：离线
```

## 常见问题

| 提示 | 原因 | 解决 |
| --- | --- | --- |
| 连接超时 | 服务器未启动或防火墙未放行端口 | 确认服务器运行、端口对外开放 |
| 端口拒绝连接 | 端口无服务监听 | 检查端口是否正确、是否使用 SRV 代理端口 |
| 无法解析地址 | 域名不存在或 DNS 无响应 | 检查地址拼写或改用 IP |
| 服务器无响应 | 服务器禁用了状态查询或正在启动 | 检查 `enable-status` 配置 |
| 无法识别的数据 | 目标不是 Minecraft 服务器 | 确认地址与端口 |

## 依赖

- `Pillow`（生成状态 Banner，未安装时自动回退「图标 + 文本」模式）
- `fonttools`（Banner 特殊符号字形回退）
- `aiohttp`（仅启用 `fallback_api` 时需要，AstrBot 自带）

## 开发与测试

- 测试：`pytest`（测试文件以 `test_` 开头，不随插件分发）
- 本插件不包含任何需要额外配置的目录结构，`assets/icon_default.png` 为内置默认图标

## 开源许可

MIT License。详见 [LICENSE](LICENSE)。
