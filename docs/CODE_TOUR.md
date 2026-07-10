# CODE_TOUR — 全程式碼導讀(給補課用的逐檔解說)

> 對應 v0.23.x。每個檔案講三件事:**做什麼**、**用了什麼 SDK/庫**、
> **為什麼這樣寫**(關鍵段落的設計理由)。搭配各目錄 README 與
> [TECHNICAL_GUIDE](TECHNICAL_GUIDE.md) 服用;讀碼時對著原始檔開兩個視窗。

## 先背五個全局決定(每個檔案都繞著它們轉)

1. **venv/ROS 隔離**:uv 管的 venv 看不到 rclpy;ROS 的 PYTHONPATH 會遮蔽 venv 套件。
   → 所有 rclpy 程式碼住在獨立系統程序(`bridge/ros_bridge.py`),用 JSON/stdio 溝通。
2. **誠實回報**:每條路徑都能回 `unavailable`+原因;絕不假裝成功。
3. **調度只寫一次**:急停=`safety.py`、導航調度=`navigate_with_fallback`、
   相機→VLM=`capture_and_analyze` —— 四介面共用同一實作。
4. **LLM 永不進即時迴路**;載具字眼只准出現在 config(CI 用 `test_architecture.py` 強制)。
5. **純邏輯/包裝分離**:`*_core.py` 可單測純邏輯;`*_agent_tools.py` 只是包給 SDK 的殼。

## 使用的外部庫(一次認識)

| 庫 | 用在哪 | 角色 |
|---|---|---|
| **typer** | cli/main.py | CLI 框架(函數簽名→命令列參數,`Annotated` 定義選項) |
| **rich** | CLI/TUI 各處 | 終端排版(Table/Panel/Text/markup 色彩) |
| **textual** | tui/ | TUI 框架(Widget 樹 + CSS + 事件迴圈;rich 同作者) |
| **pydantic v2** | schemas/、config/ | 資料模型與驗證(`extra="forbid"` 擋打錯欄位) |
| **openai (AsyncOpenAI)** | providers/ | 所有 LLM 呼叫 —— 只當「OpenAI 相容 HTTP client」用,吃 NIM/Ollama |
| **openai-agents(`agents`)** | agent/ | /plan /run 的多代理 SDK(Agent/Runner/handoffs/guardrails) |
| **mcp (FastMCP)** | mcp_server/ | 把工具開成 MCP stdio server |
| **rclpy + ROS msg 套件** | bridge/ros_bridge.py **限定** | 唯一碰 ROS runtime 的檔案 |
| **numpy** | bridge/ros_bridge.py(系統端) | 深度影像→偽雷射的向量化運算 |
| 標準庫 asyncio/subprocess/tomllib/threading | 到處 | 非同步、外部程序、TOML 讀取、bridge 執行緒 |

---

# 第一層:橋接與載具(離鐵最近)

### `bridge/ros_bridge.py`(767 行)——【全 repo 最重要的檔案】
- **做什麼**:常駐 rclpy 節點。ops:`pose`、`nav_send`(Nav2 action)、
  `drive_to_pose`(無 Nav2 的 odom→cmd_vel 直驅+stop-and-go detour)、
  `nav_cancel`、`halt`(急停)、`watchdog`、`capture_frame`、`watch`。
