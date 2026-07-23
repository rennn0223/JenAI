# JenAI 技術指南(從零到有)

> 給新加入的工程師:這份文件讓你在一台新機器上把 JenAI 建起來、理解每個模組在做什麼、知道怎麼擴充。讀完你應該能獨立開發。
> 對應版本:v2.2.0(2026-07)。專案方向見 [PROJECT_DIRECTION.md](product/PROJECT_DIRECTION.md),前瞻主圖見 [ROADMAP.md](product/ROADMAP.md);逐檔導讀見 [CODE_TOUR.md](CODE_TOUR.md)。

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
- **權限三模式**(v0.22):Shift+Tab 循環「審批/規劃/自動」;裸自然語言依模式路由(規劃只想不動、自動免批准卡),急停/硬限速永不放鬆
- **Development copilot**(v0.19+):`JenAI scaffold` 自然語言生成 ROS2 套件(`--build` 生成即 colcon 驗證);`skills/*.toml` 檔案定義技能
- **決策腦 + 評測**(v0.21):`decision_core` 有界動作單選決策、`JenAI eval` E1 場景評測(論文工具鏈)

## 2. 從零建置

### 2.1 前置環境

| 需求 | 說明 |
|---|---|
| Linux(aarch64 或 x86_64) | 開發機是 DGX Spark(Ubuntu 24.04);macOS 可跑無 ROS 模式 |
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

# 5) 一鍵啟動器（自動 source ROS2 Jazzy 與 Isaac ROS jazzy_ws）
ln -sf "$PWD/scripts/jenai" ~/.local/bin/jenai

# 6) 驗收
jenai doctor        # 每一項應為 pass/warn,不該有意外的 fail
```

`~/.config/jenai/` 下的檔案:`config.toml`(provider/model/route_adapter)、`.env`(金鑰)、`locations.toml`(地點)、`rules.toml`(daemon 規則,選配)、`sessions/`(對話狀態)。路徑解析尊重 `JENAI_CONFIG`、`XDG_CONFIG_HOME`、`APPDATA`。

### 2.3 設定檔重點

```toml
# ~/.config/jenai/config.toml
active_provider = "local"          # /provider 可即時切換
route_adapter = "nav2"             # "stub"（不動載具）| "nav2"（產品導航）；"odom" 僅供 bridge bring-up，產品任務會拒絕
deployment_mode = "simulation"     # 接實體車前必須改為 "physical"；會強制 Twin ROS domain 隔離
locations_path = "locations.toml"  # 相對於 config.toml 所在目錄

[provider_profiles."local"]
provider = "ollama"
base_url = "http://localhost:11434/v1"
api_key_env = ""                   # Ollama 免金鑰

[model_bindings]                   # /model 可即時切換
chat = "qwen3.6:35b"
vision = "qwen3.6:35b"             # 要挑有 vision capability 的模型;pin 明確 tag(latest 會漂)
# plan / route / default …

