# JenAI 模組架構

> 📜 **設計期文件**（v0.1 規劃階段，僅供追溯）。實際現況以
> [TECHNICAL_GUIDE](../../TECHNICAL_GUIDE.md)、[COMMANDS](../../COMMANDS.md)
> 與程式碼為準；方向與 roadmap 見 [PROJECT_DIRECTION](../../PROJECT_DIRECTION.md)。


## 總覽

```
JenAI/
├── jenai/
│   ├── cli/              # CLI 入口（JenAI 主命令）
│   ├── tui/              # Textual TUI 介面
│   ├── webui/            # WebUI 監控介面
│   ├── agent/            # OpenAI Agents SDK agent 核心
│   ├── tools/            # 所有 agent 工具實作
│   ├── adapters/         # 外部系統 adapter（ROS2, route）
│   ├── state/            # SessionState, RunRecord 管理
│   ├── schemas/          # Pydantic 資料結構定義
│   ├── providers/        # OpenAI-compatible provider 管理
│   └── config/           # 設定檔讀寫
└── tests/
    ├── unit/
    ├── integration/
    └── e2e/
```

---

## 模組說明

### `cli/`
- JenAI 主入口
- JenAI doctor / config / providers / models / version
- 非互動式 route / loc 命令
- 負責啟動 TUI 或 WebUI

### `tui/`
- 基於 Textual 的 TUI 實作
- InputComposer（slash palette / tab completion / history）
- Block renderer（PlanBlock, ToolBlock, ApprovalCard, ErrorBlock）
- Header bar（provider / model / ROS status）

### `agent/`
- 基於 OpenAI Agents SDK
- 管理 run lifecycle（idle → running → awaiting_approval → completed）
- 處理 tool call interruptions 與 resume
- 透過 OpenAI-compatible API 串接 provider（包含遠端 LiteLLM gateway）

### `tools/`
- 每個 slash 指令背後的工具實作
- 每個工具都定義 `risk_level` 與 `effect_scope`
- 敏感工具（pub, route, shell）統一套用 `needs_approval` decorator

### `adapters/`
- `ros2_adapter.py`：包裝 ros2 CLI 工具（topics, info, echo, pub）
- `route_adapter.py`：route 指令送出到 ROS2 navigation stack
- `vision_adapter.py`：圖片傳送至 VLM

### `state/`
- SessionState 管理
- RunRecord CRUD
- 歷史輸入管理

### `schemas/`
- 所有 Pydantic 資料結構（見 DATA_SCHEMAS.md）

### `providers/`
- OpenAI-compatible client 與 provider profile 管理
- ModelBindings 管理
- Health check 整合

### `config/`
- 設定檔讀寫（YAML / TOML）
- Setup wizard 流程

---

## 核心依賴

| 套件 | 用途 |
|---|---|
| `openai-agents` | Agent framework, run state, tool calls, approvals |
| `openai` | OpenAI-compatible client(LiteLLM 可部署為遠端 gateway) |
| `textual` | TUI 框架 |
| `pydantic` | 資料結構驗證 |
| `typer` | CLI 命令定義 |
| `rclpy` / `ros2cli` | ROS2 整合 |
| `pytest` | 測試框架 |

---

## 資料流（以 `/run` 為例）

```
使用者輸入 /run <task>
    │
    ▼
InputComposer (tui/)
    │ 解析命令
    ▼
Agent.run() (agent/)
    │ streaming
    ▼
PlanStep generation
    │
    ▼
Tool call → tools/ros2_tool.py
    │ risk_level = p1
    ▼
needs_approval? → YES
    │
    ▼
ApprovalCard 顯示 (tui/)
    │ 使用者 Enter
    ▼
Run resume (agent/)
    │
    ▼
adapters/ros2_adapter.py
    │
    ▼
RunRecord update (state/)
    │
    ▼
SummaryBlock 顯示 (tui/)
```
