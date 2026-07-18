# SUPPORT_MATRIX — 已驗證組合與支援等級

> `Validated` 只表示「該列所寫的精確組合」有目前 release/commit 的可重播證據；
> `Supported` 表示設計與自動化測試涵蓋，但部署端仍須驗收；`Experimental` 不可作
> 採購或安全承諾；`Planned` 尚未完成。能啟動不等於能力已驗證。

## 目前開發／現場環境指紋（2026-07-18）

| 欄位 | 觀察值 | 證據邊界 |
|---|---|---|
| Host | NVIDIA DGX Spark、aarch64、Ubuntu 24.04.4 LTS | 本機即時觀察；不是過去所有實驗的回溯 metadata |
| ROS | ROS 2 Jazzy（`/opt/ros/jazzy`）、Nav2 bringup 可解析 | RMW 未明式設定；舊實驗 artifact 未完整保存套件版本 |
| Simulator | Isaac Sim 5.1.0-rc.19（local build）、RViz2 Carter navigation workspace | 當前程序可觀察；候選 commit 的 HIL/10-run artifact 尚待合法起點後重跑 |
| Domain | 當前 shell 預設 ROS_DOMAIN_ID 0 | 純模擬可驗功能但不提供實體隔離；Twin 正式 gate 需不同 domain |
| Model | Ollama `qwen3.6:35b` | 完整 model digest 未封存，不宣稱跨重拉位元可重現 |

## 主機、套件與安裝

| 項目 | 精確組合 | 等級 | 證據／限制 |
|---|---|---|---|
| Package CI | Python 3.12、3.13、3.14；GitHub-hosted Linux x86_64 | Validated | CI matrix、ruff、pytest、coverage gate |
| Release wheel | CI 建出的 wheel → 隔離環境 → `JenAI`/`jenai`、doctor、uninstall | Validated | 驗的是 wheel，不等同 source install；每版 hash 以 release SHA256SUMS 為準 |
| Source install | pinned tag/commit + `uv tool install .` | Supported | 需建置工具且依賴解析可能隨時間改變；正式交付優先 wheel + constraints |
| Development | `uv sync --frozen` + `uv run JenAI` | Supported | `uv.lock` 鎖開發／CI解析；不是一般使用者交付路徑 |
| DGX Spark host | Ubuntu 24.04.4 LTS、aarch64 | Supported | 主要開發機；候選版 fresh-machine 非作者驗收仍為外部 gate |
| Linux without ROS2 | Python 功能、聊天、規劃、eval、scaffold | Supported | ROS/導航指令誠實 unavailable |
| macOS | Python 套件／TUI | Experimental | ROS2、Isaac、Nav2 路徑未在 CI 或現場驗證 |

## ROS2、模擬與載具

| 項目 | 精確組合 | 等級 | 證據／限制 |
|---|---|---|---|
| ROS2 host | Jazzy + Nav2 on current DGX Spark | Supported | bridge/doctor/單測與既有實驗涵蓋；候選 commit HIL artifact 未關閉 |
| ROS2 vehicle | Humble ↔ Jazzy host | Experimental | 跨機 DDS 尚未完成正式固定任務集 |
| Simulation | Isaac Sim 5.1.0-rc.19 + ROS2 Jazzy/Nav2 + Carter/Leatherback acceptance scenes | Supported | 有既有 E2/B4/現場證據但 execution revision/場景 hash 不完整；需同 commit 10-run 才升 Validated |
| Physical Ackermann | 小型 ROS2 Ackermann 車 | Experimental | 先前同指令整合作背景；本版未完成正式實體任務集 |
| Quadruped | Vendor ROS2/Nav2 quadruped | Planned | 只有高階 schema 設計；adapter、步態 API 與物理 PoC 未完成 |
| Unknown-map exploration | Frontier SLAM | Not supported | `/explore` 僅巡遊已知且合格的儲存點位 |

## 模型與介面

| 項目 | 精確組合 | 等級 | 證據／限制 |
|---|---|---|---|
| Local model | Ollama + `qwen3.6:35b` | Supported | DGX Spark TUI/E3/E4；缺完整 digest，正式新實驗必須封存 |
| Cloud model | NVIDIA OpenAI-compatible endpoint | Supported | provider abstraction/config 測試；資料會離開本機，見 SECURITY |
| Other provider | Custom OpenAI-compatible base URL/model | Experimental | API 相容不代表工具呼叫品質相同；需跑 E1/E3 |
| TUI | Local terminal | Supported | 主要操作面；候選版完整 wheel + 實際 viewport/HIL 尚待終局驗收 |
| WebUI | localhost／isolated LAN | Supported | token auth；不得直接公開到 internet |
| MCP | stdio、read-only by default | Supported | action tools 必須明確 `--allow-actions` |

## 升級規則

任何列升成 `Validated` 前，必須保存：JenAI commit/tag、OS/CPU、ROS/Nav2/RMW、
ROS_DOMAIN_ID、simulator/場景/map hash、provider/model digest、固定任務集、完整分母、
成功與失敗、incident，以及不可覆寫的 artifact hash。缺欄位只能寫 `not recorded`，
不得事後推定。