[vehicle]                          # 載具差異唯一的家(v0.7+)
type = "ackermann"                 # ackermann | diff | quadruped
cmd_vel_topic = "/cmd_vel"
cmd_vel_stamped = false            # true 時發 TwistStamped
camera_topic = "/camera/image_raw" # /vision camera、patrol photo、MCP camera_look 預設
max_linear = 1.0                   # 執行期硬限速(m/s)——LLM/使用者給再大都會被夾住
max_angular = 2.0                  # rad/s;安全預設,依實車再調(Leatherback:2.0/0.53)
arrival_position_tolerance_m = 0.25 # Nav2 terminal pose 的 JenAI 二次核對上限
arrival_yaw_tolerance_rad = 0.25    # Isaac 精準 profile:0.05 m / 0.15 rad
odom_timeout_s = 1.0               # odom 逾時立即歸零；不沿用舊位姿繼續直驅
```

NavigationGateway 不把 action status 當成幾何證據。bridge 會把 Nav2 feedback 的最後
`current_pose` 放入 terminal event，`navigate_live` 再計算平面距離與 wrap-around yaw
誤差；超過 Vehicle Profile 上限、frame 不一致、位姿缺失或非有限值都 fail closed 並
送 halt。這讓「Nav2 的成功容差」與「產品對外宣告的到點精度」成為兩個明確邊界。

## 3. 日常使用

### 3.1 TUI(`jenai` 或 `uv run JenAI`)

| 類別 | 指令 | 說明 |
|---|---|---|
| **安全** | **`/stop`** | **緊急停止:取消導航 + 零速度;免批准,任務執行中也能搶佔** |
| 對話 | 直接打字 | 裸自然語言依權限模式路由(見下);寒暄走免工具串流聊天;`!<cmd>` 跑 shell |
| 任務 | `/plan <任務>`、`/run <任務>`、`/why`、`/review`、`/abort` | 代理規劃/執行(工具呼叫要批准) |
| ROS 檢查 | `/ros topics`、`/ros topic-info <t>`、`/ros schema <t>`、`/ros echo <t>` | 唯讀,不需批准 |
| 動作 | `/ros pub …`、`/ros drive …`、`/drive 前進兩秒` | 需批准;速度過 `[vehicle]` 硬限速 |
| 技能 | `/mission kitchen, lobby`、**`/patrol A, B x3 photo`**、**`/explore 5m goals=8`**、**`/dock`** | 需批准;explore 僅在合格已知點位間低重複率巡遊，具時間/目標/失敗界線；dock 找 `tags=["dock"]` 的地點 |
| 導航 | `/route from A to B` | 需批准;Nav2 模式下**即時顯示剩餘距離,Esc 真的取消 goal** |
| 地點 | `/loc list`、`/loc show <名>`、**`/loc add here <名>`** | add here 抓當下機器人位置存檔 |
| 視覺 | `/vision image <路徑>`、**`/vision camera [topic]`** | camera 預設讀 `vehicle.camera_topic` |
| 感知 | `/perception start|stop|status` | 相機→VLM 定頻結構化分析;只觀察不動作 |
| 報告 | `/report`、`/report task [list]`、`/report event` | 巡邏日報；每次 TUI run 的結構化任務收據；daemon 事件→動作結果（後兩者不呼叫 LLM） |
| 技能檔 | `/skills`(+ `skills/*.toml` 定義的自訂指令) | 檔案定義技能,走同一張批准卡;保留字拒載 |
| Shell | `/shell <cmd>`(或 `!<cmd>`) | 需批准;風險評估進批准卡 |
| 模型 | `/model`(列出+編號切換)、`/models`、`/provider <名>`、`/providers` | 即時生效並持久化 |
| 系統 | `/status`、`/doctor`、`/config`、`/permissions`、`/help`、`/clear`、`/quit` | |

**權限模式(v0.22)**:**Shift+Tab** 循環切換「審批(預設)/規劃/自動」。模式路由的是**裸自然語言**——打一句話就會做事(規劃模式只產計畫不執行;審批/自動模式交給 run agent);自動模式下批准卡一律自動批准(時間軸以 warn 明示「自動模式:已批准」)。slash 指令不受模式改道;急停與硬限速在任何模式都不放鬆。自然語言路由包例外網(v0.22.1):provider 錯誤/模型輸出不合規以乾淨訊息呈現,不會變成未處理例外。終端吃不到 Shift+Tab 時(SSH client/內嵌終端常見)用 **`/mode`** 備援:不帶參數循環、帶參數直接指定(en/zh 別名皆可)。

### 3.2 WebUI(`jenai web`,預設 127.0.0.1:8760)

**多頁式**(v0.15+):Console(chat + slash + **slash 指令選擇表**(輸入 `/` 彈出,↑↓ 選、Tab/點擊補完,清單與實作同源)+ **確認按鈕** + Map)、**Camera**(`/api/frame` 每秒抓一幀 RGB + 旁邊小格 odometry 即時更新;只在該頁時輪詢,不浪費 bridge)、Status(5 秒自動更新)、**API**(ORDS 風格端點目錄:GET/POST 徽章 + 路徑 + 說明)。StatusCache 讓所有瀏覽器共用 doctor 30 秒與 ROS graph 2 秒快照,避免每個分頁重跑 subprocess。動作類指令一律回 confirm token,由伺服器端一次性持有且 120 秒到期;STOP 會撤銷全部舊確認 —— 瀏覽器無法偽造或在急停後重放。

**Token 認證**(v0.10+):啟動時自動生成 token 並印出帶 `?token=…` 的網址(`--token` 可固定);Bearer header、cookie、query 三種攜帶方式,首次 query 驗證通過即種 session cookie。唯一免認證端點 **`/api/stop`** —— 停車永遠安全(見 docs/validation/THREAT_MODEL.md)。

**遠端存取**(v0.23.6):啟動輸出直接列印所有開法——本機網址、SSH 轉發一行指令(`ssh -L 8760:127.0.0.1:8760 …` 後開同一網址;可寫進 `~/.ssh/config` 的 `LocalForward` 一勞永逸)、以及 `--host 0.0.0.0` 時各介面的區網網址(誠實原則:綁 loopback 時**不**印打不開的區網網址)。公共/不可信網路建議走 SSH 轉發;實驗室網段 `--host 0.0.0.0` + token 即可。

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
觸發、動作開始與終端結果會寫入本機 audit（不保存原始 topic payload），可在 TUI 用 `/report event` 查看；這使 notify、被 gate 阻擋、busy、導航成功／失敗與 halt 結果都有終端狀態。

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

> 行數為 2026-07-18 工作樹快照,僅供量級參考——以 `wc -l` 實測為準。

| 模組 | 行數 | 職責 |
|---|---|---|
| `cli/main.py` | 668 | Typer 進入點:TUI(預設)、`doctor`、`web`、`mcp`、`daemon`、`loc`、`route`、**`scaffold`**、**`eval`**、**`onboard`**(重跑設定精靈,自動備份)、`help`、`version`;callback 統一載入 `.env`;診斷一律走 `err_console`(stderr,保護 MCP stdout) |
| `tui/app.py` | 1,311 | App 殼:輸入分發、**權限三模式(Shift+Tab)與裸自然語言路由**、串流、spinner、Esc 中斷、`/stop` 搶佔與共用 UI 生命週期 |
| `tui/approval_flow.py` | 159 | 批准決策、remember、agent/direct interruption 恢復；與實際工具執行分離 |
| `tui/direct_execution.py` | 250 | 已批准 direct action 的 handler map、audit/run 收尾與結果渲染；不以巨型 if/elif 分派 |
| `tui/catalog.py` | 403 | Slash palette、help 分組的單一資料來源、standalone greeting 判斷與 Claude Code 風格 responsive CSS |
| `tui/robot_commands.py` | 940 | Mixin:`/stop` `/ros` `/route` `/mission` `/patrol` `/dock` `/drive` `/vision` + bridge 生命週期(含 watchdog 佈署) |
| `tui/location_commands.py` | 303 | Mixin:`/loc` persistence、AMCL/odom pose 與 GPS→map 儲存；不執行導航 |
| `tui/info_commands.py` | 341 | Mixin:`/help` `/status` `/doctor` `/model` `/provider` 等資訊類 |
| `tui/panels.py` | 442 | 純視覺:雙欄 WelcomePanel、TimelineItem、OutputPanel、CommandPalette |
| `tui/widgets/` | ~200 | ApprovalCard(依風險顯示 2/3 個選項；HOST_COMMAND/P2 不可 remember；P2、host command 與 robot control 預選 No)、Plan/Tool/Error blocks |
| `bridge/ros_bridge.py` | 999 | **系統 Python** 下的 rclpy I/O 節點:pose、nav_send/feedback/cancel、**halt(急停)**、**watchdog(斷線自主停車)**、capture_frame、watch；控制演算法與協定迴圈已拆至 stdlib-only 模組 |
| `bridge/_protocol.py` | — | stdlib-only JSON op dispatcher；request defaults 與精確型別／finite／範圍驗證可不靠 rclpy 單測，無效請求不會進入 ROS node |
| `bridge/_wire.py` / `_server.py` | — | newline-JSON frame 驗證與 stdin server；畸形 frame、callback 失敗與慢速唯讀操作皆有隔離測試 |
| `bridge/_drive_control.py` | — | 純狀態機的 odom→cmd_vel 控制、深度 freshness、detour/replan；ROS I/O 只套用輸出 |
| `bridge/client.py` | — | venv 側非同步 client:安全 argv spawn、request future 清理、reader 故障傳播、事件隔離；致動／watchdog 輸入與 pose/map/plan/cancel/halt 回應皆採嚴格 wire 驗證，拒絕 truthiness、非 finite 值、無效範圍與矛盾安全證據 |
| `tools/*_core.py` | — | 各能力純邏輯(可單測):route 解析與執行、mission 步進、drive 解析、vision、shell 風險評估 |
| `tools/nav_live.py` | 313 | bridge 版導航:回饋串流、逾時、取消、心跳餵 watchdog;**`navigate_with_fallback` 只接受受監督 bridge，bridge/watchdog 不可用即 fail-closed，絕不降級成 CLI goal** |
| `tools/skills.py` | 145 | 任務技能:`parse_patrol`/`run_patrol`(循環+觀察+失敗續行)、`find_dock` |
| `tools/ros2_pkg_core.py` | 286 | **自然語言 → ROS2 套件**(`JenAI scaffold`):`render_package` 純確定性 boilerplate(可單測、永遠 build)+ LLM 寫 node 主體;name/dep 驗證、拒絕覆蓋;`--build` 生成即 colcon 驗證(失敗餵錯誤回 LLM 修一輪)。從 control agent 邁向 development copilot |
| `tools/decision_core.py` | 104 | **M6 決策腦**(v0.21):`ContextSnapshot`(六欄位情境快照)→ 單次 `ask_json` 於封閉動作集單選 `Decision`;越界動作/幻覺目的地/解析失敗一律降級 refer_to_human,無自由文字可達致動 |
| `tools/decision_eval.py` | 133 | **`JenAI eval`**(E1 評測):scenarios.toml 場景庫 → per-family accuracy / unsafe rate / refer rate;標註 `action:target` 綁定目標、gold 優先於 unsafe、未知動作名 fail-loud(論文工具鏈) |
| `tools/user_skills.py` | 85 | **檔案定義技能**(v0.20):`skills/*.toml` → 新 slash 指令;與內建指令同一張批准卡;保留字拒載 |
| `tools/safety.py` | 32 | `halt_robot`/`arm_watchdog`——急停語意的唯一出處,四介面共用 |
| `tools/navigation_gateway.py` | 138 | **NavigationGateway(v0.25)**:所有導航的唯一出口——CLI/TUI/WebUI/MCP/daemon/任務/agent 工具全部經此;Twin Gate 與 watchdog 政策無法被直呼 route 執行繞過 |
| `twin/gate.py` | 302 | **Twin Gate**:G1 碰撞/G2 逾時/G3 禁區/G4 終點偏差/G5 規劃失敗 → pass/block/refer;非有限 pose 不算有效樣本、缺孿生遙測回 refer(fail-closed) |
| `state/`(runs/audit/reports/task_receipts/history/session) | — | run 記錄、SQLite audit（run/批准/工具/Gate/daemon event）、每個終止 TUI run 的原子 JSON task receipt（耗時、工具、批准、結果、標準失敗代碼）、巡邏報告與輸入歷史。audit 不落盤 prompt/raw payload；task receipt 是本機操作報告，依 data lifecycle 管理 |
| `config/setup.py` | 223 | **Setup Wizard**:橘色主題三步設定;誤貼金鑰自動安全搬遷至 `.env`(0600) |
| `tools/perception.py` | ~180 | **PerceptionLoop**:持續相機→VLM→結構化 `SceneAnalysis`(場景/物件/affordances/建議動作);TUI `/perception`、daemon `@perception` 規則共用;只觀察不動作 |
| `mcp_server/server.py` | 208 | FastMCP stdio server:共享資源生命週期與 ROS／狀態／導航註冊器分離；唯讀工具 + stop;`--allow-actions` 才有 navigate_to(單飛鎖) |
| `agent/orchestrator.py` 等 | ~700 | /run 代理:規劃、specialist 工具、批准中斷、guardrails、tracing；純唯讀 ROS 狀態要求走共用工具的確定性快速路徑，混合／動作要求保留完整 LLM 與批准流程；session 有項目與 UTF-8 位元組上限 |
| `providers/chat.py` | 337 | OpenAI 相容呼叫:`ask_provider`、**`stream_provider`(串流)**、`ask_json`、`ask_vision_json`、`list_provider_models`;`_provider_errors` 共用例外映射;`parse_json_reply`(寬容解析,thinking 模型必備) |
| `adapters/ros2_adapter.py` | 304 | `ros2` CLI subprocess 包裝(有 timeout、錯誤分類) |
| `adapters/locations.py` | ~200 | locations.toml 載入/儲存/模糊搜尋;`load_locations_tolerant`(全介面共用的容錯載入) |
| `adapters/route_adapter.py` | 91 | 舊 RouteAdapter 相容層與 stub；產品導航入口不得直接呼叫。`nav2` 的正式路徑只走 `NavigationGateway` + watchdog bridge；`odom` 只保留為 legacy bring-up 介面，gateway 會拒絕高階任務，失效時不送 goal |
| `bridge` `drive_to_pose` | — | **(deprecated,bring-up fallback)無 Nav2 的點對點直驅**:閉環 /odom → /cmd_vel(目標視為 odom 座標,map≈odom 時成立);餵同一套 nav_feedback/nav_result,navigate_live 無縫共用 |
| `bridge/_avoidance.py` + drive_loop | — | **(deprecated,maintenance mode)局部避障**:Isaac 實測單 depth 反應式避障不可行——只修 bug 不加能力,終局=對接載具原生 nav。原敘述::depth camera(32FC1)→ 偽雷射 → 目標方向走廊判定 → stop-and-go detour。反射層不經 LLM;深度畫面逾時立即歸零並回報 `sensor_unavailable`,不使用陳舊影像繼續移動。**局部反應,非全域規劃**——複雜地圖仍需 Nav2 |
| `daemon/engine.py` | 165 | 規則引擎純邏輯:條件、冷卻、安全 gating;動作 notify/goto/**halt**(可單測) |
| `daemon/runner.py` | 307 | bridge watch → queue → engine → (獲准才)NavigationGateway；perception、decision audit、navigation worker 與 halt 各自封裝，halt 決策優先搶佔 |
| `webui/server.py` | 717 | http.server:`/api/status` `/api/command` `/api/confirm` `/api/map` **`/api/stop`**;PoseCache(退避重試) |
| `webui/render.py` | 699 | 純渲染:儀表板 HTML/CSS/JS(含 SVG 地圖、紅色 STOP 鈕) |
| `webui/commands.py` | 303 | Web 版指令執行 + confirm 動作封存 |
| `config/models.py` | 220 | AppConfig + **VehicleProfile(`[vehicle]`:cmd_vel/限速/相機)** |
| `config/store.py` | ~210 | config/.env 載入(`JENAI_CONFIG`/XDG/APPDATA;shell 優先於 .env) |
| `doctor/checks.py` | 519 | 健檢:python/uv/venv/config/env_file/ros2/provider/locations/webui |
| `schemas/` | ~500 | 全部 pydantic 模型(`extra="forbid"`):Location、RouteOutput、DoctorResult… |

### 4.3 關鍵設計決策(為什麼長這樣)

**rclpy bridge 是獨立程序,不是 import。** venv(uv 管理的 Python)看不到 rclpy,ROS 的 PYTHONPATH 又會遮蔽 venv 依賴(pytest 都得 `env -u PYTHONPATH` 跑)。解法:`bridge/ros_bridge.py` 由 `/usr/bin/python3` 跑(source ROS 後 exec),與 venv 完全隔離,講 newline-delimited JSON。venv 側 `RosBridgeClient` 用 asyncio 管 request/response(以 id 配對 future)和事件(nav_feedback、watch)。CLI 做不到的即時回饋、取消、影像抓取全靠它。**bridge 檔案內絕不能 import jenai**(它跑在 venv 外)。

**誠實回報原則。** 每條執行路徑必須能回 `unavailable` 並說明原因(「goal 沒有送出」),UI 用 warn 而非 success 呈現。寫新工具時遵守:不確定就說不確定。

**批准模型。** 動作類指令建立 run + ApprovalCard;批准後的執行包成可取消的 active task(長導航要能 Esc)。任意 `/shell`／`!` 一律是 P2、逐次批准且預選 No；不以關鍵字猜測「唯讀 shell」，避免 Python、子 shell、管線或重新導向繞過。WebUI 的 confirm 是伺服器端一次性 token —— 瀏覽器只拿到 id,動作本體不出伺服器。daemon 則是「規則明確 `auto_approve` 才動」。三個介面,同一哲學:**沒有明確授權,機器人不動**。

**Provider 依能力分流。** 官方 OpenAI agent 路徑使用 Responses API;NVIDIA NIM、Ollama 與自訂 `base_url` 使用 Chat Completions 相容層。兩者仍共用同一個 OpenAI SDK client/config resolver;`parse_json_reply` 寬容處理 thinking 模型的 ```json 圍欄與前後綴文字。

**佔位符防呆。** 指令面板補完的 `<name|number>` 模板原文送出會被擋(曾把字面佔位符存成 model binding 弄壞 config)。

**安全鏈是分層的(v0.7+)。** 依時間尺度由快到慢:①bridge watchdog(client 斷線/卡死 → 自主停車,不依賴任何上層)→ ②急停(四介面一鍵,免批准可搶佔,語意統一在 `tools/safety.py`)→ ③執行期硬限速(`[vehicle]`,LLM 給再大也夾住)→ ④HITL 批准卡(意圖層)→ ⑤daemon 明確授權。**LLM 永不進即時迴路**;載具差異只准活在 `[vehicle]`。

**調度只寫一次。** 所有表面先進 `NavigationGateway`,再由 `navigate_with_fallback` 套用 Twin Gate、watchdog 與 live bridge；Bridge 啟動、協定、wire request/response 驗證或 watchdog 任一失敗即拒絕 goal，不存在無監督 CLI 後備。client 與 system-Python sidecar 都驗證致動參數，避免直接 JSON 呼叫繞過上層設定模型。人工 HIL 任一 motion check 失敗後也會停止後續 goal，並把 final halt／bridge shutdown 寫入 artifact。架構測試禁止其他模組直呼 `route_execute`。同理:急停 = `safety.py`,相機→VLM = `capture_and_analyze`,locations 容錯載入 = `load_locations_tolerant`。

### 4.4 測試與 CI

```bash
uv lock --check
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy                         # 全部 src/jenai production code 採 strict mode
uv run pytest --cov=jenai --cov-branch
```

- **目前工作樹基準**:全專案 branch coverage 77%（門檻 76%）、安全鏈 branch coverage 94%（門檻 90%）；Ruff format/lint 與全部 `src/jenai` production code 的 mypy strict 均通過。`ros_bridge.py` 的 rclpy 接線不假裝由無 ROS 的 hosted CI 覆蓋，另由人工 self-hosted Isaac HIL 驗收。精確測試數以當次 CI summary 為準；發布證據仍以該 tag 的 GitHub Actions run 為準，不能用本機結果冒充 release 證明。
- **真實 TUI 驗收**:[TUI_LIVE_ACCEPTANCE_2026-07-17.md](validation/TUI_LIVE_ACCEPTANCE_2026-07-17.md) 記錄 Isaac Sim/Nav2 的 ROS introspection、有限時致動、stop、vision/perception、patrol、Slash/NL explore 與四角 inspect。該紀錄是描述性系統驗收，不代替實體安全或使用者效率實驗。
- **CI**(`.github/workflows/ci.yml`):ubuntu-latest、無 ROS —— 測試設計成不依賴 ROS(bridge 用 `tests/unit/fake_bridge.py` 這個純 stdlib 假程序講同一套協定)。兩個 job:`test` 以 Python 3.12／3.13／3.14 matrix 跑 Ruff format/lint、mypy strict 與 pytest branch coverage（整體 `fail-under=76`、安全鏈 `fail-under=90`）、`build`(`uv build` + 全新 tool 環境裝 wheel 跑 lifecycle smoke,抓漏列的依賴)。架構鐵律由 `tests/unit/test_architecture.py` 進 CI 防護：所有導航必經 gateway，且任何函式超過 120 行都會失敗，目前無白名單。直接依賴同時有最低版與下一個未審核主版本上限，release 仍另輸出精確 constraints。
- **Release**(`.github/workflows/release.yml`):兩個入口共用 tag／pyproject 一致、完整 lint／測試／安全 coverage、dependency audit、重現性 build、敏感檔掃描、constraints、SBOM、checksum 與隔離 wheel lifecycle 閘；public repository 另強制 build provenance 與 SBOM attestations。推 `vX.Y.Z` tag 只建立草稿；`workflow_dispatch` 建新 tag 時只接受 fetched `origin/main` 的精確 commit，並以 `docs/releases/<tag>.md` 發布。既有 tag recovery 必須從同一 tag ref 觸發，且只可覆寫尚未發布的 draft；published release 永不可變更，必須升版。
- **TUI 測試**:Textual `app.run_test()` + `handle_user_text()`;導航測試 patch `NavigationGateway.execute`,以安全入口為邊界
- **本機 E2E 手法**(開發時驗真鏈路):`scratchpad` 裡跑假節點 —— fake Nav2 action server、fake camera publisher、fake battery —— 全是真 rclpy、真協定,TUI/daemon 分不出真假。參考 git log 中各功能 commit 的驗證描述

### 4.5 擴充指南(常見四件事)

**加一個 slash 指令**:`tui/app.py` 的 `SLASH_COMMANDS` 加 `SlashCommand("/foo", "說明", "/foo <arg>")` → `_resolve_command_handler` 的 handlers dict 加映射 → 在對應 mixin(info/robot)寫 `async def _show_foo(self, arg)` → `help_content.py` 補條目 → 測試用 `app.handle_user_text("/foo x")`。

**加一個 bridge 能力**:`ros_bridge.py` 的 `BridgeNode` 加方法 + `_protocol.dispatch_request()` 加 op 分支(記住:只能用系統 Python 有的套件)→ `client.py` 加 typed helper → `fake_bridge.py` 補假回應 → protocol/client 測試。

**加一個 daemon 條件/動作**:條件在 `engine.py` 的 `condition_met`;動作在 `Rule._check` 白名單 + `RuleEngine.handle_event` 的 gating + `runner.py` 執行。**預設必須是不動作**。

**加一個 provider**:通常不用寫程式 —— OpenAI 相容端點只要新 profile(`/provider` 切換)。特殊行為(如 NVIDIA 別名)加在 `providers/chat.py` 的 `resolve_model_alias`。

## 5. 後續建議(接下來值得做)

Roadmap 的正式版在 [PROJECT_DIRECTION.md](product/PROJECT_DIRECTION.md)(必做 M1–M5、可做、考慮做);已完成與剩餘:

1. ~~MCP server 化~~(v0.6)、~~急停 + watchdog(M1)~~、~~vehicle profile(M2)~~、~~任務技能 patrol/dock(M4)~~(以上 v0.7 系列完成)
2. ~~M5 onboarding~~ ✅([ONBOARDING.md](ONBOARDING.md):裸 ROS2 → 建圖 → 定位 → Nav2 → 首航;doctor nav 檢查即進度條)
3. ~~M3 Twin Gate~~ ✅(v0.9,見 [TWIN_SETUP.md](operations/TWIN_SETUP.md):G1–G5 判準、pass/block/refer、獨立 ROS_DOMAIN_ID;剩 Isaac 場景 = 客戶 B5)
4. ~~巡邏報告~~ ✅(v0.12 `/report`:確定性日報 + LLM 摘要,離線誠實降級)
5. **WebUI SSE**:把 5s/2s 輪詢換成 Server-Sent Events;地圖點擊下 goal(走既有 confirm 流程)
6. **多機器人**:namespace 切換(`/robot <ns>`),Twin 的第二 bridge 就是地基(ROADMAP 軌道 4)
7. **語音**:DGX Spark 上 Whisper(STT)+ Piper(TTS),入口掛在 TUI 輸入層(ROADMAP 軌道 5)
8. **Nav2 進階**:waypoint following(`FollowWaypoints` action)取代逐點 send;depth→costmap(ROADMAP 軌道 2)
9. **M6 常駐迴圈**(post-v2／候選 v3 研究方向):決策腦與 eval 已備(`decision_core`/`decision_eval`),但 perceive→decide→rehearse→act 尚未接成常駐迴圈；v2.0 不宣稱已完成(ROADMAP 軌道 1)

---
*其他現行文件：[PROJECT_DIRECTION](product/PROJECT_DIRECTION.md)（方向與 roadmap）、[COMMANDS](COMMANDS.md)（指令規格）、[UX](design/UX.md)（介面基準）。v0.1 規劃文件已集中至 [設計歸檔](archive/design/README.md)，現況以本指南與程式碼為準。*
