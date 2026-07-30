# Robot Runtime Protocol v0 Draft

> 狀態：Proposed／documentation-only
>
> 本文件不實作 HTTP server、NXDog motion 或 production migration。

## Decision under assessment

採用：

> **簡潔、task-oriented 的公開 Interface，搭配 event-centric 的內部實作。**

一般 caller 只理解 Task、Approval、Evidence、Stop 與 Task Outcome，並只使用
`submit`、`observe`、`stop` 三個語意入口。Command lease、
fencing token、safety epoch、idempotency ledger、deadline budget、event journal 與
Adapter lifecycle 由 `RobotRuntimeAuthority` module 隱藏。這讓 Runtime 成為深 module：
Interaction surfaces 取得高 leverage，而 distributed-control complexity 保持 locality。

顯式 lease management 暫不成為 public v0 Interface。只有未來出現真正需要跨多個 task
持有 lease 的 fleet owner，才考慮新增 admin-scoped Interface。

## Scope

Protocol v0 必須：

- 支持現有 Isaac/Nav2 Adapter 與 in-memory Adapter；
- 支持未來 NXDog Edge Adapter，不把 vendor transport洩漏給 caller；
- 讓 TUI、WebUI、MCP 投影同一份 task/approval/stop/evidence truth；
- 在 HTTP disconnect、retry、reconnect與 process restart 下保留誠實 lifecycle；
- 讓 STOP provider-free、免批准、優先於一般 command；
- 維持現有 Navigation Gateway、Site Profile、Completion Contract 與 Task Outcome。

Protocol v0 不做：

- 任意 ROS topic/service/action proxy；
- raw `/cmd_vel` 或 vendor endpoint；
- simulator Stop/Play/Replay；
- fleet scheduling；
- NXDog motion enablement；
- physical safety certification。

## Public Interface

概念 Interface 只有三個操作：

```text
submit(command_envelope)                -> CommandAccepted
observe(observation_request)            -> RuntimeSnapshot | TaskEvent stream
stop(stop_envelope)                     -> StopView
```

`submit` 使用 bounded discriminated union 表示 start Capability、resolve approval 與
task-scoped cancel；`observe` 只讀取 authoritative snapshot／replay／live stream；
`stop` 是 robot-scoped safety operation，繞過一般 command admission、lease與approval。
三者不可合併。HTTP/JSON + SSE 只是第一個 Adapter，Runtime core 不依賴 HTTP。

建議 transport mapping：

```text
POST /runtime/v0/commands
GET  /runtime/v0/observations
POST /runtime/v0/stop
```

`GET /observations` 依 `Accept`／`follow` 回 JSON snapshot 或 SSE；cursor 使用
`after_sequence`／`Last-Event-ID`。`CancelCommand` 是task-scoped operation；`stop` 是
robot-scoped safety operation。Public Interface不為每個vendor或Capability建立一條淺
HTTP endpoint。

## Core schemas

以下是 transport-neutral logical schema，不是最終 Pydantic/OpenAPI code。

### `ClientHello`

```text
supported_api_versions: [string]
client_id: string
source_surface: tui | webui | mcp | cli | daemon | test
last_runtime_id?: string
last_event_sequence?: integer
```

### `RuntimeDescriptor`

```text
api_version: string
runtime_id: string
boot_id: string
server_time: timestamp
auth_mode: string
supported_capabilities: [CapabilityDescriptor]
robot: RobotIdentity
current_safety_epoch: integer
latest_event_sequence: integer
oldest_replayable_sequence: integer
event_retention: RetentionDescriptor
```

Version negotiation 必須 fail closed。Client 沒有共同 version 時回
`UNSUPPORTED_PROTOCOL`，不能自動猜 schema。

### `RobotIdentity`

```text
robot_id: string
display_name: string
platform_type: string
runtime_adapter_id: string
runtime_adapter_version: string
capability_card_version: string
site_profile_id?: string
map_identity?: MapIdentityEvidence
```

`runtime_adapter_id` 可顯示 `isaac_nav2` 或 `nxdog_edge`，但 public command 不得包含
ROS/vendor operation name。

### `RuntimeHealth`

```text
status: available | degraded | unavailable | read_only
observed_at: timestamp
checks: [HealthCheck]
limitations: [string]
```

