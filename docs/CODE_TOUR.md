# CODE_TOUR — v2.5.0 程式碼閱讀指南

> 本指南用「執行路徑」帶你讀程式，不記容易過期的行數。先讀
> [ARCHITECTURE](ARCHITECTURE.md)，需要安裝與設定細節再看
> [TECHNICAL_GUIDE](TECHNICAL_GUIDE.md)。

## 先理解四條規則

1. LLM 只理解任務與選擇已註冊 Capability，不進入即時控制迴路。
2. 正常長任務由確定性 Workflow 執行，不在每個導航點重新詢問 LLM。
3. 所有移動共用 Navigation Gateway、批准與結果驗證。
4. 完成程序不等於任務成功；task outcome 必須有可用證據。

## 建議閱讀順序

### 1. Domain 與 Workflow

先讀：

- `src/jenai/workflows/area_patrol.py`
- `src/jenai/task_results.py`
- `src/jenai/capabilities.py`
- `src/jenai/site_profiles.py`
- `src/jenai/site_assets.py`

`workflows/area_patrol.py` 是第一個深模組。它定義不可變的 area／mission model、合法狀態
轉移、確定性 coverage plan、有限重試、取消、return-home 與完成判定。這裡不 import
ROS、LLM、TUI 或 WebUI，因此可以用純單元測試驗證。

讀完應能回答：

- 哪些 area 狀態可以互相轉移？
- required area 未完成時，任務為何不能回 success？
- retry、defer、cancel 與 return-home 的上限在哪裡？
- `partial_success` 和 `requires_human_review` 差在哪裡？

### 2. Workflow application seam

接著讀：

- `src/jenai/tools/area_patrol_service.py`
- `src/jenai/tools/area_patrol_agent_tools.py`
- `src/jenai/tools/navigation_gateway.py`
- `src/jenai/tools/nav_live.py`

`area_patrol_service.py` 把純 Workflow 接到地點、Nav2、影像、報告與 audit。Agent adapter
很薄，只負責將模型 tool call 轉成同一個 Workflow request。TUI、自然語言與未來入口不應
各自複製巡邏流程。

`navigation_gateway.py` 是移動的唯一公開 seam。新增會移動機器人的能力時，先確認它能否
經過這裡；不能時，應先修正 interface，而不是繞過它。

### 3. ROS 2 bridge

再讀：

- `src/jenai/bridge/client.py`
- `src/jenai/bridge/_protocol.py`
- `src/jenai/bridge/_wire.py`
- `src/jenai/bridge/_nav_plan.py`
- `src/jenai/bridge/_navigation_state.py`
- `src/jenai/bridge/ros_bridge.py`

uv 虛擬環境與系統 ROS Python 分離，因此 rclpy 在 bridge 子程序中執行，主程式透過有界
JSON/stdio protocol 呼叫。`client.py` 管生命週期、timeout、取消與錯誤分類；
`ros_bridge.py` 應維持為 ROS 接線殼，純判斷盡量放在可單測 sibling module。

相關安全模組：

- `_watchdog.py`: client 中斷時的停止責任
- `_safety_order.py`: cancel、zero command 與 cleanup 的順序
- `_avoidance.py`: legacy bring-up 路徑的有界障礙判斷
- `_drive_control.py`: legacy odom bring-up，不是產品高階導航
- `_occupancy.py`: map metadata 與 occupancy 判斷

### 4. Agent 與 deterministic fast path

再讀：

- `src/jenai/agent/intent_routing.py`
- `src/jenai/agent/fast_paths.py`
- `src/jenai/agent/context.py`
- `src/jenai/agent/orchestrator.py`
- `src/jenai/agent/runtime.py`
- `src/jenai/tools/registry.py`

明確的唯讀狀態問題可由 fast path 直接取得 pose、scan 與 Nav2 狀態。需要語意理解或
Capability 選擇時才交給 Agent。兩條路都必須使用同一套工具、audit 與誠實結果格式。

`registry.py` 是 Capability allowlist。模型輸出的名稱或參數在通過 schema、registry、
policy、approval 和執行前狀態檢查前，一律視為不可信。

Provider 位於：

