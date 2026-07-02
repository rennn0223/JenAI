# JenAI 技術指南(從零到有)

> 給新加入的工程師:這份文件讓你在一台新機器上把 JenAI 建起來、理解每個模組在做什麼、知道怎麼擴充。讀完你應該能獨立開發。
> 對應版本:v0.4/v0.5 系列(2026-07)。

## 1. JenAI 是什麼

**用對話和 slash 指令操作 ROS2 機器人的終端介面** —— 可以想成「機器人的 Claude Code」。核心特性:

- **TUI 優先**(Textual):聊天、`/plan`/`/run` 代理任務、`/ros` 檢查、`/route`/`/mission` 導航、`/vision` 視覺
- **誠實回報**:沒有 ROS、沒有 Nav2、沒有金鑰時明確說 unavailable,絕不假裝成功
- **危險動作要批准**:所有會動到機器人的操作(pub、drive、route、shell)先出審批卡
- **模型雲地隨切**:任何 OpenAI 相容端點(NVIDIA NIM、Ollama⋯),`/provider`、`/model` 即時切換
- **WebUI 儀表板**:手機也能看狀態、下指令、批准動作、看即時地圖
- **daemon 常駐**:規則觸發(如電量低回充),預設只通報、明確授權才動作

## 2. 從零建置

### 2.1 前置環境

