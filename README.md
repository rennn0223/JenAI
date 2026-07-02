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
- **模型雲地隨切**：`/provider`、`/model` 即時切換 NVIDIA 雲端／本機 Ollama，含編號快選
- **Human-in-the-loop 批准機制**：敏感操作一律暫停等待人工核准，Enter 批准、Esc 拒絕
- **TUI + WebUI 雙介面**：terminal 優先；WebUI 有對話 console、即時地圖、手機批准
- **daemon 常駐模式**：`jenai daemon` 規則觸發（如電量低回充），預設只通報、明確授權才動作

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
| [docs/COMMANDS.md](docs/COMMANDS.md) | CLI + slash 命令完整規格 |
| [docs/FEATURES.md](docs/FEATURES.md) | 14 個核心功能可實作規格 |
| [docs/UX.md](docs/UX.md) | TUI/WebUI 互動設計規格 |
| [docs/DATA_SCHEMAS.md](docs/DATA_SCHEMAS.md) | 共用資料結構定義 |
| [docs/STATE_MACHINE.md](docs/STATE_MACHINE.md) | Run 狀態機設計 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 模組架構設計 |
| [docs/MOSCOW.md](docs/MOSCOW.md) | v0.1.0 功能優先級 |

---

## 技術棧（規劃中）

- **Agent Framework**：OpenAI Agents SDK
- **LLM Provider**：LiteLLM（多 provider 統一接口）
- **TUI**：Textual（Python）
- **WebUI**：待定
- **ROS2**：Jazzy
- **語言**：Python 3.12+

---

## 狀態

> ✅ 核心功能：CLI 入口、setup wizard、`doctor`、`/plan`、ROS2 工具（`topics`／`topic-info`／`schema`／`echo`／`pub`／`drive`／`state`）、`/drive` 自然語言控車、`/route`、`/vision image`、`/shell`、Claude 風格 TUI（項目符號時間軸、編號審批、`!` bash、esc 中斷）與可互動的 WebUI（`jenai web`，含指令 console，手機 App 佈局）。
>
> ✅ 完整 Agent 架構（基於 openai-agents SDK）：**多-agent handoffs**（Supervisor + ROS/Motion/Navigation/Perception 專職 agent）、**跨重啟對話記憶**（`Session`，依專案，`/clear` 清除）、**安全 guardrails** + 確定性速度夾限（涵蓋 Twist/TwistStamped）、**閉環感知**（`/ros state`）、**Nav2 導航**（誠實回報）、**確定性任務執行器**（`/mission`）、**本地 tracing**（取代 OpenAI 後端，不外傳）。詳見 [`docs/FEATURES.md`](docs/FEATURES.md)。
>
> 🚧 後續：`/run` 多步自主的可靠化、Nav2/感知端到端驗證、連續 `/ros echo` streaming、WebUI 完整 timeline。
