# JenAI 核心功能規格 v0.1.0

> 📜 **設計期文件**（v0.1 規劃階段，僅供追溯）。實際現況以
> [TECHNICAL_GUIDE](../../TECHNICAL_GUIDE.md)、[COMMANDS](../../COMMANDS.md)
> 與程式碼為準；方向與 roadmap 見 [PROJECT_DIRECTION](../../PROJECT_DIRECTION.md)。


每個功能依以下格式定義：功能說明 / 輸入格式 / 輸出 schema / 錯誤情境 / TUI 行為 / 驗收條件。

---

## F01 — `JenAI` 主入口

### 功能
單一主入口，判斷是否完成首次設定；若無則進 setup wizard，若有則進 TUI 主畫面。

### 輸入
```
JenAI [--debug] [--config <path>]
```

### 前置檢查順序
1. 設定檔是否存在且完整
2. provider profile 是否配置
3. default model bindings 是否完整
4. ROS2 / vision / locations 基本可用性

### 輸出
- 首次使用 → setup wizard
- 已設定 → TUI 主畫面（顯示 provider / model / ROS 狀態）
- 環境異常 → 提示執行 `JenAI doctor`

### 錯誤情境
| 錯誤 | 行為 |
|---|---|
| 設定檔損毀 | `config_error` + 提示重新 setup |
| provider 未配置 | 引導進 setup |
| 啟動資源缺失 | 建議 `JenAI doctor` |

### 驗收條件
- [x] 首次使用可完成設定並寫入設定檔
- [x] 第二次啟動直接進 TUI，不重複 setup
- [x] 啟動流程不要求手動操作環境命令
- [ ] 3 秒內進入可操作主畫面（已設定情況） <!-- 效能指標，尚未量測 -->

---

## F02 — `JenAI doctor`

### 功能
檢查執行環境健康狀態，涵蓋所有 JenAI 依賴項目。

### 檢查項目
| 項目 | 說明 |
|---|---|
| Python / uv / venv | 執行環境完整性 |
| ROS2 Jazzy | `ros2` 指令可用 + 環境 sourced |
| Provider 連線 | base URL 可達 + model 可用 |
| Vision model | VLM 可用性 |
| Location schema | locations 檔案格式正確 |
| Route adapter | route 工具可用 |
| WebUI assets | WebUI 靜態資源完整 |

### 輸出 Schema
```python
DoctorResult(
    overall: "pass" | "warn" | "fail",
    items: [
        DoctorCheckItem(
            section: str,
            check_name: str,
            status: "pass" | "warn" | "fail",
            message: str,
            fix_suggestion: str | None
        )
    ],
    checked_at: datetime
)
```

### 驗收條件
- [x] 每個 fail 都有具體可執行的 fix_suggestion
- [ ] provider 檢查能區分「base URL 不可達」與「model 不存在」 <!-- 需連線探測，尚未實作 -->
- [x] ROS2 檢查能區分「ros2 指令不存在」與「環境未 source」

---

## F03 — `/help`

### 功能
顯示 JenAI 的核心概念、命令分類、範例與快捷鍵，作為 onboarding 入口。

### 輸入格式
```
/help
/help ros
/help route
/help vision
/help planning
```

### 輸出 Schema
```python
HelpOutput(
    title: str,
    summary: str,
    command_groups: list[CommandGroup],
    examples: list[str],
    keyboard_shortcuts: list[KeyboardShortcut]
)
```

### 驗收條件
- [x] 新使用者能在 30 秒內從 `/help` 找到第一個可執行動作
- [ ] 不超過 2 屏主畫面高度 <!-- 依終端機高度而定，尚未量測 -->
- [x] 分組 help 可正確過濾顯示

---

## F04 — `/plan <task>`

### 功能
規劃任務，分析需求並產生 execution plan，**不執行任何 side-effect 工具**。

### 輸入
自然語言任務描述。

### 輸出 Schema
```python
PlanOutput(
    task_summary: str,
    assumptions: list[str],
    plan_steps: list[PlanStep],
    candidate_tools: list[str],
    approval_checkpoints: list[str],
    expected_output: str
)
```

### TUI 行為
- 產生 `PlanBlock`，預設展開
- 每個 step 顯示標題、說明、是否需批准

### 驗收條件
- [x] 不呼叫任何有 side effect 的工具
- [x] 清楚標示哪些步驟需要 approval
- [ ] 任務模糊時要求澄清，而非亂猜 <!-- 由 LLM 指令保證，未做確定性測試 -->

---

## F05 — `/run <task>`

### 功能
執行任務，允許 agent 呼叫工具，遇到敏感工具時進入 `awaiting_approval`。

### 輸入
自然語言任務描述。

### 輸出 Schema
```python
RunOutput(
    run_id: str,
    status: RunStatus,
    current_step: str,
    tool_calls: list[ToolCallRecord],
    interruptions: list[ApprovalRequest],
    final_output: str
)
```