- **SDK**:rclpy(Node/ActionClient/QoS)、nav2_msgs、geometry_msgs、numpy。
- **為什麼這樣寫**:
  - 它由 `/usr/bin/python3` 執行(source ROS 後),**絕不 import jenai** ——
    venv 在它眼裡不存在。跟主程式講 newline-delimited JSON(`_emit()` 帶鎖,
    因為多執行緒同時寫 stdout 會把 JSON 行撕碎)。
  - `nav_send` 的 **tag 機制**:每個 goal 帶隨機 tag,feedback/result 事件回帶。
    沒有它,上一個被取消 goal 的遲到 result 會被當成下一個 goal 的結果(真踩過)。
  - `_nav_pending`/`_cancel_on_accept`:goal 送出到 server 接受之間有個空窗,
    這期間來的急停必須「接受當下立刻取消」,否則急停會漏掉這個 goal。
  - `nav_cancel` 一定呼叫 **server 端 cancel-all**(零 goal id):別的程序(WebUI)
    送的 goal 這個 bridge 看不見,只取消自己的 handle 不是急停(v0.17.1 教訓)。
  - `halt`:零速度**連發**(單發會輸給還在灌指令的 controller)、publisher
    **預先建立**(DDS discovery 沒完成前發的訊息會無聲消失)、上鎖(stdin 執行緒
    與 watchdog 執行緒都可能呼叫,rclpy 實體操作不耐並發)。
  - `_drive_loop` 的 setup 在 try **裡面**:rclpy 建實體失敗也要重置旗標並發
    result,否則 client 掛到 timeout、之後每次都「already running」(v0.17.1)。
  - 慢 ops(`capture_frame`/`pose`)丟 worker thread:急停不能排在 5 秒相機等待後面。
  - stdin EOF(主程式死了)→ 有 goal 就自主 halt:機器人絕不無人監督地繼續跑。

### `bridge/client.py`(345 行)
- **做什麼**:venv 側的 asyncio client:spawn bridge、request/response 配對、事件路由。
- **SDK**:asyncio(subprocess、Future)。
- **為什麼**:
  - `_spawn` 用 `bash -c 'source $ROS_SETUP; exec python3 ros_bridge.py'`,
    並從環境剔除 `PYTHONPATH`/`VIRTUAL_ENV` —— 隔離決定的執行面。
  - request 用遞增 id ↔ `asyncio.Future` 配對;`_read_loop` 收到回應就 resolve。
    垃圾行 `continue`(不毒害後續)、程序死亡把所有 pending 一次 fail(不掛)。
  - `configure_safety` 存下設定,**每次 (re)spawn 自動重新武裝 watchdog**;
    武裝失敗=啟動失敗 —— 絕不發出「能動但無保護」的 bridge。
  - `stop()`:kill 後 `await proc.wait()` 收屍(否則殭屍+GC 噪音);
    kill 與自然死亡的 race 用 `ProcessLookupError` 包護。

### `bridge/_avoidance.py` + `_safety_order.py`
- **做什麼**:純 stop-and-go detour、目標走廊、depth freshness、StuckDetector
  與急停順序。
- **為什麼**:stdlib-only 的**兄弟模組** —— bridge 當 sibling import、venv 測試當
  package import。把 bridge 裡的純決策抽出後,安全分支不依賴 ROS 也能單測。

### `adapters/ros2_adapter.py`(331 行)
- **做什麼**:`ros2` CLI 的 subprocess 包裝(topics/echo/pub/action/interface)。
- **SDK**:subprocess。
- **為什麼**:CLI 做得到的就不麻煩 bridge(bridge 留給需要即時性的);每個呼叫
  有 timeout 與錯誤分類(`Ros2NotAvailableError` vs `Ros2CommandError`)——
  上層才能誠實區分「沒裝」和「壞了」。

### `adapters/route_adapter.py`(102 行)
- **做什麼**:RouteAdapter 協定:`stub`(誠實拒絕)/`nav2`(CLI send_goal 後備)/
  `odom`(映到 Null —— 直驅只走 live bridge,落到這裡表示 bridge 掛了,誠實報)。
- **為什麼**:Protocol + 工廠函數 = 換導航後端不動呼叫端;`NullRouteAdapter`
  是「誠實回報」的教科書體現:回 unavailable,不假裝送出。

### `adapters/locations.py`(221 行)
- **做什麼**:locations.toml 載入/追加/模糊搜尋 + `gps_to_map_xy`(經緯度→map 公尺)。
- **SDK**:tomllib、difflib(模糊比對)、math。
- **為什麼**:`load_locations_tolerant` 把錯誤變訊息 —— 四個介面共用同一容錯語意;
  儲存是**手寫 TOML 序列化**(標準庫只有讀沒有寫,省一個依賴);
  GPS 用等距圓柱近似(校園尺度誤差公分級,不值得引地理庫)。

---

# 第二層:純邏輯核心(tools/,大部分可以直接單測)