`available` 仍不等於某個 Capability ready。每個 Capability 的 prerequisite 另行評估。

### `RuntimeState`

```text
robot: RobotIdentity
health: RuntimeHealth
safety_epoch: integer
active_task?: TaskSummary
effectful_command_lease_state: idle | held | stopping | unknown
latest_evidence: [EvidenceEnvelope]
latest_event_sequence: integer
```

### `RequestMeta`

```text
api_version: string
client_id: string
source_surface: string
idempotency_key: string
expected_safety_epoch: integer
deadline: timestamp
```

`deadline` 在接受 request 時轉成 Runtime-local monotonic budget；wall-clock 只作 transport
資料。過期 command 不得開始執行。

### `CommandEnvelope`

```text
meta: RequestMeta
robot_id: string
operation: StartCapability | ResolveApproval | CancelCommand
```

```text
StartCapability:
  kind: start_capability
  capability_id: string
  input_schema_version: string
  input: typed capability input
  operator_context?: redacted string

ResolveApproval:
  kind: resolve_approval
  command_id: string
  approval_id: string
  decision: approve | reject

CancelCommand:
  kind: cancel_command
  command_id: string
  reason: string
```

`StartCapability.input` 只接受 Capability Registry 中的 typed schema，例如 registered
location ID；不接受任意 ROS JSON、topic、action、vendor URL或 filesystem path。
Approval的canonical action digest只由server持有並比對，不由browser/client回傳。

### `ObservationRequest`

```text
client: ClientHello
robot_id?: string
command_id?: string
after_sequence?: integer
follow: boolean
```

無cursor時，Runtime原子取得head `H`與snapshot；第一個frame是
`snapshot(sequence=H)`，之後只送sequence `> H`。有cursor時按序replay再接live events。
cursor早於retention floor時回`REPLAY_GAP`／`SnapshotRequired`，caller重新取得snapshot，
不得自行猜遺失事件。

### `RuntimeSnapshot`

```text
descriptor: RuntimeDescriptor
state: RuntimeState
active_tasks: [TaskView]
head_sequence: integer
```

Snapshot是detached、immutable projection；caller不得由舊snapshot取得command authority。

### `CommandAccepted`

```text
command_id: string
task_id: string
state: awaiting_approval | accepted | rejected
safety_epoch: integer
created_at: timestamp
deadline: timestamp
accepted_sequence: integer
replayed: boolean
```

`command_id` 由 Runtime 產生。相同
`(authenticated principal, runtime_id, idempotency_key)` 與相同 canonical request digest
必須回傳原 acknowledgement，且不得新增event或重新dispatch；相同 key 但不同 digest
回`IDEMPOTENCY_CONFLICT`。Canonical request digest 只存在 server-side
idempotency／approval binding，不序列化給 browser、client、event 或 receipt。

### `ApprovalView`

```text
approval_id: string
display_title: string
parameters: [redacted, operator-readable parameter]
preview_complete: boolean
expires_at: timestamp
safety_epoch: integer
```

Browser/client projection 不取得 raw action digest。`ApprovalDecision` 提交 approval ID、
decision、idempotency key與 expected safety epoch；Runtime 在 server 內重新比對 exact
action digest。Preview 是「與 server-side exact action 綁定的 operator-readable
representation」，不宣稱顯示所有浮點精度。

### `TaskView`

```text
task_id: string
command_id: string
robot_id: string
capability_id: string
status: proposed | awaiting_approval | accepted | running
      | stopping | completed | blocked | failed | cancelled
outcome?: TaskOutcome
failure_code?: string
current_step?: string
progress?: typed progress
pending_approval?: ApprovalView
evidence_summary: [EvidenceReference]
receipt?: TaskReceiptReference
safety_epoch: integer
latest_event_sequence: integer
```

Terminal status 是 single-assignment。晚到 success 不得覆蓋 STOP/cancel 後的 cancelled
outcome。

### `TaskEvent`

公開 event vocabulary 保持 task-centric：

```text
TaskAccepted
ApprovalRequired
ApprovalResolved
ApprovalInvalidated
TaskStarted
TaskProgressed
EvidenceAdded
TaskFinished
SafetyEpochAdvanced
StopProgressed
StopFinished
RuntimeAvailabilityChanged
SnapshotRequired
```

