# THREAT_MODEL — 威脅模型(一頁)

> 資產:一台會動的實體載具(最貴)、操作機(Jetson)、API 金鑰(`~/.config/jenai/.env`)。
> 假設環境:實驗室/場域 LAN;**不設計給公網曝露**。

## 信任邊界與防護

| 介面 | 曝露面 | 防護 | 刻意的決策 |
|---|---|---|---|
| TUI | 本機終端 | 作業系統登入;敏感操作 HITL 批准卡 | 信任本機操作者 |
| WebUI(`JenAI web`) | 預設 127.0.0.1;`--host` 可進 LAN | **token 認證**(每次啟動自動生成,Bearer/cookie/`?token=`;`--token` 可固定)、批准動作 server 端一次性且 120 秒到期的 confirm_id、JSON body 64 KiB 上限 | **`/api/stop` 免認證且不等待 body** —— 停車永遠安全,並立即撤銷所有舊確認;攻擊者最多能把車停下(可用性損失,非安全損失) |
| MCP(`JenAI mcp`) | stdio,由宿主(Claude Code 等)管控 | **預設唯讀**;`navigate_to` 需明式 `--allow-actions`;`stop` 唯讀模式也提供 | 信任 MCP 宿主行程;不做行程間認證(stdio 無網路面) |
| daemon | 本機行程 | `halt` 免批准;`goto` 需 `auto_approve` + nav2 明式授權 | 規則檔是本機檔案 = 信任本機檔案系統 |
| `/shell`、`!` | 等同操作者 shell | HITL 批准卡 | **批准後即操作者級 RCE,by design** —— 它就是給操作者用的 shell;防護在批准卡,不在沙箱 |
| bridge(rclpy sidecar) | ROS graph(DDS) | 速度硬夾限(`[vehicle]`)、watchdog 斷線自主停車、twin 隔離 ROS_DOMAIN_ID | DDS 本身無認證 —— 這是 ROS2 部署現實;場域 LAN 隔離是部署前提(可選 SROS2,不在 v1 範圍) |
| 金鑰 | `~/.config/jenai/.env` | 檔案權限;不進版控(.gitignore) | — |

## 主要威脅 → 對應

- **LAN 上的陌生裝置操作載具** → WebUI token;MCP/daemon/TUI 無網路面
- **瀏覽器端竄改/重放批准內容** → action 存 server 端;confirm_id 一次性、120 秒到期,STOP 全部撤銷
- **LLM 產生危險動作** → 有界動作集 + 批准卡 + 速度夾限 + Twin Gate(啟用時)+ estop 鏈
- **模型/網路失效** → 反射層不依賴 LLM 與網路;watchdog 自主停車;誠實降級
- **prompt injection(相機畫面/文件內容誘導 VLM)** → 感知只觀察不動作;建議動作一律過批准/規則 gating,無捷徑

## 明確不在範圍

公網部署、多租戶、DDS 加密(SROS2)、供應鏈攻擊防禦 —— 部署前提是隔離 LAN + 信任本機。