| 需求 | 說明 |
|---|---|
| Linux(aarch64 或 x86_64) | 開發機是 Jetson(Ubuntu 24.04);macOS 可跑無 ROS 模式 |
| [uv](https://docs.astral.sh/uv/) | 唯一必裝工具;Python 3.12+ 由 uv 自動管理 |
| ROS2 Jazzy(選配) | 只有控真機/模擬器需要;沒有它聊天與 `/plan` 照常 |
| Nav2(選配) | `/route`、`/mission`、daemon goto 需要 |
| Ollama(選配) | 全地端模型;或用雲端 API 金鑰 |

系統 Python 需可 `import rclpy`(裝 ROS 就有)。橋接程序(見 §4.3)另需 `python3-pil`、`python3-numpy`(Ubuntu 的 ROS desktop 安裝通常已帶)。

### 2.2 安裝步驟

```bash
# 1) 取得程式碼並安裝依賴(uv.lock 已鎖好所有平台的 wheel)
git clone <repo-url> ~/JenAI && cd ~/JenAI
uv sync

# 2) 首次執行 → setup wizard 建 ~/.config/jenai/config.toml
uv run JenAI

# 3) API 金鑰(擇一:雲端金鑰 or 本機 Ollama 免金鑰)
printf 'NVIDIA_API_KEY=nvapi-…\n' > ~/.config/jenai/.env && chmod 600 ~/.config/jenai/.env
#    JenAI 啟動時自動載入 .env(所有啟動方式一致);shell 已 export 的變數優先

# 4) 地點檔(路徑導航用;也可以之後在 TUI 裡 /loc add here 現場建)
cp locations.example.toml ~/.config/jenai/locations.toml   # 再編輯座標

# 5) 一鍵啟動器(自動 source ROS2)
ln -sf "$PWD/scripts/jenai" ~/.local/bin/jenai

# 6) 驗收
jenai doctor        # 每一項應為 pass/warn,不該有意外的 fail
```

`~/.config/jenai/` 下的檔案:`config.toml`(provider/model/route_adapter)、`.env`(金鑰)、`locations.toml`(地點)、`rules.toml`(daemon 規則,選配)、`sessions/`(對話狀態)。路徑解析尊重 `JENAI_CONFIG`、`XDG_CONFIG_HOME`、`APPDATA`。

### 2.3 設定檔重點

```toml
# ~/.config/jenai/config.toml
active_provider = "local"          # /provider 可即時切換
route_adapter = "nav2"             # "stub"(預設,不動真機)| "nav2"(真的送 goal)
locations_path = "locations.toml"  # 相對於 config.toml 所在目錄

[provider_profiles."local"]
provider = "ollama"
base_url = "http://localhost:11434/v1"
api_key_env = ""                   # Ollama 免金鑰

[model_bindings]                   # /model 可即時切換
chat = "qwen3:8b"
vision = "qwen3.6:latest"          # 要挑有 vision capability 的模型
# plan / route / default …
```

## 3. 日常使用

### 3.1 TUI(`jenai` 或 `uv run JenAI`)

| 類別 | 指令 | 說明 |
|---|---|---|
| 對話 | 直接打字 | 走 chat 模型;`!<cmd>` 跑 shell |
| 任務 | `/plan <任務>`、`/run <任務>`、`/why`、`/review`、`/abort` | 代理規劃/執行(工具呼叫要批准) |
| ROS 檢查 | `/ros topics`、`/ros topic-info <t>`、`/ros schema <t>`、`/ros echo <t>` | 唯讀,不需批准 |
| 動作 | `/ros pub …`、`/ros drive …`、`/drive 前進兩秒`、`/mission kitchen, lobby` | 需批准 |
| 導航 | `/route from A to B` | 需批准;Nav2 模式下**即時顯示剩餘距離,Esc 真的取消 goal** |
| 地點 | `/loc list`、`/loc show <名>`、**`/loc add here <名>`** | add here 抓當下機器人位置存檔 |
| 視覺 | `/vision image <路徑>`、**`/vision camera [topic]`** | camera 從相機 topic 抓一張 frame 給 VLM |
| 模型 | `/model`(列出+編號切換)、`/models`、`/provider <名>`、`/providers` | 即時生效並持久化 |
| 系統 | `/status`、`/doctor`、`/config`、`/permissions`、`/help`、`/clear`、`/quit` | |

### 3.2 WebUI(`jenai web`,預設 127.0.0.1:8760)

Console(chat + slash + **確認按鈕**,手機可批准)、Status 卡片(5 秒自動更新)、**Map 卡片**(已存地點 + 機器人即時位置與朝向,2 秒更新)。動作類指令一律回 confirm token,由伺服器端一次性持有 —— 瀏覽器無法偽造或重放。

### 3.3 daemon(`jenai daemon`)

```bash
cp rules.example.toml ~/.config/jenai/rules.toml   # 編輯規則
jenai daemon                                        # Ctrl-C 停止
```

規則 = 監看一個 topic 欄位 + 門檻(below/above/equals)+ 冷卻時間。`action = "notify"` 只通報;`"goto <地點>"` 要**同時** `auto_approve = true` 且 `route_adapter = "nav2"` 才會真的移動,否則印出「本來會做什麼」。

## 4. 架構

### 4.1 資料流總覽

```
使用者 ─ TUI(Textual)/ WebUI(http.server)/ CLI(Typer)/ daemon
              │
              ├── providers/  ← OpenAI 相容 API(NVIDIA、Ollama…):chat、JSON、vision、agent 模型
              ├── agent/      ← /plan /run 的代理協調(規劃→批准→執行→回報)
              ├── tools/      ← 每個能力的純邏輯核心(*_core.py)+ agent 工具包裝(*_agent_tools.py)
              │       │
              │       ├── adapters/ros2_adapter.py   ← ros2 CLI subprocess(topics/echo/pub)
              │       └── bridge/                    ← rclpy 常駐 sidecar(pose/Nav2回饋/相機/watch)
              │
              ├── config/ + state/ + schemas/        ← 設定、run 記錄、pydantic 資料模型
              └── doctor/                            ← 環境健檢
```

### 4.2 模組導覽(每個檔案做什麼)

| 模組 | 行數 | 職責 |
|---|---|---|
| `cli/main.py` | 347 | Typer 進入點:TUI(預設)、`doctor`、`web`、`daemon`、`loc`、`route`、`version`;callback 統一載入 `.env` |
| `tui/app.py` | 1134 | App 殼:輸入分發、spinner、Esc 中斷、審批卡流程、agent 執行 |
| `tui/robot_commands.py` | 575 | Mixin:`/ros` `/route` `/mission` `/drive` `/loc` `/vision` + bridge 生命週期與 live 導航 |
| `tui/info_commands.py` | 292 | Mixin:`/help` `/status` `/doctor` `/model` `/provider` 等資訊類 |
| `tui/panels.py` | 320 | 純視覺:WelcomePanel、TimelineItem、OutputPanel、CommandPalette、色票 |
| `tui/widgets/` | ~200 | ApprovalCard(1/2/3 鍵選擇)、Plan/Tool/Error blocks |
| `bridge/ros_bridge.py` | 287 | **系統 Python** 下的 rclpy 常駐節點:JSON-over-stdio;pose、nav_send/feedback/cancel、capture_frame、watch |
| `bridge/client.py` | 223 | venv 側非同步 client:spawn、request futures、事件路由 |
| `tools/*_core.py` | — | 各能力純邏輯(可單測):route 解析與執行、mission 步進、drive 解析、vision、shell 風險評估 |
| `tools/nav_live.py` | 97 | bridge 版導航:回饋串流、逾時、取消(Esc → Nav2 cancel) |
| `agent/orchestrator.py` 等 | ~600 | /run 代理:規劃、specialist 工具、批准中斷、guardrails、tracing |
| `providers/chat.py` | 289 | OpenAI 相容呼叫:`ask_provider`、`ask_json`、`ask_vision_json`、`list_provider_models`、**`parse_json_reply`(寬容解析,thinking 模型必備)** |
| `adapters/ros2_adapter.py` | 304 | `ros2` CLI subprocess 包裝(有 timeout、錯誤分類) |
| `adapters/locations.py` | 182 | locations.toml 載入/儲存/模糊搜尋/`append_location` |
| `adapters/route_adapter.py` | 91 | RouteAdapter 協定:`stub`(誠實拒絕)/`nav2`(CLI send_goal,bridge 不可用時的後備) |
| `daemon/engine.py` | 143 | 規則引擎純邏輯:條件、冷卻、安全 gating(可單測) |
| `daemon/runner.py` | 81 | bridge watch → queue → engine → (獲准才)navigate_live |
| `webui/server.py` | 301 | http.server:`/api/status` `/api/command` `/api/confirm` `/api/map`;PoseCache 背景執行緒 |
| `webui/render.py` | 474 | 純渲染:儀表板 HTML/CSS/JS(含 SVG 地圖) |
| `webui/commands.py` | 304 | Web 版指令執行 + confirm 動作封存 |
| `config/store.py` | 192 | config/.env 載入(`JENAI_CONFIG`/XDG/APPDATA;shell 優先於 .env) |
| `doctor/checks.py` | 338 | 健檢:python/uv/venv/config/env_file/ros2/provider/locations/webui |
| `schemas/` | ~500 | 全部 pydantic 模型(`extra="forbid"`):Location、RouteOutput、DoctorResult、Rule… |

### 4.3 關鍵設計決策(為什麼長這樣)

**rclpy bridge 是獨立程序,不是 import。** venv(uv 管理的 Python)看不到 rclpy,ROS 的 PYTHONPATH 又會遮蔽 venv 依賴(pytest 都得 `env -u PYTHONPATH` 跑)。解法:`bridge/ros_bridge.py` 由 `/usr/bin/python3` 跑(source ROS 後 exec),與 venv 完全隔離,講 newline-delimited JSON。venv 側 `RosBridgeClient` 用 asyncio 管 request/response(以 id 配對 future)和事件(nav_feedback、watch)。CLI 做不到的即時回饋、取消、影像抓取全靠它。**bridge 檔案內絕不能 import jenai**(它跑在 venv 外)。

**誠實回報原則。** 每條執行路徑必須能回 `unavailable` 並說明原因(「goal 沒有送出」),UI 用 warn 而非 success 呈現。寫新工具時遵守:不確定就說不確定。

**批准模型。** 動作類指令建立 run + ApprovalCard;批准後的執行包成可取消的 active task(長導航要能 Esc)。WebUI 的 confirm 是伺服器端一次性 token —— 瀏覽器只拿到 id,動作本體不出伺服器。daemon 則是「規則明確 `auto_approve` 才動」。三個介面,同一哲學:**沒有明確授權,機器人不動**。

**Provider 全走 OpenAI 相容端點。** 一個抽象吃遍 NVIDIA NIM / Ollama / 其他;`parse_json_reply` 寬容處理 thinking 模型的 ```json 圍欄與前後綴文字 —— 所有結構化輸出(vision、route 解析)都經過它。

**佔位符防呆。** 指令面板補完的 `<name|number>` 模板原文送出會被擋(曾把字面佔位符存成 model binding 弄壞 config)。

### 4.4 測試與 CI

```bash
env -u PYTHONPATH uv run pytest     # 必須 unset PYTHONPATH(ROS 遮蔽問題)
env -u PYTHONPATH uv run ruff check src tests
```

- **CI**(`.github/workflows/ci.yml`):ubuntu-latest、無 ROS —— 測試設計成不依賴 ROS(bridge 用 `tests/unit/fake_bridge.py` 這個純 stdlib 假程序講同一套協定)
- **TUI 測試**:Textual `app.run_test()` + `handle_user_text()`;monkeypatch 目標在 handler 所在模組(如 `jenai.tui.robot_commands.route_execute`)
- **本機 E2E 手法**(開發時驗真鏈路):`scratchpad` 裡跑假節點 —— fake Nav2 action server、fake camera publisher、fake battery —— 全是真 rclpy、真協定,TUI/daemon 分不出真假。參考 git log 中各功能 commit 的驗證描述

### 4.5 擴充指南(常見四件事)

**加一個 slash 指令**:`tui/app.py` 的 `SLASH_COMMANDS` 加 `SlashCommand("/foo", "說明", "/foo <arg>")` → `_resolve_command_handler` 的 handlers dict 加映射 → 在對應 mixin(info/robot)寫 `async def _show_foo(self, arg)` → `help_content.py` 補條目 → 測試用 `app.handle_user_text("/foo x")`。

**加一個 bridge 能力**:`ros_bridge.py` 的 `BridgeNode` 加方法 + `_handle()` 加 op 分支(記住:只能用系統 Python 有的套件)→ `client.py` 加 typed helper → `fake_bridge.py` 補假回應 → 測試。

**加一個 daemon 條件/動作**:條件在 `engine.py` 的 `condition_met`;動作在 `Rule._check` 白名單 + `RuleEngine.handle_event` 的 gating + `runner.py` 執行。**預設必須是不動作**。

**加一個 provider**:通常不用寫程式 —— OpenAI 相容端點只要新 profile(`/provider` 切換)。特殊行為(如 NVIDIA 別名)加在 `providers/chat.py` 的 `resolve_model_alias`。

## 5. 後續建議(接下來值得做)

1. **MCP server 化**:把 tool registry 映射成 MCP tools,讓 Claude Code/Desktop 直接控機器人 —— 現有結構幾乎可直翻
2. **WebUI SSE**:把 5s/2s 輪詢換成 Server-Sent Events;地圖上點一下直接下導航 goal(走既有 confirm 流程)
3. **多機器人**:namespace 切換(像 `/provider` 一樣的 `/robot <ns>`),bridge 已可帶 namespace 參數擴充
4. **語音**:Jetson 上 Whisper(STT)+ Piper(TTS),入口掛在 TUI 的輸入層
5. **`/loc add` 的 CLI 版**與地點編輯(rename/delete/alias)
6. **相機串流預覽**:WebUI 地圖旁小視窗(bridge 已能抓 frame,補連續模式即可)
7. **doctor 加 bridge 檢查**:`env_file` 之後加一項 `ros_bridge`(spawn + ping)
8. **Nav2 進階**:waypoint following(`FollowWaypoints` action)取代 mission 的逐點 send

---
*其他文件:[ARCHITECTURE.md](ARCHITECTURE.md)(原始設計)、[COMMANDS.md](COMMANDS.md)、[DATA_SCHEMAS.md](DATA_SCHEMAS.md)、[FEATURES.md](FEATURES.md)。本指南以實際程式碼為準(v0.4/v0.5)。*