每筆 event：

```text
runtime_id: string
boot_id: string
event_id: string
sequence: integer
task_id?: string
command_id?: string
kind: string
occurred_at: timestamp
schema_version: string
data: typed event data
evidence_refs: [string]
```

`sequence`是同一runtime continuity內的canonical replay cursor與duplicate-suppression key；
`event_id`只作immutable tracing identity，不參與ordering。Client保存最新連續sequence，
並以sequence去重。

Runtime 內部可有更細的 lease/adapter journal，但不把它變成所有 UI 都要學的 public
Interface。

### `EvidenceEnvelope`

```text
evidence_id: string
robot_id: string
task_id?: string
command_id?: string
safety_epoch: integer
kind: string
source: EvidenceSource
source_observed_at?: timestamp
runtime_received_at: timestamp
fresh_until?: timestamp
sequence?: integer
frame_id?: string
map_identity?: MapIdentityEvidence
freshness: fresh | stale | unknown
integrity: content_digest | transport_authenticated | unverified
payload_schema_version: string
payload: typed evidence
qualifiers: [source_timestamp_unavailable | map_identity_unverified
           | transport_unauthenticated | non_atomic_collection
           | delivery_not_application | motion_stop_not_observed
           | partial | late_after_epoch_change]
limitations: [string]
```

沒有 source timestamp 時 `source_observed_at` 留空、freshness=`unknown`；不能用 Runtime
收到時間冒充 sensor timestamp。Vendor map label不得填入cryptographic `MapIdentity`。
只有相同command ID、accepted safety epoch、deadline內且通過freshness/map contract的
Evidence，才能支撐Completion Contract。

### `StopEnvelope` and `StopView`

```text
StopEnvelope:
  api_version: string
  client_id: string
  source_surface: string
  idempotency_key: string
  robot_id: string
  reason: operator | policy | watchdog | runtime_shutdown

StopView:
  stop_id: string
  safety_epoch: integer
  accepted_sequence: integer
  preempted_command_id?: string
  replayed: boolean
  request_accepted: boolean
  cancel_requested: boolean
  cancel_acknowledged: boolean | unknown
  goal_terminal: boolean | unknown
  zero_velocity_command_published: boolean | unknown
  zero_velocity_observation: observed | not_observed | unknown
  evidence_refs: [EvidenceReference]
  operator_motion_stop_observation?: EvidenceReference
  terminal: boolean
  limitations: [string]
```

`stop()` 必須先原子提高 safety epoch，再撤銷 approvals/leases/queued commands，最後執行
Adapter stop。它不等待一般 queue、不呼叫模型、不要求 approval，也不因caller持有舊
safety epoch而拒絕。Caller timeout只限制等待response，不得撤回已接受的STOP。
`operator_motion_stop_observation`必須包含observer/source/time，且只是操作員觀察，不是
physical safety certification。

## Hidden internal model

Runtime implementation 內部保留：

- durable event journal 與 read projections；
- idempotency ledger；
- per-robot effectful command lease、monotonic fencing token與renewal；
- safety epoch；
- approval exact-action binding；
- existing Navigation Gateway、RunStore/TaskReceipt/Audit integration；
- Adapter action UUID/handle、retry、watchdog與cleanup；
- evidence storage/redaction；
- HTTP/SSE reconnect/replay。

Edge Adapter 每次 effectful request 也必須驗證 safety epoch與fencing token，拒絕 delayed
packet。只有 central lock、沒有 Edge fencing，無法阻止 STOP 後晚到的網路 command。

## Internal Adapter seam

概念上的 internal port：

```text
snapshot() -> AdapterSnapshot
prepare(TypedCapabilityCommand, ExecutionFence) -> PreparedCommand
execute(PreparedCommand, EventSink) -> AdapterTerminalEvidence
stop(StopFence, EventSink) -> AdapterStopEvidence
close() -> None
```

這不是 public wire Interface。`IsaacNav2Adapter`、`InMemoryAdapter` 與未來
`NXDogEdgeAdapter` 實作它。Site validation、approval與 Task Outcome 不得複製到各
Adapter。

## Invariants

