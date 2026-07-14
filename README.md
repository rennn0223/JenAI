# JenAI

> An agentic AI terminal assistant for ROS2-based robot systems.

JenAI 是一套以 terminal 為核心的 AI Agent 操作介面，專為機器人開發者設計。它整合大型語言模型、ROS2 工具鏈、視覺理解能力與 human-in-the-loop 批准機制，讓你能以自然語言規劃、執行、監控機器人任務。

---

## 核心能力

- **自然語言任務規劃與執行**：以 `/plan` 與 `/run` 驅動 agent 完成多步驟任務
- **ROS2 整合**：topics 探索、schema 解析、echo 監看、pub 控制
- **即時導航**：`/route`、`/mission` 走 Nav2，剩餘距離即時顯示、Esc 真的取消 goal（rclpy bridge）
- **地點管理**：`/loc add here <名字>` 抓機器人當下位置存檔，邊走邊建地圖點位
- **視覺理解**：`/vision image <路徑>` 分析圖片；`/vision camera` 直接抓相機畫面問「你看到什麼」
- **持續感知**：`/perception start` 相機→VLM 定頻迴圈，輸出結構化場景分析（affordances 可觸發 daemon 規則；只觀察，動作一律走批准）
- **模型雲地隨切**：`/provider`、`/model` 即時切換 NVIDIA 雲端／本機 Ollama，含編號快選
- **緊急停止**：TUI `/stop`、WebUI 紅色 STOP 鈕、MCP `stop` 工具——取消導航 + 送零速度,不需批准、忙碌中也能搶佔；bridge 端 watchdog 在 client 斷線時自動停車
- **Human-in-the-loop 批准機制**：敏感操作一律暫停等待人工核准，Enter 批准、Esc 拒絕
- **TUI + WebUI 雙介面**：terminal 優先；WebUI 有對話 console、即時地圖、手機批准
- **daemon 常駐模式**：`jenai daemon` 規則觸發（如電量低回充），預設只通報、明確授權才動作
- **MCP server**：`jenai mcp` 把機器人工具開放給 Claude Code／Desktop 等 MCP client（預設唯讀，`--allow-actions` 才開放導航）
- **權限三模式**：TUI Shift+Tab 循環切換「審批／規劃／自動」——裸自然語言依模式路由（規劃模式只產計畫不執行、自動模式批准卡自動批准並明示），急停與硬限速在任何模式都不放鬆
- **Development copilot**：`JenAI scaffold "<描述>"` 自然語言生成 ROS2 套件（boilerplate 確定性生成 + LLM 寫 node 主體 + 送出前審閱；`--build` 生成即 colcon 驗證）；`skills/*.toml` 檔案定義技能擴充 slash 指令
- **決策核心與評測**：`decision_core` 有界動作集單選決策（越界一律降級 refer）+ `JenAI eval` E1 場景評測（accuracy／unsafe rate／refer rate）
- **巡邏日報**：`/report` 確定性日報 + LLM 摘要（離線誠實降級），`/report list` 回看歷次

---

## 快速入門

```bash
# 開發環境
uv sync
uv run pytest

# 首次執行（自動進入 setup wizard）
uv run JenAI

# 重新設定（先備份 config；保留 .env 與 locations）
uv run JenAI onboard

# 環境健康檢查
uv run JenAI doctor

# 啟動 WebUI 監控中心
uv run JenAI web
```

### 在新機器上安裝（fresh clone）

repo 本身可攜：依賴鎖在 `uv.lock`（含 aarch64／x86_64／macOS wheel），原始碼沒有寫死任何機器路徑。有三個檔案**不在 repo 裡**（使用者設定／機密），換機器後要重建：

| 檔案（`~/.config/jenai/`） | 怎麼來 |
|---|---|
| `config.toml`（provider／model） | 首次 `uv run JenAI` 自動跑 setup wizard 建立 |
| `.env`（API 金鑰） | 手動一行（見下方「API 金鑰」）；JenAI 啟動時自動載入，`uv run JenAI` 也吃得到 |
| `locations.toml`（地點） | 依 [`locations.example.toml`](locations.example.toml) 填 |

```bash
git clone <repo-url> ~/JenAI && cd ~/JenAI
uv sync                 # 依 uv.lock 裝依賴（需要時 uv 會自動裝 Python 3.12+）
uv run JenAI            # 首次 → setup wizard → 進 TUI
```

