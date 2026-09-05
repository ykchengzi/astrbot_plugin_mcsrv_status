# astrbot_plugin_mcsrv_status

AstrBot 插件：查询 Minecraft 服务器在线状态。默认使用**直连查询**——AstrBot 所在机器直接对目标服务器发起 Minecraft 原生状态查询协议（SLP），**不依赖任何第三方 API**，国内/自建服也能准确查到真实状态。

## 功能

- 指令 `/查服 [服务器地址]` 查询服务器在线状态、版本、MOTD、玩家数
- **直连查询**（SLP 协议），不经过境外第三方 API，实时且准确
- 返回消息第一行为服务器图标（优先服务器 favicon，无则使用内置默认 MC 图标）
- 精确错误提示，区分四种情况：端口未放行 / 端口未监听 / 服务器禁用了状态查询 / 数据解析失败
- 可配置全局默认服务器，并可为不同 QQ 群设置各自的默认服务器
- 可选回退第三方 API（`mcsrvstat.us`，默认关闭）

## 安装

1. 下载 `astrbot_plugin_mcsrv_status.zip`
2. AstrBot WebUI → 插件 → 安装插件 → **本地安装**，选择该 zip
3. 在插件配置弹窗中填写默认服务器（可选，见下）

## 配置

在插件配置弹窗（WebUI）中设置：

| 配置项 | 类型 | 说明 |
| --- | --- | --- |
| `default_server` | string | 全局默认服务器地址，如 `mc.example.com` 或 `mc.example.com:25565` |
| `group_servers` | JSON | 按 QQ 群设置默认服务器，格式 `{"群号": "服务器地址"}`，例：`{"123456789": "mc.group1.com:25565"}` |
| `fallback_api` | bool | 直连失败时是否回退 mcsrvstat.us API，默认 `false`（不依赖第三方） |

## 使用

群聊或私聊发送：

```
/查服                 # 按「当前群专属服务器 > 全局默认服务器」查询
/查服 mc.example.com  # 查询指定服务器（默认端口 25565）
/查服 mc.example.com:12345  # 查询指定端口
```

### 返回示例（在线）

```
[服务器图标]
服务器：mc.ambercat.top
状态：在线
版本：Paper 26.2
MOTD：≫ Amber Cat 橙猫服~ [1.9～26.2]
    ≫ 服务器已更新至26.2！
玩家：1/23333
```

### 返回示例（直连失败时按原因提示）

```
服务器：mc.example.com:25565
状态：连接超时
```

## 常见问题

| 提示 | 原因 | 解决 |
| --- | --- | --- |
| 连接超时 | 端口未放行 / 服务器未启动 / 防火墙丢弃 | 云安全组、UFW/firewalld 放行对应 TCP 端口 |
| 端口拒绝连接 | 服务器未监听该端口 | 确认端口号与 SRV 记录、`server-port` 一致 |
| 未响应状态查询 | 服务器禁用了状态查询 | `server.properties` 中设置 `enable-status=true` |
| 数据无法解析 | 服务器返回异常数据 | 多为代理/防火墙干扰，检查服务端插件 |

## 依赖

- `aiohttp`（仅启用 `fallback_api` 时需要，AstrBot 自带）

## 开发与测试

```
python test_slp.py        # SLP 直连客户端（本地 mock 服务器，不联网）
python test_mcsrv.py      # 核心逻辑 + 真实 API 兼容
python test_integration.py# handler 端到端集成
```

## 开源许可

MIT License
