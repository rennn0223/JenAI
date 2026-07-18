# SUPPORT_MATRIX — 已驗證組合與支援等級

> `Validated` 表示有目前版本的正式證據；`Supported` 表示設計與測試涵蓋但仍依部署
> 環境；`Experimental` 表示可試用但不能作採購或安全承諾；`Planned` 尚未完成。

## 主機與安裝

| 項目 | 組合 | 等級 | 證據／限制 |
|---|---|---|---|
| Package | Python 3.12、3.13、3.14 | Validated | GitHub CI matrix、wheel smoke test |
| Host | Ubuntu Linux + ROS2 Jazzy | Validated | 主要開發、Isaac Sim/Nav2 與 TUI 實測環境 |
| Host | Linux without ROS2 | Supported | 聊天、規劃、eval、文件與 scaffold；ROS 指令誠實 unavailable |
| Host | macOS | Experimental | 套件依賴可安裝；ROS2/Isaac 路徑未在 CI 或現場驗證 |
| Install | `uv tool install .` | Validated | 產生全域 `JenAI`／`jenai` entry points |
| Development | `uv sync` + `uv run JenAI` | Validated | 原始碼與 lockfile 開發流程 |

## ROS2、模擬與載具

| 項目 | 組合 | 等級 | 證據／限制 |
|---|---|---|---|
| ROS2 host | Jazzy | Validated | bridge、Nav2、E2/E3/B4 與 doctor |
| ROS2 vehicle | Humble ↔ Jazzy host | Experimental | 文件與協定設計涵蓋；跨機 DDS 尚未完成正式驗收 |
| Simulation | Isaac Sim + Nav2 + Ackermann UGV | Validated | Twin G1–G5、E2 消融、B4 20h、四角預檢 |
| Physical Ackermann | 小型 ROS2 Ackermann 車 | Experimental | 先前同指令整合作背景；本版未完成正式實體任務集 |
| Quadruped | Vendor ROS2/Nav2 quadruped | Planned | 高階 API 可移植設計；adapter、步態 API 與物理 PoC 未完成 |
| Unknown-map exploration | Frontier SLAM | Not supported | `/explore` 僅巡遊已知且合格的儲存點位 |

## 模型與介面

| 項目 | 組合 | 等級 | 證據／限制 |
|---|---|---|---|
| Local model | Ollama + pinned qwen3.6:35b | Validated | DGX Spark TUI/E3；正式實驗必須保存完整 model tag |
| Cloud model | NVIDIA OpenAI-compatible endpoint | Supported | provider abstraction、config 與測試；網路／金鑰由部署者提供 |
| Other OpenAI-compatible providers | Custom base URL/model | Experimental | SDK 相容不代表工具呼叫品質相同，需跑 E1/E3 acceptance |
| TUI | Local terminal | Validated | 主要操作面、批准、取消、Slash/NL |
| WebUI | localhost／isolated LAN | Supported | token auth；不得直接公開到 internet |
| MCP | stdio, read-only by default | Supported | action tools 必須明確 `--allow-actions` |

新增一個 `Validated` 組合前，必須保存版本、環境、固定任務集、成功／失敗結果與事故
紀錄；「能啟動」不等於「載具能力已驗證」。