### `tools/safety.py`(32 行)——最小卻最重要
- `halt_robot`/`arm_watchdog`:急停語意的唯一出處。TUI/WebUI/MCP/daemon 全呼叫它。
- **為什麼這麼小**:急停必須簡單到不可能出錯;它只是把「取消導航+零速度」的
  請求轉給 bridge,所有複雜性(pulse、鎖、cancel-all)都在 bridge 端。

### `tools/nav_live.py`(190 行)
- **做什麼**:`navigate_live`(送 goal→聽 feedback/result→可取消)、
  `navigate_with_fallback`(**唯一的導航調度點**:twin 預演→live bridge→CLI 後備)。
- **為什麼**:
  - 事件配對靠 tag(見 bridge);`_heartbeat` 每 2 秒 ping 餵 watchdog ——
    client 活著就不觸發自主停車,client 死了 watchdog 才接手。
  - `asyncio.CancelledError`(TUI Esc)→ `_cancel_quietly` 真的取消 Nav2 goal
    再往上拋:UI 放棄等待≠機器人停,必須顯式取消。
  - Twin Gate 掛在這裡:所有導航入口(TUI/MCP/mission/patrol/daemon)都經過
    這個函數,所以閘門不可能被繞過 —— 這就是「調度只寫一次」的安全意義。

### `tools/route_core.py` / `mission_core.py` / `skills.py` / `drive_core.py`
- **route**:regex 快路(`從X到Y`)→ LLM 慢路(結構化抽取)雙層解析;只有
  目的地是必要的(Nav2 本來就從當前位置出發)。
- **mission**:逗號分隔步驟(goto/drive 混排)的**確定性**步進器 —— 不用 LLM 迴圈,
  所以可靠可測;一步失敗記錄後續行(不棄單)。`resolve_and_navigate` 是
  mission/patrol 共用的「名稱→goal dict→導航」唯一實作。
- **skills**(patrol/dock):`parse_patrol` 只在**尾巴**吃 x3/photo 記號 ——
  地點名叫「Photo Lab」不會被吃掉(注釋裡就是這個案例)。
- **drive**:同樣 regex 快路(方向詞+中文數字正規化)→ LLM 慢路;
  時長夾 30s 上限 —— 語言理解錯誤的爆炸半徑被硬限制住。

### `tools/perception.py`(175 行)
- PerceptionLoop:定頻抓幀→VLM→`SceneAnalysis`。**只觀察不動作**;
  解析寬容(字串當清單、數字字串當信心值)但 `requires_approval` 缺失時
  **預設 True** —— 寬容絕不能讓安全旗標 fail-open。錯誤連發只報一次(不洗版)。

### `tools/decision_core.py` / `decision_eval.py`(96/110 行)
- 決策腦:六欄情境快照→有界動作單選;一切異常降級 `refer_to_human`。
- eval:TOML 情境庫→per-family accuracy/**unsafe rate**/refer rate。
- **為什麼單次呼叫不重試**:延遲與失效模式要可預測;重試是上層的政策決定。

### `tools/ros2_core.py`(403 行)
- topics/echo/schema/pub/drive 的核心;**`[vehicle]` 硬限速夾在這裡的執行路徑上**
  (`ros_drive`/`ros_pub` 進來的速度先 clamp 再發)—— LLM 給再大的數字也切掉。
- `_kind_hint` 給 topic 貼 sensor/control 標籤(UI 顯示用,純字串啟發式)。

### `tools/ros2_pkg_core.py`(286 行)
- scaffold 的核心:`PackagePlan`(pydantic 驗證名稱/依賴白名單)→
  `render_package`(**純函數**產 boilerplate)→ `write_package`(拒覆蓋)→
  `build_package`(colcon)→ `repair_node`(LLM 修一輪,只准動 node 檔)。
- **為什麼 boilerplate 用模板不用 LLM**:package.xml/setup.py 的正確性是
  機械性的,LLM 只會引入不確定;把 LLM 限制在「唯一需要創造力的地方」(node 邏輯)。