### 特殊行為
- 支援 streaming output
- 支援 interruption resume（批准後從同一 run 繼續）
- 拒絕後 agent 必須提出替代方案或回報失敗原因

### 驗收條件
- [x] 敏感工具一律進入 `awaiting_approval`，不可繞過
- [x] 批准後從原狀態繼續，而非重新開始
- [x] 拒絕後有可讀的後續處理，不允許靜默跳過

---

## F06 — `/ros topics`

### 功能
列出目前 ROS2 graph 中可見的 topics。

### 輸出 Schema
```python
RosTopicsOutput(
    topics: list[TopicItem(
        name: str,
        kind_hint: "control" | "sensor" | "debug" | "unknown"
    )]
)
```

### 驗收條件
- [x] ROS2 可用時能正確列出
- [x] 無 ROS2 時顯示 `env_error` + 建議修復步驟
- [x] 空 topic 列表時有友善提示

---

## F07 — `/ros topic-info <topic>`

### 功能
查詢單一 topic 的 type、publishers 與 subscribers 資訊。

### 輸出 Schema
```python
RosTopicInfoOutput(
    name: str,
    message_type: str,
    publisher_count: int,
    subscriber_count: int,
    publishers: list[str],
    subscribers: list[str],
    summary: str
)
```

### 驗收條件
- [x] topic 不存在時給候選建議（fuzzy match）
- [x] 成功時包含完整 type 資訊

---

## F08 — `/ros schema <topic>`

### 功能
根據 topic 自動查 type，再查 message interface，最後以自然語言摘要欄位。

### 內部流程
1. 取 topic type（via topic-info）
2. 查 message interface（ros2 interface show）
3. LLM 摘要欄位意義

### 輸出 Schema
```python
RosSchemaOutput(
    topic: str,
    message_type: str,
    raw_interface: str,
    field_summary: list[FieldSummary(
        field_name: str,
        field_type: str,
        description: str
    )],
    example_payload: dict
)
```

### TUI 行為
- 預設顯示 field_summary
- raw_interface 放折疊區塊
- example_payload 可直接複製作為 `/ros pub` 草稿

### 驗收條件
- [x] 能串起 topic-info 與 interface show
- [x] example_payload 格式合法，可直接用於 `/ros pub`

---

## F09 — `/ros echo <topic>`

### 功能
即時監看 topic 訊息流。

### 輸入
```
/ros echo <topic> [--limit N] [--timeout S]
```

### 輸出 Schema
```python
RosEchoOutput(
    topic: str,
    mode: "stream" | "snapshot",
    messages: list[dict],
    summary: str
)
```

### 驗收條件
- [x] 有流量時可持續顯示 <!-- v0.1.0 為 snapshot 模式，可指定 count 擷取多筆 -->
- [ ] 可手動停止 <!-- 連續 stream 模式尚未實作；snapshot 為有界擷取 -->
- [x] timeout 後若無訊息要給友善提示

---

## F10 — `/ros pub <topic> <payload>`

### 功能
向指定 topic 發送訊息（**需批准**）。

### 內部流程
1. 驗證 topic 存在
2. 驗證 payload 格式符合 message type
3. 產生 approval card
4. 使用者批准後執行
5. 回報結果

### Approval Card 必顯示
- target topic
- message type
- payload preview
- effect_scope = `sim_control`

### 輸出 Schema
```python
RosPubOutput(
    topic: str,
    message_type: str,
    payload_preview: dict,
    approval_status: str,
    execution_status: str,
    result_message: str
)
```

### 驗收條件
- [x] 未批准不得執行
- [x] payload 格式不合法時阻止執行並說明錯誤
- [x] 執行成功後有明確回報

---

## F11 — `/route <natural language>`

### 功能
把自然語言起終點解析成 locations 座標，產生 route preview，批准後送出導航命令（**需批准**）。

### 內部流程
1. 抽取 start / goal
2. 查詢 locations，fuzzy resolve
3. 產生 route preview
4. Approval card
5. 送出 route command

### 輸出 Schema
```python
RouteOutput(
    input_text: str,
    resolved_start: Location,
    resolved_goal: Location,
    candidate_matches: list[Location],
    route_preview: str,
    outgoing_action: dict,
    approval_status: str,
    execution_status: str
)
```

### 錯誤情境
| 錯誤 | 行為 |
|---|---|
| 缺少起點或終點 | 請求補充 |
| locations 找不到 | fuzzy 候選 + 請求確認，不亂猜 |
| 多個候選太相近 | 列出候選，請求使用者選擇 |
| route adapter 不可用 | `env_error` |

### 驗收條件
- [x] 找不到位置時不亂猜
- [x] 批准前只做 preview，不送出
- [x] 執行後有明確結果回報

---

## F12 — `/loc list` 與 `/loc show`

### `/loc list` 輸出
```python
LocListOutput(
    locations: list[LocationSummary(
        id: str,
        name: str,
        aliases: list[str],
        tags: list[str]
    )]
)
```