- **ROS2 是選配**：沒裝 ROS 的機器，`/ros*`、`/drive`、`/route` 會誠實回報 unavailable（不會崩），聊天／`/plan` 照常；控真車才需要 ROS2 Jazzy + Nav2。
- 需要網路 + 金鑰（或本機 Ollama）才能實際呼叫模型；純離線無法。

### 一鍵啟動（含 ROS2）

啟動腳本 [`scripts/jenai`](scripts/jenai) 會在「還沒有可用的 ROS 環境」時 source ROS2
（已經 source 好的環境——包括其他發行版——會被尊重，不會疊加）、確保 uv，再用
`uv run` 啟動。安裝方式是連結到 PATH 上：

```bash
ln -sf "$PWD/scripts/jenai" ~/.local/bin/jenai   # 一次性安裝
jenai            # source ROS2 → 進 TUI
jenai doctor     # 環境檢查
jenai web        # WebUI 儀表板
# 覆寫路徑：JENAI_DIR=/path/to/JenAI  ROS_SETUP=/opt/ros/humble/setup.bash jenai
```

**API 金鑰用 `.env`（建議）**：把 provider 金鑰放在 `~/.config/jenai/.env`
（`chmod 600`，跟 `config.toml` 同目錄），**JenAI 啟動時自動載入**——不論用
`jenai`、`uv run JenAI` 還是 venv script 啟動都一樣。shell 已 export 的變數
優先於檔案內容。這比寫在 `.bashrc` 好——不受「互動 shell 才載入」限制。
setup 欄位預期填 `NVIDIA_API_KEY` 這類變數名稱;若誤貼 key 本體,v0.25.1 起會
自動搬到權限 `0600` 的 `.env`,不再把 secret 留在 `config.toml`：

```bash
printf 'NVIDIA_API_KEY=nvapi-…\n' > ~/.config/jenai/.env && chmod 600 ~/.config/jenai/.env
# 覆寫路徑：JENAI_ENV_FILE=/path/to/.env jenai
```

### 載具設定（`[vehicle]`）

載具差異（topic、速限）唯一的家——換車/換狗只改這段,不改程式:

```toml
[vehicle]
type = "ackermann"          # ackermann | diff | quadruped
cmd_vel_topic = "/cmd_vel"
cmd_vel_stamped = false     # true 時發 TwistStamped
camera_topic = "/camera/image_raw"   # /vision camera 與 MCP camera_look 預設
max_linear = 1.0            # m/s — 執行期硬限速(LLM/使用者給再大都會被夾住)。
max_angular = 2.0           # rad/s — 以上為安全預設;依你的車實測後再調
                            # (例:Leatherback 用 2.0 / 0.53)
```

### 使用本地 Ollama

Ollama 提供 OpenAI 相容端點，設定要點：

- `base_url` = `http://localhost:11434/v1`（**要有 `/v1`**）
- model 用純 tag（例如 `qwen3.6:35b`，**不要** `ollama/` 前綴）
- `api_key_env` 留空即可（本地 keyless，不需金鑰）

設定檔位置：`~/.config/jenai/config.toml`。

---

## 文件導覽

| 文件 | 說明 |
|---|---|
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | **零基礎上手手冊**：不熟終端機也能 20 分鐘從空機到第一句對話（小白先讀這份） |
| [docs/TECHNICAL_GUIDE.md](docs/TECHNICAL_GUIDE.md) | **從零到有技術指南**：建置、架構、每個模組做什麼、擴充方式（開發新人先讀這份） |
| [docs/CODE_TOUR.md](docs/CODE_TOUR.md) | **全程式碼逐檔導讀**：每個檔案在做什麼、為什麼這樣設計 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | **前瞻主圖**：現況快照、演進五軌、工程健康度、風險登記 |
| [docs/V1_GATE.md](docs/V1_GATE.md) | v1.0 驗收基準與兩層分工（agent 可獨力 vs 需客戶下場） |
| [docs/HANDOFF.md](docs/HANDOFF.md) | 交接與臨別備忘：狀態、方法、誠實的話 |
| [docs/ONBOARDING.md](docs/ONBOARDING.md) | **機器人上線手把手**：裸 ROS2 → 建圖 → 定位 → Nav2 → 第一次 `/route`（`jenai doctor` 的 nav 檢查就是進度條） |
| [docs/PROJECT_DIRECTION.md](docs/PROJECT_DIRECTION.md) | **專案方向**：三方視角收斂的六層架構、功能優先序（必做 M1–M5）與可用性評估 |
| [docs/COMMANDS.md](docs/COMMANDS.md) | CLI + slash 命令完整規格 |
| [docs/FEATURES.md](docs/FEATURES.md) | 設計期文件：14 個核心功能規格 |
| [docs/UX.md](docs/UX.md) | 設計期文件：TUI/WebUI 互動設計 |
| [docs/DATA_SCHEMAS.md](docs/DATA_SCHEMAS.md) | 設計期文件：共用資料結構 |
| [docs/STATE_MACHINE.md](docs/STATE_MACHINE.md) | 設計期文件：Run 狀態機 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 設計期文件：模組架構 |
| [docs/MOSCOW.md](docs/MOSCOW.md) | 設計期文件：v0.1.0 功能優先級 |

