# Robot Runtime Protocol v0 Draft

> 狀態：Proposed／documentation-only
>
> 本文件不實作 HTTP server、NXDog motion 或 production migration。

## Decision under assessment

採用：

> **簡潔、task-oriented 的公開 Interface，搭配 event-centric 的內部實作。**

一般 caller 只理解 Task、Approval、Evidence、Stop 與 Task Outcome，並只使用
`submit`、`observe`、`stop` 三個語意入口。Command lease、
fencing token、safety epoch、idempotency ledger、timing budgets、event journal 與
Adapter lifecycle 由 Robot Runtime Authority module 隱藏。這讓 Runtime 成為深 module：
Interaction surfaces 取得高 leverage，而 distributed-control complexity 保持 locality。

LLM／Fast Path 只負責把 operator intent 轉成 registered Capability 與 typed input。一旦
Runtime 接受 Task，Approval resource、整段 effectful Task 的lease、Capability execution、
Evidence evaluation、Completion Contract、Task Outcome 與Receipt都只有Robot Runtime
Authority能改變。Workflow Capability另由Authority建立mutable Workflow Instance；共用
Workflow code只是immutable definition。JenAI Application不保存第二份active Workflow、
Task lifecycle或approval state。

顯式 lease management 暫不成為 public v0 Interface。只有未來出現真正需要跨多個 task
持有 lease 的 fleet owner，才考慮新增 admin-scoped Interface。

## Scope

Protocol v0 必須：

- 支持現有 Isaac/Nav2 Adapter 與 in-memory Executor ports；
- 支持與 Authority co-located 的未來 NXDog Adapter，不把 vendor transport洩漏給 caller；
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

## Execution ownership and v0 deployment

### One mutable owner

```text
Intent Layer
  natural language／Fast Path／Capability selection
        │ typed high-level Task
        ▼
Robot Runtime Authority
  Approval resource／Workflow Instance／lease／epoch／journal
  Completion Contract／Task Outcome／Receipt
        │ admitted typed execution step
        ▼
Capability Executor
  NavigationGateway／PlatformCommandPort／ObservationPort
```

Intent Layer 可以建立 typed request，但不得執行 normal Workflow steps、保存 pending
Approval、持有 robot lease 或判定 terminal outcome。Effectful Task 從 accepted／
awaiting-approval 開始到 terminal cleanup 完成，均由 Authority 持有同一個 lifecycle；
STOP 終止整個 Task／Workflow Instance，而不只取消目前 waypoint。

### Co-located Authority and Adapter in v0

Public `submit`／`observe`／`stop` 只存在於 caller → Authority。Authority 與 platform
Adapter 在 v0 是同一 deployment unit；internal ports 不使用 public Runtime protocol：

```text
Isaac v0
  DGX Spark: caller + Authority + Capability Executor + Isaac/Nav2 Adapter

NXDog v0
  DGX caller
      │ authenticated public Runtime protocol
      ▼
  robot-side companion／LAN sidecar:
      Authority + Capability Executor + NXDog Adapter
      │ local ROS 2 Foxy／vendor interfaces
      ▼
  NXDog
```

未來若 Authority 與 robot-side Adapter 必須跨網路分離，需另立 Edge Control Protocol 與
ADR，至少定義 authority generation、boot identity、fencing、deadline budget、
prepare/execute/cancel/robot-wide stop、Evidence stream、network partition、takeover 與
restart reconciliation。v0 不以 `submit`／`observe`／`stop` 冒充這條第二層 wire
Interface，也不宣稱已解決 remote stale-authority fencing。

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
claims: CallerClaims
last_runtime_id?: string
last_event_sequence?: integer
```

```text
CallerClaims:
  client_instance_id?: string
  source_surface?: tui | webui | mcp | cli | daemon | test
```

`CallerClaims` 是不可信 presentation／diagnostic metadata。它不能授予 scope、決定
policy、成為 audit actor 或建立 idempotency namespace。

### `AuthenticatedPrincipal`

`AuthenticatedPrincipal` 由 HTTP/TLS authentication Adapter 建立，不存在 request body：

```text
principal_id: string
credential_id: string
allowed_scopes: [string]
permitted_robot_ids: [string]
authenticated_transport: tls | loopback_token | other
transport_bound_client_id?: string
```

Authorization、Approval resolver identity、audit actor 與 idempotency scope只使用這個
transport-owned context。`transport_bound_client_id` 必須由 credential／TLS identity導出，
不得抄用payload中的`client_instance_id`。`source_surface`永遠只保留為caller claim，不能
升格為authenticated identity或trusted classification。

### `RuntimeDescriptor`

```text
api_version: string
runtime_id: string
boot_id: string
authority_generation: integer
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