### `/loc show <name>` 輸出
```python
LocShowOutput(
    id: str,
    name: str,
    aliases: list[str],
    frame_id: str,
    pose: Pose2D,
    tags: list[str],
    description: str | None
)
```

### 驗收條件
- [x] `/loc show` 支援 alias 查詢
- [x] 找不到時給 fuzzy 建議
- [x] schema 不合法時給出明確錯誤

---

## F13 — `/vision image <path>`

### 功能
讀取使用者指定圖片並交給 VLM 分析。

### 輸出 Schema
```python
VisionOutput(
    source: str,
    summary: str,
    objects: list[str],
    anomalies: list[str],
    relevance_to_task: str,
    next_action_suggestions: list[str]
)
```

### 驗收條件
- [x] 非圖片檔案要阻止並報錯
- [x] 分析結果可與目前任務上下文結合
- [ ] 支援 WebUI 圖文並排顯示 <!-- WebUI 尚未實作 -->

---

## F14 — `/shell <cmd>`

### 功能
執行 shell 命令（**需批准**）。

### 內部流程
1. 解析命令
2. 評估 risk_level 與 effect_scope
3. 產生 approval card
4. 批准後執行
5. 彙整 stdout/stderr

### Approval Card 必顯示
- 原始命令
- 工作目錄
- risk_level
- effect_scope
- 是否包含寫入/刪除/安裝等行為

### 輸出 Schema
```python
ShellOutput(
    command: str,
    working_directory: str,
    risk_summary: str,
    approval_status: str,
    exit_code: int,
    stdout_summary: str,
    stderr_summary: str
)
```

### 驗收條件
- [x] 未批准不得執行
- [x] stdout / stderr 有結構化顯示
- [x] 高風險命令額外加強警示（p2 level）


---

## 完整 Agent 架構（v0.2，基於 openai-agents SDK）

把 JenAI 從「工具執行器」升級成分工協作的 agent。分三階段實作，全部有單元測試。

### Phase 1 — 多-agent + 記憶 + 可觀測性
- **多-agent（SDK handoffs）**：`Supervisor` agent 依需求 handoff 給專職 agent
  —— `ROS Explorer`（唯讀查詢）、`Motion`（發布/驅動）、`Navigation`（導航）、
  `Perception`（視覺）。每個 agent 只帶自己領域的工具，工具選擇更可靠。
  程式：`src/jenai/agent/specialists.py`（`agents.Agent(handoffs=[...])`）。
- **對話記憶（SDK Session）**：`JenAIFileSession` 實作 `agents.memory.SessionABC`，
  以 JSON 檔持久化對話，`Runner.run(session=...)`（初次執行與 resume 都帶）自動載入/續存。
  互動 TUI 用「依工作目錄推導的**穩定 session id**」，記憶**跨重啟保存**（不同專案各自獨立），
  歷史上限 200 筆、原子寫入，`/clear` 連帶清除記憶。程式：`agent/session.py`。
- **可觀測性（SDK Tracing）**：`FileTracingProcessor` 以 `agents.set_trace_processors`
  **取代**預設的 OpenAI 後端 exporter（因此 trace 不會外傳），把每次 run 的推理/工具呼叫/
  handoff 寫成本地 JSONL。程式：`agent/tracing.py`。

### Phase 2 — 安全護欄 + 閉環感知 + 真導航
- **Guardrails（SDK input guardrail）**：`unsafe_command_guardrail`（`@input_guardrail`）
  攔截「解除安全/無視障礙」等**停用安全機制**的意圖（不誤擋「最高速多少」這類善意問句）；
  底層再加**確定性速度夾限**（`ros2_core._safety_clamp`，硬性上限 1.0 m/s / 2.0 rad/s，
  遞迴涵蓋 Twist 與巢狀的 TwistStamped，不依賴 LLM）。程式：`agent/guardrails.py`。
- **閉環感知**：`/ros state` 與 `ros_state_tool` 一次快照 `/odom`+`/scan`，讓 agent 能
  「先觀察再決策」。程式：`ros2_core.ros_state`。
- **真導航（Nav2）**：`Nav2RouteAdapter` 以 `NavigateToPose` action 送目標；Nav2 未啟動時
  誠實回報 `unavailable`（不假成功）。以 `route_adapter = "nav2"` 啟用。程式：`adapters/route_adapter.py`。

### Phase 3 — 自主任務 + 回報
- **確定性任務執行器**：`/mission 廚房, drive 左轉, 大廳` 依序執行各步（不靠 LLM 迴圈，
  可靠且可測），批准一次後逐步執行並串流進度，最後產出報告。程式：`tools/mission_core.py`。

### 已知範圍
- `/run` 的 agent 迴圈在本地弱模型下仍受「一次一動作」護欄限制；多步自主請用 `/mission`（確定性）。
- Nav2 與閉環感知需真實機器人堆疊在線才能端到端驗證；程式為誠實實作 + 單元測試。