---

## 技術棧

- **語言**：Python 3.12+（依賴由 [uv](https://docs.astral.sh/uv/) 管理，`uv.lock` 鎖定多平台 wheel）
- **TUI**：Textual；**WebUI**：stdlib `http.server`（零額外依賴）；**CLI**：Typer
- **LLM Provider**：`openai` SDK 打任何 OpenAI 相容端點（NVIDIA NIM 雲端／Ollama 地端）
- **Agent Framework**：openai-agents SDK（多-agent handoffs、本地 tracing）
- **ROS2**：Jazzy；Nav2（Smac Hybrid-A* + RPP，阿克曼）；rclpy 走獨立 bridge 子程序（系統 Python）
- **MCP**：官方 `mcp` SDK（FastMCP，stdio transport）

---

## 狀態（v0.33.1，2026-07）

> ✅ **安全鏈**：緊急停止（TUI `/stop`／WebUI STOP 鈕／MCP `stop`／daemon `halt`，免批准可搶佔、跨程序 cancel-all）、bridge watchdog（client 斷線自主停車）、執行期硬限速（`[vehicle]`）、HITL 編號審批卡、daemon 明確授權 gating、權限模式的自然語言路由例外網。
>
> ✅ **操作面**：串流聊天、`/plan`／`/run` 多-agent、忙碌時 FIFO 指令排隊（`/queue` 管理）、ROS2 工具全套、`/drive` 自然語言控車、`/route` 即時導航（Nav2／odom 直驅雙路徑，剩餘距離+Esc 真取消）、depth stop-and-go 局部繞障（資料逾時即停）、`/mission`／`/patrol`（循環巡邏+每點 VLM 拍照回報）／`/dock`、`/loc add here|gps` 現場建點與 GPS 註冊、`/vision image|camera`、`/perception` 持續感知、`/report` 巡邏日報、`/model`／`/provider` 雲地即時切換。
>
> ✅ **介面**：Claude 風格 TUI（會動的吉祥物+權限三模式 Shift+Tab）、多頁 WebUI（Console／Camera／Status／API，token 認證+手機批准+即時地圖+STOP）、MCP server、daemon 常駐、`skills/*.toml` 檔案定義技能。全部走同一套共用原語（導航調度、急停、相機分析、地點載入各只有一份）。
>
> ✅ **Copilot 與決策腦**：`JenAI scaffold` 自然語言生成 ROS2 套件（`--build` 生成即驗證閉環）；`decision_core` 有界動作決策 + `JenAI eval` E1 評測（論文工具鏈）。
>
> ✅ **工程**：430+ 自動化測試（無 ROS 的 CI 可全跑）、Python 3.12／3.13／3.14 CI 矩陣與三道閘（安全鏈覆蓋倒退閘+架構鐵律+wheel 冒煙）、rclpy bridge 協定有純 stdlib fake、批准中斷可跨重啟恢復、誠實回報原則貫穿每條路徑。
>
> ✅ **Twin Gate**（[TWIN_SETUP.md](docs/TWIN_SETUP.md)）：導航目標先在數位孿生（獨立 ROS_DOMAIN_ID）預演，G1 碰撞／G2 超時／G3 禁區／G4 終點偏差／G5 Nav2 失敗 → pass／block／refer；`[twin]` 一行開關，所有導航入口與 daemon 全部過閘，daemon 自主路徑 refer 一律視同 block。
>
> 🚧 **進行中**（見 [ROADMAP.md](docs/ROADMAP.md)／[V1_GATE.md](docs/V1_GATE.md)）：v1.0 等實機驗測數據（客戶層二 B1–B7）；M6 自主決策常駐迴圈是 v2 主軸（決策腦零件已備）。
