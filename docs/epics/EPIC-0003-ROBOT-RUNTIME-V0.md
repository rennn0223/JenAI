# EPIC-0003 — Natural-Language Patrol Golden Path

- Roadmap position at Constitution ratification: Current; live authorization: `docs/product/TECH_LEAD.md`
- Type: Product

## Product contract

[JenAI Golden Path Core Spec](../product/CORE_SPEC.md)

## Goal

讓操作員以自然語言提出固定巡邏，檢查 exact Plan，選擇 `Yes / Auto / No`，並讓 JenAI
透過既有 Navigation Gateway 與 Nav2 完成 `A → B → C → Dock`，最後回報可驗證的
`TaskOutcome` 與 Receipt。

## Canonical vocabulary

- `MissionDraft` 是 Intent Interpreter 產生的 typed but untrusted candidate；不得批准或執行。
- `MissionSpec` 是 Validator／Binder 產生的 immutable high-level `Task` request；v1 只允許
  `kind = patrol`。
- `ExecutionPlan` 是 deterministic compiler 從 `MissionSpec` 產生的 immutable ordered steps；
  approval 綁定它的 `plan_digest`。
- `ExecutionEngine` 是本 Golden Path 中單一 `Workflow Instance` 的執行角色；只有它持有 mutable
  progress、retry、cancel 與 terminal state。它不解讀自然語言，也不取代 `NavigationGateway`。
- `MissionOutcome` 只可作 canonical `TaskOutcome` 的 presentation name，不得建立第二套 outcome enum。

ADR 0006 仍記錄未來跨 TUI／WebUI 的 Robot Runtime Authority 方向。本 Epic 不以 durable Event Store、
startup reconciliation 或 HTTP／SSE 為前置條件；未來 Authority 可包含本 Epic 的
`ExecutionEngine`，不得建立平行 execution owner。

## Deliverables

- versioned Golden Path Core Spec 與 canonical digest／approval contract；
- effect-free Intent Interpreter boundary 與 deterministic Validator／Binder；
- immutable `MissionSpec` 與 `ExecutionPlan`；
- v1 compiler：`Navigate(A) → Navigate(B) → Navigate(C) → ReturnHome(Dock)`；
- exact Plan preview 與 `Yes once / Auto / No`；
- deterministic `ExecutionEngine`，沿用既有 Capability Executor 與 Navigation Gateway；
- waypoint-local retry-once／skip-and-continue，以及 system-failure abort policy；
- `0.15 m` position completion tolerance，v1 不要求 yaw；
- STOP／cancel pre-emption、late-success prevention、既有 `TaskOutcome` 與 immutable Receipt；
- 最小 TUI transcript 與固定 Isaac reference scenario validation。

## Definition of Done

```text
自然語言提出巡邏
→ MissionDraft
→ validate / bind 成 immutable MissionSpec
→ compile 成 exact immutable ExecutionPlan
→ 顯示 Plan 並選擇 Yes / Auto / No
→ ExecutionEngine 依序執行 A → B → C → Dock
→ CapabilityExecutor → NavigationGateway → Nav2
→ 驗證 endpoint 與任務政策
→ 既有 TaskOutcome
→ immutable Receipt
```

並且：

- LLM 不產生座標、控制 Nav2、決定 retry loop 或改寫已批准 Plan；
- `mission_digest` 排除 `mission_id`，`plan_digest` 精確綁定批准內容；
- `Yes` 只批准該 `mission_id + plan_digest + approval epoch` 一次；
- `Auto` 只匹配同 session、同 safety epoch、完全相同 `plan_digest`；
- `No` 不建立 effectful execution；
- waypoint failure 最多重試一次，仍失敗則記錄並繼續；
- navigation-system failure 停止後續步驟，不得盲目返航；
- STOP 不依賴 LLM，且 late success 不得改寫 terminal truth；
- Return Home 只證明回到 home pose，不宣稱 charging success；
- 相同 commit／scene／profile 的固定 Isaac scenario 連續 3 次完成才可合併，5/5 才可形成
  release claim。

## Non-goals

- 通用 Mission Engine、任意 DSL、plugin system 或多種 Mission kind；
- Semantic Area coverage、camera／VLM inspection 或完整 Inspection Mission；
- durable Event Store、startup reconciliation、HTTP／SSE 或多 client parity；
- NXDog motion、React WebUI rewrite、TUI redesign 或 multi-robot；
- 修改 Nav2 planner／controller、自己實作避障或發布 `/cmd_vel`；
- frozen infrastructure 或 Certification Research 擴張。

## Dependencies

[ADR 0003](../adr/0003-task-outcome-contract.md)、
[ADR 0004](../adr/0004-llm-selects-deterministic-workflows.md)、既有 Capability Registry、
Capability Executor seam、Navigation Gateway、RunStore／Task Receipt 與 reference Site／Vehicle Profile。
[ADR 0006](../adr/0006-single-high-level-http-robot-runtime.md) 是未來 migration direction，不是本 Epic
開始 transport／durability 工作的授權。