### `tools/user_skills.py`(85 行)
- skills/*.toml 載入器:名稱 regex、**保留字拒載**(技能不可以叫 stop)、
  壞檔變警告。技能=具名 mission,只能組合已 gated 原語 —— 檔案格式再自由
  也生不出新的動作種類,這是它安全的根本原因。

### `tools/*_agent_tools.py` × 4 + `registry.py` + `approval_formatters.py` + `tracking.py` + `summaries.py`
- 把 core 函數包成 openai-agents 的 `@function_tool`;registry 登記每個工具的
  風險等級(P0/P1)與影響範圍 → orchestrator 據此決定要不要彈批准卡;
  formatters 生批准卡文案;tracking 把工具呼叫記進 RunRecord;
  summaries 用 LLM 把 ROS interface 原文變人話(失敗退回 naive 欄位列表)。

### `tools/vision_core.py` / `shell_core.py`
- vision:圖檔→base64 data URL→VLM 結構化 JSON;`capture_and_analyze` =
  bridge 抓幀+分析的唯一組合點。
- shell:`assess_command` 用模式表給命令貼風險標籤(rm -rf 之類)→ 批准卡素材;
  執行帶 timeout 與輸出摘要。

---

# 第三層:LLM 供應與代理

### `providers/chat.py`(339 行)
- **做什麼**:`ask_provider`(單問)、`stream_provider`(串流)、`ask_json`、
  `ask_vision_json`、`list_provider_models`。
- **SDK**:openai.AsyncOpenAI —— 但只當「HTTP client」:base_url 指到 NIM/Ollama
  就吃相容端點,這是雲地切換一鍵化的全部秘密。
- **為什麼**:
  - **每次呼叫新建 client**(`async with AsyncOpenAI(...)`):httpx client 綁
    event loop,TUI/daemon/WebUI 各有自己的 loop,共用 client 會炸;順便不漏連線池。
  - `parse_json_reply` 寬容解析:thinking 模型會包 ```json 圍欄、前後綴廢話 ——
    所有結構化輸出都經它,一處修全域好。
  - `_provider_errors` contextmanager 統一把 SDK 例外映成 `ProviderChatError`
    (帶 profile/model 資訊)—— 呼叫端只需接一種例外。

### `providers/agent_model.py`(~40 行)
- 把 config 的 model binding(chat/plan/vision/route)轉成 openai-agents 的
  model。官方 OpenAI 預設端點用 `OpenAIResponsesModel`;Ollama/NIM/自訂
  `base_url` 用 `OpenAIChatCompletionsModel`,因相容端點通常只實作 chat.completions。

### `agent/`(/plan /run 的家,openai-agents SDK 專區)
- **specialists.py**:Supervisor + 四個專職 Agent(ROS 查詢/Motion/Navigation/
  Perception),用 SDK 的 **handoffs** 接線 —— 每個專職只帶自己的小工具集,
  小模型選工具才穩(工具全塞一個 agent 會亂選)。
- **orchestrator.py**:`/run` 的主迴圈。關鍵設計:
  - `max_turns=6`:弱模型會迴圈(重發 drive 維持運動),回合上限止血。
  - **批准=SDK interruptions**:工具標 `needs_approval` → `Runner.run` 中斷
    → TUI 彈卡 → `resume_run` 帶決定續跑。動作本體在 server 側狀態裡,
    UI 只回是/否。
  - 錯誤分類(`MaxTurnsExceeded`/`ModelBehaviorError`/`ToolTimeoutError`)
    → 誠實的 `JenAIError` 呈現,不吞。
- **runtime.py**:`build_plan_agent` 給 `tools=[]` —— /plan 不能有副作用是
  **結構保證**(沒有工具可呼叫),不是 prompt 央求。
- **guardrails.py**:輸入 guardrail 攔「拆機器人」這類請求(SDK tripwire)。
- **session.py**:`JenAIFileSession` 實作 SDK 的 Session 介面存 JSONL ——
  跨重啟記憶;`/clear` 真的清檔案。
- **context.py**:dataclass,把 config/run_store/bridge getter 穿過 SDK 的
  `context` 參數傳給每個工具 —— 工具不碰全域。
- **tracing.py**:SDK TracingProcessor 落地本地 JSONL(`traces/`)——
  不外送,除錯 /why 用。
- **plan_agent.py / run_agent.py**:組裝入口(instructions + model + tools)。

---

# 第四層:介面(四個門)

### `tui/app.py`(1383 行,最大檔)
- **SDK**:textual(App/Widget/CSS/set_interval)。
- **結構**:`JenAITuiApp(InfoCommandsMixin, RobotCommandsMixin, App)` ——
  指令 handler 按領域拆進 mixin,app 本體管:輸入分發(chat/`!`shell/slash)、
  slash palette、串流渲染、批准卡流程、active task(Esc 可取消)、吉祥物動畫。
- **為什麼**:
  - **權限模式(v0.22)**:`PERMISSION_MODES`(approve/plan/auto),shift+tab 循環;
    自然語言依模式路由到 /plan 或 /run;auto 模式在兩個批准入口短路並寫時間軸。
    (原 `_stream_chat_reply` 純聊天串流已被模式系統取代移除。)
  - 批准後的執行包成 `self._active_task`(asyncio.Task):**Esc=cancel()**,
    而 navigate_live 把 cancel 翻成真的 Nav2 取消 —— 整條鏈打通 UI 到輪子。
  - `_TEMPLATE_VALUES` 防呆:palette 補的 `<name>` 模板原文送出會被擋
    (曾把字面 `<name|number>` 存進 config)。
  - 吉祥物 `set_interval(0.6, …)`:任務跑→狗跑,是狀態指示不是裝飾。
- **panels.py**:純視覺(WelcomePanel/TimelineItem/OutputPanel/palette);
  `pixel_mark(frame, running)` 半格像素渲染(▀▄█ + 前景背景色=一格兩像素);
  各姿勢釘同 bounding box 防抖。
- **robot_commands.py / info_commands.py**:全部 slash handler;
  `_request_direct_approval` 是**非 agent 動作的唯一批准管線**(v0.8 把 7 份
  複製收成 1 份);`auto_key` 讓 /dock 與 /route 的「記住批准」不互相洩漏。
- **widgets/**:ApprovalCard(1/2/3 數字鍵、Esc 拒絕)、Plan/Tool/Error blocks。
- **help_content.py**:/help 的分組資料。

### `webui/`(server 492 / render 617 / commands 305)
- **SDK**:標準庫 `http.server.ThreadingHTTPServer` —— **為什麼不用 FastAPI**:
  零依賴、單檔可讀、流量是一個人一支手機;框架在這裡是負資產。
- **server.py**:token 認證(Bearer/cookie/`?token=`,**401 絕不 Set-Cookie**
  —— 猜錯的人不能拿到真 token)、`_PendingConfirms`(動作本體 server 端一次性
  持有,瀏覽器只拿 id —— 改不了要批准的東西)、`/api/stop` 唯一免認證
  (停永遠安全)、PoseCache(**專用執行緒跑自己的 event loop + 常駐 bridge**,
  因為 http.server 是多執行緒同步世界,不能每個請求 spawn bridge)。
- **render.py**:整頁 HTML/CSS/JS 字串內嵌(E501 豁免)—— 單檔零建置,
  嵌入式儀表板的務實選擇;多頁=CSS `body.view-*` 切換,不是真路由。
- **commands.py**:web 版指令執行 + confirm 動作封存。

### `mcp_server/server.py`(187 行)
- **SDK**:mcp(FastMCP)。`@mcp.tool()` 裝飾器把函數變 MCP 工具。
- **為什麼**:預設唯讀 + `stop`;`navigate_to` 要 `--allow-actions` 才**註冊**
  (不存在的工具無法被呼叫 —— 又是結構保證優於 prompt 約束);
  stdout 是協定通道,所以全 repo 的診斷都走 stderr(cli/main.py 的 err_console)。

### `cli/main.py`(568 行)
- **SDK**:typer。callback 統一載入 `.env`(shell 優先);
  無子命令→doctor 快檢(跳過慢的 nav 探測)→TUI。
- 各命令(doctor/web/mcp/daemon/route/loc/scaffold/eval/help)都是薄殼:
  解析參數→呼叫 core→rich 呈現。`scaffold --build` 的「生成→build→LLM 修一輪」
  策略邏輯在這裡(策略屬於入口,機制屬於 core)。

---

# 第五層:狀態、設定、健檢、守護程序

### `daemon/engine.py`(188 行)+ `daemon/runner.py`(164 行)
- engine:純規則邏輯 —— Rule(below/above/equals/affordance+min_confidence)、
  冷卻、`Decision`。**halt 免批准、goto 要 auto_approve+nav2 雙開關**;
  信心值解析失敗當 0(不觸發)—— 寬容不能觸發動作。
- runner:bridge watch → queue → engine → 動作。halt **搶佔**進行中導航;
  `_navigate` 是 fire-and-forget task,所以每個失敗都必須自己報(否則規則
  觸發了、車沒動、沒人知道為什麼);twin 啟用時 refer→block(自主路徑無人可問)。

### `config/models.py` / `store.py` / `setup.py`
- models:AppConfig + 四個 profile(Vehicle/Twin/MapDatum/Avoidance),全部
  `extra="forbid"`;vehicle.type 用 `Literal` —— 打錯字在載入期爆,不是幾個月後。
- store:路徑解析(JENAI_CONFIG→XDG→APPDATA)、.env 載入(shell 優先)、儲存。
- setup:精靈(rich Panel + typer.prompt;預設值全從 preset 來,測試用
  monkeypatch typer.prompt 餵答案)。

### `state/`(runs/session/history/reports)
- runs:RunStore —— RunRecord + SDK 原生 RunState JSON;批准中斷以原子檔案保存,
  重啟後重建 agent graph/context 與批准卡,claim 後刪檔避免重播已批准動作。
- session/history:對話 session 檔與輸入歷史(↑↓)。
- reports:巡邏 log JSON 落地 + 確定性 markdown + LLM 摘要(離線退化)。

### `doctor/checks.py`(519 行)
- 分區檢查,每項回 `DoctorCheckItem(status, message, fix_suggestion)` ——
  **帶修法的失敗**才是可自助的失敗。nav 區段全 WARN(沒跑機器人是常態不是錯誤);
  `include_nav=False` 讓 TUI 啟動不付 ros2 CLI 的秒級成本。

### `schemas/`(models 258 + outputs 203)
- 全部 pydantic `extra="forbid"`。RouteOutput 的 `execution_status` +
  `route_preview` 是誠實回報的資料載體;SceneAnalysis 的安全旗標缺失預設 True。

### `scripts/soak.py`(151 行)
- 24h 穩定性:程序樹 RSS 採樣(讀 /proc)、warmup 排除啟動爬坡、
  head/tail 窗口中位數抗噪、PASS/WARN 判定。stdlib-only,任何機器免安裝。

---

# 測試側(怎麼讀)

- **`tests/unit/fake_bridge.py`**:不是測試 —— 講 bridge 同一套 JSON 協定的
  stdlib 假程序,是「無 ROS 全綠」的關鍵。新增 bridge op 記得同步教它。
- **`test_architecture.py`**:AST 掃 import + 逐行掃載具字眼 —— 鐵律的 CI 形態。
- **`test_tui.py`**(最大):`app.run_test()` + `handle_user_text()` 驅動真 App;
  monkeypatch 打在 **handler 所在模組**(`jenai.tui.app.run_mission`),
  打在定義處(mission_core)是沒用的 —— 這是最常見的測試坑。
- 故障注入群(bridge/daemon/twin):每個「誠實降級」主張都有對應測試。

# 建議閱讀順序(共 ~6 小時)

1. `schemas/models.py` + `config/models.py`(30 分)—— 先認識資料形狀
2. `bridge/ros_bridge.py` + `bridge/client.py`(90 分)—— 全案核心,慢讀
3. `tools/nav_live.py` + `tools/safety.py` + `daemon/`(60 分)—— 安全鏈全貌
4. `tui/app.py` 的 handle_user_text→_handle_command→批准流程(60 分)
5. `providers/chat.py` + `agent/orchestrator.py`(45 分)—— LLM 這半邊
6. 其餘 tools/ 與 webui/ 挑著讀(拿著本文件當地圖)
