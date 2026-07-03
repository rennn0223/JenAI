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

---

## 快速入門

```bash
# 開發環境
uv sync
uv run pytest

# 首次執行（自動進入 setup wizard）
uv run JenAI

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
優先於檔案內容。這比寫在 `.bashrc` 好——不受「互動 shell 才載入」限制：

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
| [docs/TECHNICAL_GUIDE.md](docs/TECHNICAL_GUIDE.md) | **從零到有技術指南**：建置、架構、每個模組做什麼、擴充方式（新人先讀這份） |
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

## 狀態（v0.7 系列，2026-07）

> ✅ **安全鏈**：緊急停止（TUI `/stop`／WebUI STOP 鈕／MCP `stop`／daemon `halt`，免批准可搶佔）、bridge watchdog（client 斷線自主停車）、執行期硬限速（`[vehicle]`）、HITL 編號審批卡、daemon 明確授權 gating。
>
> ✅ **操作面**：串流聊天、`/plan`／`/run` 多-agent、ROS2 工具全套、`/drive` 自然語言控車、`/route` 即時導航（剩餘距離+Esc 真取消）、`/mission`／`/patrol`（循環巡邏+每點 VLM 拍照回報）／`/dock`、`/loc add here` 現場建點、`/vision image|camera`、`/model`／`/provider` 雲地即時切換。
>
> ✅ **介面**：Claude 風格 TUI、WebUI（console+手機批准+即時地圖+STOP）、MCP server、daemon 常駐。全部走同一套共用原語（導航調度、急停、相機分析、地點載入各只有一份）。
>
> ✅ **工程**：305 測試（無 ROS 的 CI 可全跑）、rclpy bridge 協定有純 stdlib fake、誠實回報原則貫穿每條路徑。
>
> ✅ **Twin Gate**（[TWIN_SETUP.md](docs/TWIN_SETUP.md)）：導航目標先在數位孿生（獨立 ROS_DOMAIN_ID）預演，G1 碰撞／G2 超時／G3 禁區／G4 終點偏差／G5 Nav2 失敗 → pass／block／refer；`[twin]` 一行開關，所有導航入口與 daemon 全部過閘，daemon 自主路徑 refer 一律視同 block。
>
> 🚧 **進行中**（見 [PROJECT_DIRECTION.md](docs/PROJECT_DIRECTION.md)）：M3 Isaac Sim 孿生場景建置（工作站作業，見 TWIN_SETUP.md）、M6 自主決策迴圈（論文主軸）。