`runtime_adapter_id` 可顯示 `isaac_nav2` 或 `nxdog_ros2`，但 public command 不得包含
ROS/vendor operation name。

### `RuntimeHealth`

```text
status: available | degraded | unavailable | read_only
observed_at: timestamp
reconciliation_state: pending | running | complete | failed
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
claims: CallerClaims
idempotency_key: string
expected_safety_epoch: integer
```

`ResolveApproval`與`CancelCommand`沿用既有Task的server-owned timing contract，不得藉由
新request延長execution或cleanup budget。

Client wall clock 不作安全決策。Runtime transport 在 request 到達時建立 monotonic
admission timer，依 server policy 驗證 request freshness，再以 Capability policy clamp
`StartCapability.requested_timeout_ms`。以下 budget 互相獨立，不得彼此借用或推論：

- **request freshness budget**：從 server 收到 request 到 admission 完成；caller 不可設定；
- **approval expiry**：從 Approval 建立到 resolver decision；不消耗 execution budget；
- **execution budget**：從 TaskStarted 到 normal execution terminal；由 requested value 與
  Capability maximum 的較小值決定；
- **postcondition Evidence window**：normal execution terminal後的bounded read-only取樣窗，
  用於取得final pose、velocity或其他Completion Contract Evidence；它不得延長或重新開啟
  effectful execution；
- **cleanup／cancel budget**：從 cancel、failure或timeout開始，直到 bounded cleanup；
- **STOP budget**：server-owned safety policy；caller timeout只限制等待 response。

Idempotent replay沿用第一次 acceptance 的 server budget與時間，不延長任何 budget。

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
  requested_timeout_ms: positive integer
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
Workflow Capability 被接受時，Runtime建立唯一 mutable Workflow Instance；atomic
Capability則建立同樣由 Runtime-owned 的單步 Task lifecycle。

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
accepted_at_server_time: timestamp
approval_expires_at_server_time?: timestamp
execution_timing: PendingApprovalTiming | StartedExecutionTiming | NotApplicableTiming
accepted_sequence: integer
replayed: boolean
```

```text
PendingApprovalTiming:
  kind: pending_approval
  accepted_execution_budget_ms: positive integer
  accepted_postcondition_evidence_window_ms: positive integer
  accepted_cleanup_budget_ms: positive integer

StartedExecutionTiming:
  kind: started
  accepted_execution_budget_ms: positive integer
  accepted_postcondition_evidence_window_ms: positive integer
  accepted_cleanup_budget_ms: positive integer
  started_at_server_time: timestamp
  deadline_server_time: timestamp

NotApplicableTiming:
  kind: not_applicable
```

若 Task 等待 Approval，Runtime 先回 `PendingApprovalTiming`；Approval等待不消耗execution
budget。Runtime真正開始執行時，第一筆`TaskStarted` event與其後的`TaskView`必須包含同一份
`StartedExecutionTiming`，其中`deadline_server_time`是由server monotonic budget投影的
authoritative wall-clock deadline。若 Task 無需 Approval並立即開始，首次
`CommandAccepted`直接回`StartedExecutionTiming`。Rejected／blocked-before-start request
使用`NotApplicableTiming`，不得虛構execution deadline。

Normal execution terminal時，Runtime必須以`TaskProgressed`發布server-derived
`postcondition_deadline_server_time`並開啟read-only Evidence window。Cancel、failure或
timeout則發布`cleanup_deadline_server_time`；cleanup期間取得的cancel acknowledgement與
stationary Evidence只能支撐`cancelled`／`failed`／`unavailable`等相符outcome，不能把已
timeout的Task翻成`succeeded`。

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
workflow_instance_id?: string
robot_id: string
capability_id: string
status: proposed | awaiting_approval | accepted | running
      | stopping | completed | blocked | failed | cancelled
outcome?: TaskOutcome
failure_code?: string
current_step?: string
progress?: typed progress
execution_timing?: StartedExecutionTiming
postcondition_deadline_server_time?: timestamp
cleanup_deadline_server_time?: timestamp
pending_approval?: ApprovalView
evidence_summary: [EvidenceReference]
receipt?: TaskReceiptReference
safety_epoch: integer
latest_event_sequence: integer
```

Terminal status 是 single-assignment。晚到 success 不得覆蓋 STOP/cancel 後的 cancelled
outcome。`status=running`時`execution_timing`必填，且必須與`TaskStarted` event一致；其他
狀態不得用client wall clock自行推算deadline。

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
content_digest?:
  algorithm: sha256 | other
  value: string
transport_security: authenticated | unauthenticated | unknown
source_assurance: vendor_telemetry | runtime_observed | operator_observed
                | derived | unknown