- `src/jenai/providers/chat.py`
- `src/jenai/providers/agent_model.py`

它們只處理 OpenAI-compatible 模型介面；不得放入 mission 或 ROS 邏輯。

### 5. 互動入口

TUI：

- `src/jenai/tui/app.py`: Textual composition 與事件轉接
- `src/jenai/tui/command_palette.py`: 可單測的 slash palette 狀態與補完規則
- `src/jenai/tui/command_dispatch.py`: 可單測的 slash grammar 與 handler 分派
- `approval_flow.py`: Agent 暫停／恢復的批准生命週期
- `approval_policy.py`: 權限模式判斷
- `direct_execution.py`: 已批准的直接能力執行
- `robot_commands.py`, `location_commands.py`, `info_commands.py`: 命令分類
- `panels.py`, `widgets/`: rendering

WebUI：

- `src/jenai/webui/server.py`: HTTP、token、confirm lifecycle
- `commands.py`: 共用命令解析
- `render.py`: HTML rendering

其他入口：

- `src/jenai/cli/`: 安裝、doctor、site、data 與 process entry
- `src/jenai/mcp_server/`: MCP transport；預設唯讀
- `src/jenai/daemon/`: 事件規則與明確授權的自動動作

入口只負責 transport、批准與呈現。導航、巡邏、停止、報告若只在某個 UI 中實作，就是應
修正的淺模組。

### 6. State、證據與診斷

- `src/jenai/state/runs.py`: run lifecycle
- `src/jenai/state/task_receipts.py`: task outcome 與證據 receipt
- `src/jenai/state/reports.py`: patrol／workflow durable report
- `src/jenai/state/audit.py`: 有界 audit store
- `src/jenai/state/data_lifecycle.py`: export、prune、purge
- `src/jenai/doctor/checks.py`: 環境、ROS、Nav2、provider 與 site 診斷

讀取結果時先找 task outcome，再看 process lifecycle。不得因程序正常 exit 就推導出
導航、巡檢、Dock 或充電成功。

### 7. Twin 與正式驗收

- `src/jenai/twin/gate.py`: candidate task rehearsal verdict
- `src/jenai/acceptance/isaac_hil.py`: 正式 Isaac/Nav2 acceptance
- `scripts/isaac_hil_acceptance.py`: HIL CLI harness

Twin Gate 必須使用隔離 ROS domain 才能提供隔離證據。Isaac HIL 證明模擬執行鏈，不外推
實體安全、跨載具或物理充電。

## 四條實際 trace

### 唯讀自然語言

```text
TUI input
→ intent_routing
→ fast_paths
→ bridge client
→ pose/scan/Nav2 snapshot
→ fixed summary
→ audit
```

### 單點導航

```text
request
→ location/site validation
→ approval
→ navigation_gateway
→ bridge Nav2 action
→ terminal pose verification
→ task receipt
```

### 語意區域巡邏

```text
Agent selects area_patrol
→ area_patrol_agent_tools
→ area_patrol_service
→ workflows.area_patrol state machine
→ navigation + observation adapters
→ evidence and coverage evaluation
→ return home
→ durable report
```

### 緊急停止

```text
TUI / WebUI / MCP / daemon
→ shared stop capability
→ cancel active Nav2 goal
→ bounded zero command / acknowledgement
→ audit and terminal task outcome
```

Stop 不依賴 LLM，也不需要動作批准。

## 測試怎麼讀

1. `tests/unit/test_area_patrol_workflow.py`: 純 Workflow invariant
2. `tests/unit/test_area_patrol_adapter_contracts.py`: Workflow 與 adapter 協作
3. `tests/unit/test_architecture.py`: import 與依賴規則
4. `tests/unit/test_bridge_client.py`: protocol、timeout、cancel、故障注入
5. `tests/unit/test_tui.py`: 真 Textual app 的命令與批准流程
6. `tests/integration/`: 跨模組 contract
7. `docs/validation/ISAAC_HIL_ACCEPTANCE.md`: live Isaac/Nav2 gate

修 bug 時先增加能重現外部行為的 regression test，再修改 implementation。若測試必須繞過
公開 interface 才能寫，通常表示 module 或 seam 的形狀需要重看。
