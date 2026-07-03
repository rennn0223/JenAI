# JenAI 技術指南(從零到有)

> 給新加入的工程師:這份文件讓你在一台新機器上把 JenAI 建起來、理解每個模組在做什麼、知道怎麼擴充。讀完你應該能獨立開發。
> 對應版本:v0.9 系列(2026-07)。專案方向與 roadmap 見 [PROJECT_DIRECTION.md](PROJECT_DIRECTION.md)。

## 1. JenAI 是什麼

**用對話和 slash 指令操作 ROS2 機器人的終端介面** —— 可以想成「機器人的 Claude Code」。核心特性:

- **TUI 優先**(Textual):聊天(串流回覆)、`/plan`/`/run` 代理任務、`/ros` 檢查、`/route`/`/mission` 導航、`/vision` 視覺
- **緊急停止**:`/stop`(TUI)、紅色 STOP 鈕(WebUI)、`stop` 工具(MCP)、`halt` 規則(daemon)——免批准、忙碌中可搶佔;bridge 端 watchdog 在 client 斷線/卡死時**自主停車**
- **任務技能**:`/patrol`(循環巡邏 + 每點拍照 VLM 回報)、`/dock`(回充)、`/mission`(多步任務)
- **持續感知**:`/perception start` 定頻相機→VLM 結構化分析;affordance 可作 daemon 規則觸發條件(與數值閾值並列),動作照走既有批准
- **誠實回報**:沒有 ROS、沒有 Nav2、沒有金鑰時明確說 unavailable,絕不假裝成功
- **危險動作要批准**:所有會動到機器人的操作(pub、drive、route、patrol、shell)先出審批卡
- **載具設定 `[vehicle]`**:cmd_vel topic、硬限速、相機 topic 集中一處——換載具改設定不改程式
- **模型雲地隨切**:任何 OpenAI 相容端點(NVIDIA NIM、Ollama⋯),`/provider`、`/model` 即時切換
- **WebUI 儀表板**:手機也能看狀態、下指令、批准動作、看即時地圖
- **MCP server**:`jenai mcp` 把機器人工具開放給 Claude Code/Desktop 等 client(預設唯讀)
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