1. 每個 robot/domain 同時最多一個 effectful command lease。
2. read-only task 可依 policy 並行，但不得取得 motion handle。
3. v0不提供hidden effectful queue；lease busy時回`LEASE_BUSY`。
4. effectful accept 需 current safety epoch、未過期 deadline、valid capability/site、
   exact approval 與 available Adapter。
5. Runtime啟動與STOP都先提高epoch；STOP再執行side effects。
6. Edge 以 fencing token 拒絕 stale/delayed effect。
7. HTTP/SSE disconnect 不取消 task。
8. event sequence 在同一 runtime continuity 單調遞增；delivery at-least-once，
   client依 `sequence` replay與deduplicate。`event_id`只作tracing identity。
9. replay request 早於 retention floor 時回 `REPLAY_GAP`/`SnapshotRequired`；client
   重新取得 authoritative snapshot，不自行補事件。
10. terminal state single-assignment；late result只能成為 audit evidence，不能翻轉 outcome。
11. terminal event必須晚於其引用的全部Evidence event。
12. success 一律由 Completion Contract + Evidence 判定。
13. raw secret、raw provider text與 server-held action digest不進 browser/event/receipt。
14. vendor transport success 不自動提升成 physical success。

## Stable error taxonomy

```text
UNSUPPORTED_PROTOCOL
UNSUPPORTED_CAPABILITY_SCHEMA
AUTH_FAILED
ROBOT_NOT_FOUND
RUNTIME_READ_ONLY
STALE_SAFETY_EPOCH
LEASE_BUSY
LEASE_FENCED
IDEMPOTENCY_CONFLICT
DEADLINE_EXPIRED
APPROVAL_REQUIRED
APPROVAL_DIGEST_MISMATCH
POLICY_BLOCKED
SITE_MISMATCH
ADAPTER_UNAVAILABLE
EVIDENCE_INSUFFICIENT
REPLAY_GAP
INTERNAL_ERROR
```

HTTP status 是 transport mapping，不是 product failure taxonomy。

## Security baseline

- loopback first：generated bearer token、least scope、0600 secret storage。
- LAN deployment：TLS + explicit client/robot identity；不得 plain HTTP anonymous exposure。
- credential rotation 不得中斷 STOP path。
- request limits、schema `extra=forbid`、bounded payload/stream/replay。
- access log、TaskReceipt與Evidence均使用 redacted representation。
- Runtime 不跟隨 vendor redirect、不使用 ambient proxy。

## ADR 0006 amendment proposal

ADR 0006 direction不需推翻，但 implementation 前應補一個獨立 accepted ADR，明確決定：

1. public Interface只有`submit`／`observe`／`stop`三個task-oriented語意入口，而
   lease/event journal保持internal；
2. approval 是 Runtime-owned resource，與 exact server action綁定；
3. central lease 必須配 Edge fencing token；
4. replay gap、runtime continuity與retention contract；
5. `cancel` 與 robot-wide `stop` 分離；
6. NXDog Edge deployment、安全 transport與physical evidence gate。

這些決定 hard to reverse、wire-visible，且是 command-centric vs event/lease-centric 的真實
trade-off，符合建立新 ADR 的條件。本 assessment PR 只提出 amendment，不將其標為
Accepted。

## Proposed implementation PR sequence

1. **Protocol types + in-memory Adapter**：無 HTTP、無 motion。
2. **Authority core**：idempotency、lease、epoch、event journal、approval、STOP tests。
3. **HTTP/SSE Adapter**：loopback token、replay、disconnect、schema/version tests。
4. **Isaac/Nav2 vertical slice**：一條 typed navigate 經既有 Navigation Gateway；
   parity tests後才遷移一個 caller。
5. **NXDog Edge read-only**：RuntimeState/EvidenceEnvelope，仍無 motion。
6. **NXDog LED pipeline**：套用
   [canonical Evidence-to-outcome policy](CAPABILITY_MAPPING.md#evidence-to-outcome-policy)，
   並驗證 auth／lease／event／receipt。
7. **NXDog software stop**：stationary physical acceptance。
8. **NXDog compute-route and short navigation**：另立 motion ADR與實體 evidence。

任何一步失敗都在該 seam 收集 evidence，不用第二條 UI/vendor shortcut「先讓狗動」。