source_attestation?:
  kind: signature | certificate
  reference: string
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
`content_digest`只能偵測 content change，不能證明來源；authenticated transport也不能
證明 sensor value正確；source assurance／attestation又是另一個維度。只有相同command
ID、accepted safety epoch，且在該outcome適用的execution、postcondition Evidence或
cleanup window內收到並通過freshness、map與source-assurance contract的Evidence，才能
支撐Completion Contract。Cleanup window Evidence不得支撐已timeout Task的success。
Derived Evidence必須引用其parent Evidence。

### `StopEnvelope` and `StopView`

```text
StopEnvelope:
  api_version: string
  claims: CallerClaims
  idempotency_key: string
  robot_id: string
  reason: operator
  reason_detail?: redacted string

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

`policy`、`watchdog` 與 `runtime_shutdown` 是 Runtime 內部 `StopTrigger`，不得由 public
request 自稱。External STOP 的 audit principal 來自 authenticated transport，不來自
`claims` 或 `reason_detail`。

## Hidden internal model

Runtime implementation 內部保留：

- durable event journal 與 read projections；
- idempotency ledger；
- per-robot effectful command lease、durable monotonic fencing counter與renewal；
- safety epoch；
- Approval exact-action binding 與 mutable Workflow Instance；
- Completion Contract evaluation、Task Outcome single-assignment與Receipt；
- Capability Executor、existing Navigation Gateway、RunStore/Audit integration；
- Adapter action UUID/handle、retry、watchdog與cleanup；
- evidence storage/redaction；
- HTTP/SSE reconnect/replay。

Co-located Adapter 每次 effectful execution仍接收 opaque ExecutionContext，拒絕舊
authority generation、safety epoch或fencing token的delayed work/callback。這是 internal
execution contract，不是 Authority→Edge wire protocol。

## Capability Executor and internal ports

Authority 只使用一個 Runtime-owned `CapabilityExecutor` role：

```text
CapabilityExecutor
  snapshot(SnapshotRequest, ObservationContext) -> ObservationSnapshot
  prepare(TypedCapabilityStep, ExecutionContext) -> PreparedCapabilityStep
  execute(PreparedCapabilityStep, ExecutionContext, EventSink)
  stop(StopContext, EventSink)

  dependencies:
    NavigationGateway   # navigation／compute-route／movement only
    PlatformCommandPort # indicator／posture／charging non-navigation writes
    ObservationPort     # health／pose／velocity／map／battery Evidence
```

`snapshot` 是經 `ObservationPort` 的唯讀 fresh-state projection；`prepare` 只做 adapter
readiness、schema 與 correlation 準備，不執行 effect，也不處理 policy 或 Approval。它回傳的
`PreparedCapabilityStep` 必須綁定 request digest 與 `ExecutionContext`，且 `execute` 對任何
generation、safety epoch、fencing token 或 digest 不一致 fail closed。

`NavigationGateway` 維持既有 navigation-only responsibility，不擴張成 platform God
object。`PlatformCommandPort` 只接受 allowlisted platform-neutral commands，不接受 raw
vendor path／sport string。`ObservationPort` 是 read-only、source-attributed interface。
Concrete Isaac／NXDog implementations在同一 deployment unit內滿足所需 ports；lifecycle
`close()` 留在 composition root，不成為 Capability Interface。

Capability Executor與ports只回snapshot／prepared step／TaskEvent／Evidence；
Site／policy admission、Approval、Workflow sequencing、Completion Contract與Task Outcome只存在於
Authority。

## Startup reconciliation and authority continuity

每次 Authority process啟動或取得deployment ownership時，必須依序：

1. 取得唯一 local Runtime ownership，載入 durable runtime ID、event journal、
   idempotency ledger、safety epoch、authority generation、next fencing counter與
   non-terminal Tasks。
2. 在 effectful admission 開啟前，以單一 durable transition遞增authority generation與
   safety epoch、產生新`boot_id`，並append `SafetyEpochAdvanced(reason=startup)`。
3. invalidate pending Approvals；舊 lease不得直接恢復；每次startup takeover與新effectful
   lease都先durable increment fencing counter，token不得跨restart重用。
4. 透過ObservationPort讀取robot state、velocity與可取得的active vendor goal／handle。
5. 未知或orphaned work不得auto-resume；以新fence執行bounded provider-free
   cancel／STOP／cleanup。
6. 依fresh Evidence把舊Task收斂到既有`cancelled`、`unavailable`或`failed`；不建立新的
   recovery-success outcome，也不重新dispatch ambiguous command。
7. reconcile durable event／receipt projection；只有完成後才可宣告`available`。

Reconciliation期間 `observe` 維持可用，Runtime Health只能是`read_only`、`degraded`或
`unavailable`，effectful `submit`回`RUNTIME_RECONCILING`，STOP永遠可接受。相同
idempotency key在restart後仍回既有ack／terminal truth；不確定dispatch不得重送。
`runtime_id`與event sequence在durable continuity內延續、`boot_id`更新；若journal
continuity遺失，建立新runtime identity並要求client取得fresh snapshot。舊generation的
callback只可記為`late_after_epoch_change` audit Evidence，不得改變terminal outcome。

## Invariants

1. 每個 robot/domain 同時最多一個 effectful Task lease；Authority從 accepted／
   awaiting-approval 到 terminal cleanup 全程管理它。
2. read-only task 可依 policy 並行，但不得取得 motion handle。
3. v0不提供hidden effectful queue；lease busy時回`LEASE_BUSY`。
4. effectful accept 需 current safety epoch、有效server timing budget、valid
   capability/site、exact approval 與available Executor port。
5. Runtime啟動先完成reconciliation；STOP先提高epoch，再執行side effects。
6. Co-located execution以authority generation／epoch／fencing token拒絕stale work。
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
15. AuthenticatedPrincipal是authorization、audit與idempotency的唯一身分來源；caller
    claim不授權。
16. Authority是Approval、Workflow Instance、Completion Contract、Task Outcome與Receipt
    的唯一mutable owner；Executor／Adapter只回Events與Evidence。

## Stable error taxonomy

```text
UNSUPPORTED_PROTOCOL
UNSUPPORTED_CAPABILITY_SCHEMA
AUTH_FAILED
ROBOT_NOT_FOUND
RUNTIME_READ_ONLY
RUNTIME_RECONCILING
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
- transport Adapter建立AuthenticatedPrincipal；payload的client ID／source surface只是claim。
- scope明確限制robot與operation；caller的source surface不成為trusted identity，且caller
  不能自稱watchdog／shutdown。