[vehicle]                          # 載具差異唯一的家(v0.7+)
type = "ackermann"                 # ackermann | diff | quadruped
cmd_vel_topic = "/cmd_vel"
cmd_vel_stamped = false            # true 時發 TwistStamped
camera_topic = "/camera/image_raw" # /vision camera、patrol photo、MCP camera_look 預設
max_linear = 1.0                   # 執行期硬限速(m/s)——LLM/使用者給再大都會被夾住
max_angular = 2.0                  # rad/s;安全預設,依實車再調(Leatherback:2.0/0.53)
```

## 3. 日常使用

### 3.1 TUI(`jenai` 或 `uv run JenAI`)

| 類別 | 指令 | 說明 |
|---|---|---|
| **安全** | **`/stop`** | **緊急停止:取消導航 + 零速度;免批准,任務執行中也能搶佔** |
| 對話 | 直接打字 | 走 chat 模型,**token 串流即時渲染**;`!<cmd>` 跑 shell |
| 任務 | `/plan <任務>`、`/run <任務>`、`/why`、`/review`、`/abort` | 代理規劃/執行(工具呼叫要批准) |
| ROS 檢查 | `/ros topics`、`/ros topic-info <t>`、`/ros schema <t>`、`/ros echo <t>` | 唯讀,不需批准 |
| 動作 | `/ros pub …`、`/ros drive …`、`/drive 前進兩秒` | 需批准;速度過 `[vehicle]` 硬限速 |
| 技能 | `/mission kitchen, lobby`、**`/patrol A, B x3 photo`**、**`/dock`** | 需批准;patrol 可循環+每點拍照 VLM 回報,dock 找 `tags=["dock"]` 的地點 |
| 導航 | `/route from A to B` | 需批准;Nav2 模式下**即時顯示剩餘距離,Esc 真的取消 goal** |
| 地點 | `/loc list`、`/loc show <名>`、**`/loc add here <名>`** | add here 抓當下機器人位置存檔 |
| 視覺 | `/vision image <路徑>`、**`/vision camera [topic]`** | camera 預設讀 `vehicle.camera_topic` |
| 模型 | `/model`(列出+編號切換)、`/models`、`/provider <名>`、`/providers` | 即時生效並持久化 |
| 系統 | `/status`、`/doctor`、`/config`、`/permissions`、`/help`、`/clear`、`/quit` | |

### 3.2 WebUI(`jenai web`,預設 127.0.0.1:8760)

Console(chat + slash + **確認按鈕**,手機可批准)、Status 卡片(5 秒自動更新)、**Map 卡片**(已存地點 + 機器人即時位置與朝向,2 秒更新)。動作類指令一律回 confirm token,由伺服器端一次性持有 —— 瀏覽器無法偽造或重放。

### 3.3 MCP server(`jenai mcp`)

把 JenAI 的機器人工具以 [MCP](https://modelcontextprotocol.io) stdio 服務開放給任何 MCP client(Claude Code、Claude Desktop⋯):

```jsonc
// Claude Code 的 .mcp.json
{ "mcpServers": { "jenai": { "command": "uv", "args": ["run", "--project", "/home/nvidia/JenAI", "JenAI", "mcp"] } } }
```

預設**唯讀**(ros_topics/ros_topic_info/ros_echo/list_locations/robot_pose/camera_look/**stop**);啟動加 `--allow-actions` 才註冊 `navigate_to`(單飛鎖:並發呼叫回 busy,不搶佔進行中 goal)。`stop` 例外地永遠註冊——停車永遠安全。安全模型:client 端的工具批准是人閘,operator 的 `--allow-actions` 是總開關,兩層都過機器人才會動。

### 3.4 daemon(`jenai daemon`)

```bash
cp rules.example.toml ~/.config/jenai/rules.toml   # 編輯規則
jenai daemon                                        # Ctrl-C 停止
```

規則 = 監看一個 topic 欄位 + 門檻(below/above/equals)+ 冷卻時間。`action = "notify"` 只通報;`"goto <地點>"` 要**同時** `auto_approve = true` 且 `route_adapter = "nav2"` 才會真的移動,否則印出「本來會做什麼」;`"halt"`(緊急停止)**免批准**——停下來永遠是安全的,而且會先取消進行中的導航。

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
| `cli/main.py` | 371 | Typer 進入點:TUI(預設)、`doctor`、`web`、`mcp`、`daemon`、`loc`、`route`、`version`;callback 統一載入 `.env`;診斷一律走 `err_console`(stderr,保護 MCP stdout) |
| `tui/app.py` | 1241 | App 殼:輸入分發、串流聊天渲染、spinner、Esc 中斷、`/stop` 搶佔、審批卡流程、mission/patrol 執行 |
| `tui/robot_commands.py` | 689 | Mixin:`/stop` `/ros` `/route` `/mission` `/patrol` `/dock` `/drive` `/loc` `/vision` + bridge 生命週期(含 watchdog 佈署) |
| `tui/info_commands.py` | 292 | Mixin:`/help` `/status` `/doctor` `/model` `/provider` 等資訊類 |
| `tui/panels.py` | 352 | 純視覺:WelcomePanel、TimelineItem(variant 決定行距)、OutputPanel、CommandPalette |
| `tui/widgets/` | ~200 | ApprovalCard(1/2/3 鍵選擇)、Plan/Tool/Error blocks |
| `bridge/ros_bridge.py` | 412 | **系統 Python** 下的 rclpy 常駐節點:JSON-over-stdio;pose、nav_send/feedback/cancel、**halt(急停)**、**watchdog(斷線自主停車)**、capture_frame、watch |
| `bridge/client.py` | 274 | venv 側非同步 client:spawn、request futures、事件路由、halt/configure_safety |
| `tools/*_core.py` | — | 各能力純邏輯(可單測):route 解析與執行、mission 步進、drive 解析、vision、shell 風險評估 |
| `tools/nav_live.py` | 143 | bridge 版導航:回饋串流、逾時、取消、心跳餵 watchdog;**`navigate_with_fallback`(nav2-vs-CLI 調度的唯一出處,TUI/MCP 共用)** |
| `tools/skills.py` | 145 | 任務技能:`parse_patrol`/`run_patrol`(循環+觀察+失敗續行)、`find_dock` |
| `tools/safety.py` | 32 | `halt_robot`/`arm_watchdog`——急停語意的唯一出處,四介面共用 |
| `tools/perception.py` | ~180 | **PerceptionLoop**:持續相機→VLM→結構化 `SceneAnalysis`(場景/物件/affordances/建議動作);TUI `/perception`、daemon `@perception` 規則共用;只觀察不動作 |
| `mcp_server/server.py` | 183 | FastMCP stdio server:唯讀工具 + stop;`--allow-actions` 才有 navigate_to(單飛鎖) |
| `agent/orchestrator.py` 等 | ~600 | /run 代理:規劃、specialist 工具、批准中斷、guardrails、tracing |
| `providers/chat.py` | 337 | OpenAI 相容呼叫:`ask_provider`、**`stream_provider`(串流)**、`ask_json`、`ask_vision_json`、`list_provider_models`;`_provider_errors` 共用例外映射;`parse_json_reply`(寬容解析,thinking 模型必備) |
| `adapters/ros2_adapter.py` | 304 | `ros2` CLI subprocess 包裝(有 timeout、錯誤分類) |
| `adapters/locations.py` | ~200 | locations.toml 載入/儲存/模糊搜尋;`load_locations_tolerant`(全介面共用的容錯載入) |
| `adapters/route_adapter.py` | 91 | RouteAdapter 協定:`stub`(誠實拒絕)/`nav2`(CLI send_goal,bridge 不可用時的後備) |
| `daemon/engine.py` | 165 | 規則引擎純邏輯:條件、冷卻、安全 gating;動作 notify/goto/**halt**(可單測) |
| `daemon/runner.py` | 115 | bridge watch → queue → engine → (獲准才)navigate_live;halt 決策優先搶佔 |
| `webui/server.py` | 352 | http.server:`/api/status` `/api/command` `/api/confirm` `/api/map` **`/api/stop`**;PoseCache(退避重試) |
| `webui/render.py` | 493 | 純渲染:儀表板 HTML/CSS/JS(含 SVG 地圖、紅色 STOP 鈕) |
| `webui/commands.py` | 303 | Web 版指令執行 + confirm 動作封存 |
| `config/models.py` | ~90 | AppConfig + **VehicleProfile(`[vehicle]`:cmd_vel/限速/相機)** |
| `config/store.py` | ~210 | config/.env 載入(`JENAI_CONFIG`/XDG/APPDATA;shell 優先於 .env) |
| `doctor/checks.py` | 338 | 健檢:python/uv/venv/config/env_file/ros2/provider/locations/webui |
| `schemas/` | ~500 | 全部 pydantic 模型(`extra="forbid"`):Location、RouteOutput、DoctorResult… |

### 4.3 關鍵設計決策(為什麼長這樣)

**rclpy bridge 是獨立程序,不是 import。** venv(uv 管理的 Python)看不到 rclpy,ROS 的 PYTHONPATH 又會遮蔽 venv 依賴(pytest 都得 `env -u PYTHONPATH` 跑)。解法:`bridge/ros_bridge.py` 由 `/usr/bin/python3` 跑(source ROS 後 exec),與 venv 完全隔離,講 newline-delimited JSON。venv 側 `RosBridgeClient` 用 asyncio 管 request/response(以 id 配對 future)和事件(nav_feedback、watch)。CLI 做不到的即時回饋、取消、影像抓取全靠它。**bridge 檔案內絕不能 import jenai**(它跑在 venv 外)。

**誠實回報原則。** 每條執行路徑必須能回 `unavailable` 並說明原因(「goal 沒有送出」),UI 用 warn 而非 success 呈現。寫新工具時遵守:不確定就說不確定。

**批准模型。** 動作類指令建立 run + ApprovalCard;批准後的執行包成可取消的 active task(長導航要能 Esc)。WebUI 的 confirm 是伺服器端一次性 token —— 瀏覽器只拿到 id,動作本體不出伺服器。daemon 則是「規則明確 `auto_approve` 才動」。三個介面,同一哲學:**沒有明確授權,機器人不動**。

**Provider 全走 OpenAI 相容端點。** 一個抽象吃遍 NVIDIA NIM / Ollama / 其他;`parse_json_reply` 寬容處理 thinking 模型的 ```json 圍欄與前後綴文字 —— 所有結構化輸出(vision、route 解析)都經過它。

**佔位符防呆。** 指令面板補完的 `<name|number>` 模板原文送出會被擋(曾把字面佔位符存成 model binding 弄壞 config)。

**安全鏈是分層的(v0.7+)。** 依時間尺度由快到慢:①bridge watchdog(client 斷線/卡死 → 自主停車,不依賴任何上層)→ ②急停(四介面一鍵,免批准可搶佔,語意統一在 `tools/safety.py`)→ ③執行期硬限速(`[vehicle]`,LLM 給再大也夾住)→ ④HITL 批准卡(意圖層)→ ⑤daemon 明確授權。**LLM 永不進即時迴路**;載具差異只准活在 `[vehicle]`。

**調度只寫一次。** 「nav2 可用走 live bridge、否則誠實 CLI」的決策活在 `navigate_with_fallback`(nav_live.py)一處,TUI/MCP/技能共用——改安全政策改一個地方,所有介面同時生效。同理:急停 = `safety.py`,相機→VLM = `capture_and_analyze`,locations 容錯載入 = `load_locations_tolerant`。

### 4.4 測試與 CI

```bash
env -u PYTHONPATH uv run pytest     # 必須 unset PYTHONPATH(ROS 遮蔽問題)
env -u PYTHONPATH uv run ruff check src tests
```

- **CI**(`.github/workflows/ci.yml`):ubuntu-latest、無 ROS —— 測試設計成不依賴 ROS(bridge 用 `tests/unit/fake_bridge.py` 這個純 stdlib 假程序講同一套協定)。兩個 job:`test`(ruff + pytest,coverage 寫入 job summary)、`build`(`uv build` + `uvx` 全新環境裝 wheel 跑 `jenai --help`,抓漏列的依賴)
- **Release**(`.github/workflows/release.yml`):推 `vX.Y.Z` tag 觸發 —— 驗 tag 與 pyproject 版本一致、重跑 lint+測試、`uv build` + wheel 冒煙測試,建**草稿** release(自動產生的 notes + wheel/sdist 附件);手寫 notes 後 `gh release edit vX.Y.Z --notes-file … --draft=false` 發佈。tag 已有 release 時只補上傳附件
- **TUI 測試**:Textual `app.run_test()` + `handle_user_text()`;monkeypatch 目標在 handler 所在模組(如 `jenai.tui.robot_commands.route_execute`)
- **本機 E2E 手法**(開發時驗真鏈路):`scratchpad` 裡跑假節點 —— fake Nav2 action server、fake camera publisher、fake battery —— 全是真 rclpy、真協定,TUI/daemon 分不出真假。參考 git log 中各功能 commit 的驗證描述

### 4.5 擴充指南(常見四件事)

**加一個 slash 指令**:`tui/app.py` 的 `SLASH_COMMANDS` 加 `SlashCommand("/foo", "說明", "/foo <arg>")` → `_resolve_command_handler` 的 handlers dict 加映射 → 在對應 mixin(info/robot)寫 `async def _show_foo(self, arg)` → `help_content.py` 補條目 → 測試用 `app.handle_user_text("/foo x")`。

**加一個 bridge 能力**:`ros_bridge.py` 的 `BridgeNode` 加方法 + `_handle()` 加 op 分支(記住:只能用系統 Python 有的套件)→ `client.py` 加 typed helper → `fake_bridge.py` 補假回應 → 測試。

**加一個 daemon 條件/動作**:條件在 `engine.py` 的 `condition_met`;動作在 `Rule._check` 白名單 + `RuleEngine.handle_event` 的 gating + `runner.py` 執行。**預設必須是不動作**。

**加一個 provider**:通常不用寫程式 —— OpenAI 相容端點只要新 profile(`/provider` 切換)。特殊行為(如 NVIDIA 別名)加在 `providers/chat.py` 的 `resolve_model_alias`。

## 5. 後續建議(接下來值得做)

Roadmap 的正式版在 [PROJECT_DIRECTION.md](PROJECT_DIRECTION.md)(必做 M1–M5、可做、考慮做);已完成與剩餘:

1. ~~MCP server 化~~(v0.6)、~~急停 + watchdog(M1)~~、~~vehicle profile(M2)~~、~~任務技能 patrol/dock(M4)~~(以上 v0.7 系列完成)
2. **M5 onboarding**:doctor 擴充(map server/AMCL/Nav2 lifecycle 檢查)+ 建圖到首航的手把手文件
3. **M3 Twin Gate**:第二個 bridge 以 `ROS_DOMAIN_ID` 隔離指向 Isaac Sim 孿生;goal 先在孿生跑,G1–G5 判準過閘才碰實體(掛載點:`navigate_with_fallback`)
4. **WebUI SSE**:把 5s/2s 輪詢換成 Server-Sent Events;地圖點擊下 goal(走既有 confirm 流程)
5. **多機器人**:namespace 切換(`/robot <ns>`),M3 的第二 bridge 就是地基
6. **語音**:Jetson 上 Whisper(STT)+ Piper(TTS),入口掛在 TUI 輸入層
7. **巡邏報告**:patrol 的每點觀察已存在,補「彙整成日報」(LLM 摘要 + 存檔)
8. **Nav2 進階**:waypoint following(`FollowWaypoints` action)取代逐點 send

---
*其他文件:[PROJECT_DIRECTION.md](PROJECT_DIRECTION.md)(方向與 roadmap)、[COMMANDS.md](COMMANDS.md)(指令規格)。[ARCHITECTURE.md](ARCHITECTURE.md)、[FEATURES.md](FEATURES.md)、[UX.md](UX.md)、[DATA_SCHEMAS.md](DATA_SCHEMAS.md)、[STATE_MACHINE.md](STATE_MACHINE.md)、[MOSCOW.md](MOSCOW.md) 為 v0.1 設計期文件,細節以本指南與程式碼為準。*
