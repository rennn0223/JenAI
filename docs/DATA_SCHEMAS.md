# JenAI 共用資料結構

> 📜 **設計期文件**(v0.1 規劃階段)。實際實作已演進,現況以 [TECHNICAL_GUIDE.md](TECHNICAL_GUIDE.md)、[COMMANDS.md](COMMANDS.md) 與程式碼為準;方向與 roadmap 見 [PROJECT_DIRECTION.md](PROJECT_DIRECTION.md)。


所有核心模組（TUI、WebUI、agent、adapters）共用以下資料物件定義。
v0.1.0 使用 Python dataclass 或 Pydantic BaseModel 實作。

---

## SessionState

```python
class SessionState:
    session_id: str
    created_at: datetime
    updated_at: datetime
    mode: Literal["chat", "assist", "operate"]
    theme: Literal["system", "dark", "light"]
    provider_profile: str
    model_bindings: ModelBindings
    working_directory: str
    ros_context: RosContext
    current_run_id: Optional[str]
    history_cursor: int
    input_history: list[str]
```

---

## RunRecord

```python
class RunRecord:
    run_id: str
    session_id: str
    user_input: str
    status: RunStatus  # 見 STATE_MACHINE.md
    task_summary: Optional[str]
    plan_steps: list[PlanStep]
    tool_calls: list[ToolCallRecord]
    interruptions: list[ApprovalRequest]
    final_output: Optional[str]
    error: Optional[JenAIError]
    started_at: datetime
    finished_at: Optional[datetime]
```

---

## PlanStep

```python
class PlanStep:
    step_id: str
    title: str
    description: str
    reason: str
    candidate_tools: list[str]
    requires_approval: bool
    status: Literal["pending", "active", "done", "skipped", "failed"]
```

---

## ToolCallRecord

```python
class ToolCallRecord:
    tool_call_id: str
    tool_name: str
    category: Literal["ros2", "vision", "shell", "provider", "route", "loc"]
    input_summary: str
    raw_input: dict
    status: Literal[
        "queued", "running", "awaiting_approval",
        "succeeded", "failed", "rejected"
    ]
    risk_level: Literal["p0", "p1", "p2"]
    effect_scope: Literal[
        "none", "read", "local_write", "sim_control", "host_command"
    ]
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    output_summary: Optional[str]
    raw_output: Optional[dict]
    error: Optional[JenAIError]
```

---

## ApprovalRequest

```python
class ApprovalRequest:
    approval_id: str
    run_id: str
    tool_call_id: str
    title: str
    summary: str
    raw_action: str
    risk_level: Literal["p0", "p1", "p2"]
    effect_scope: str
    justification: str
    status: Literal["pending", "approved", "rejected"]
    created_at: datetime
    resolved_at: Optional[datetime]
```

---

## JenAIError

```python
class JenAIError:
    error_type: Literal[
        "config_error",
        "env_error",
        "validation_error",
        "tool_error",
        "approval_rejected",
        "model_error"
    ]
    message: str
    details: Optional[dict]
    fix_suggestion: Optional[str]
```

---

## ModelBindings

```python
class ModelBindings:
    chat: str         # 一般對話
    plan: str         # 任務規劃
    vision: str       # 視覺分析
    route: str        # 路由解析
    default: str      # 未指定時回退
```

---

## RosContext

```python
class RosContext:
    available: bool
    domain_id: Optional[int]
    namespace: Optional[str]
    known_topics: list[str]
    last_checked: Optional[datetime]
```

---

## Location

```python
class Location:
    id: str
    name: str
    aliases: list[str]
    frame_id: str
    pose: Pose2D       # x, y, yaw
    tags: list[str]
    description: Optional[str]
```

---

## DoctorResult

```python
class DoctorCheckItem:
    section: str
    check_name: str
    status: Literal["pass", "warn", "fail"]
    message: str
    fix_suggestion: Optional[str]

class DoctorResult:
    overall: Literal["pass", "warn", "fail"]
    items: list[DoctorCheckItem]
    checked_at: datetime
```