- credential rotation 不得中斷 STOP path。
- request limits、schema `extra=forbid`、bounded payload/stream/replay。
- access log、TaskReceipt與Evidence均使用 redacted representation。
- Runtime 不跟隨 vendor redirect、不使用 ambient proxy。

## ADR 0006 clarification and follow-up ADR scope

本PR同步澄清ADR 0006的single-owner、co-location、principal、timing、Evidence與startup
方向。下列wire-visible細節在production implementation前仍需以獨立accepted ADR固定：

1. Intent Layer只選typed Capability；Runtime唯一擁有Approval、Workflow Instance、
   Completion Contract、Task Outcome與Receipt；
2. public Interface只有`submit`／`observe`／`stop`三個task-oriented語意入口，而
   lease/event journal保持internal；
3. v0 Authority與Adapter co-located；remote Authority→Edge需獨立protocol／ADR；
4. AuthenticatedPrincipal與caller claims、scope及audit contract；
5. requested timeout的server clamp，以及request／approval／execution／cleanup budgets；
6. Evidence content digest、transport security、source assurance／attestation分離；
7. startup reconciliation、durable generation／epoch與fencing continuity；
8. CapabilityExecutor使用navigation、platform-command與observation三種internal ports；
9. replay gap、runtime continuity、retention，以及task cancel與robot-wide STOP分離；
10. NXDog vendor contract、安全 transport與physical evidence gate。

這些wire細節hard to reverse，且是command-centric vs event/lease-centric的真實
trade-off，符合建立新ADR的條件。本assessment不把logical schema冒充final
Pydantic／OpenAPI contract。

## Proposed implementation PR sequence

1. **Protocol types + in-memory Executor ports**：無 HTTP、無 motion。
2. **Authority core**：Runtime-owned Workflow/Approval/outcome、idempotency、lease、epoch、
   event journal、startup reconciliation與STOP tests。
3. **HTTP/SSE Adapter**：loopback token、replay、disconnect、schema/version tests。
4. **Isaac co-located vertical slice**：Authority與Isaac/Nav2 Adapter同在DGX，一條typed
   navigate經既有Navigation Gateway；parity tests後才遷移一個caller。
5. **NXDog co-located read-only deployment**：Authority與NXDog Adapter同在robot-side
   companion，投影RuntimeState/EvidenceEnvelope，仍無motion。
6. **NXDog LED pipeline**：套用
   [canonical Evidence-to-outcome policy](CAPABILITY_MAPPING.md#evidence-to-outcome-policy)，
   並驗證 auth／lease／event／receipt。
7. **NXDog software stop**：stationary physical acceptance。
8. **NXDog compute-route and short navigation**：另立 motion ADR與實體 evidence。

任何一步失敗都在該 seam 收集 evidence，不用第二條 UI/vendor shortcut「先讓狗動」。
