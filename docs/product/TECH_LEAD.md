# TECH_LEAD — Current Product Focus

> 這是現在式工作授權，不是長期 roadmap。

本文件是 Current Milestone 的唯一可變來源。`PRODUCT.md`、`ROADMAP.md`、`MILESTONES.md`
與 Epic 的位置文字都是 Constitution 核准時的投影，不得獨立授權或切換 Milestone。

## Current milestone

[`EPIC-0003 — Natural-Language Patrol Golden Path`](../epics/EPIC-0003-ROBOT-RUNTIME-V0.md)

## Focus

交付一條真實可用、可驗收的產品流程：操作員以自然語言要求巡邏，JenAI 顯示 exact Plan，
經 `Yes / Auto / No` 批准後，以確定性流程依序前往 `A → B → C → Dock`，最後形成誠實的
`TaskOutcome` 與 Receipt。成功以這條流程完整完成衡量，不以新增 Runtime abstraction、protocol
或 infrastructure 數量衡量。

## Authorized work

- `MissionDraft → MissionSpec → ExecutionPlan` 的 typed、validated、immutable contract；
- 第一個且唯一的 `patrol` Mission compiler；
- exact Plan preview 與 `Yes / Auto / No` approval binding；
- 單一 `ExecutionEngine`／Workflow Instance 擁有執行進度、有限重試、取消與 terminal state；
- 既有 `CapabilityExecutor → NavigationGateway → Nav2` 原子步驟路徑；
- waypoint-local failure 與 navigation-system failure 的 typed policy；
- STOP、late-success prevention、Completion Contract、`TaskOutcome` 與 Receipt；
- 既有 TUI 顯示 Plan、批准、進度、錯誤建議與結果所需的最小整合；
- 固定 Isaac reference scenario 的產品級 Golden Path 驗證。

## Intentionally ignored

- durable Event Store、startup reconciliation、HTTP／SSE 與跨介面 Runtime migration；
- PR #157 的 Authority candidate implementation；
- Geometry、Motion Safety、Differential、Acceptance、Stage Export、Headless parity；
- Semantic Area、camera／VLM inspection、coverage 與完整 Inspection Mission；
- 通用 Mission DSL、plugin framework、Delivery／Escort／Inventory 等其他 Mission kind；
- NXDog effectful capability、React WebUI rewrite、TUI redesign、multi-robot 與 Certification Research。

上述項目只有符合 Product Governance 的 minimal critical-blocker exception 才可觸及。可信
`BLOCK` 不構成擴張 infrastructure 的理由。

## Exit gate

固定 Isaac reference scenario 必須完整展示：

```text
自然語言
→ MissionSpec
→ exact ExecutionPlan
→ Yes / Auto / No
→ A → B → C → Dock
→ verified TaskOutcome
→ Receipt
```

同一 commit／scene／profile 下，合併門檻為連續 3 次完整成功；release claim 門檻為 5/5。
另須以自動測試證明 `No` 不執行、`Auto` 不越過
`plan_digest + session_id + approval_generation` 邊界、局部失敗與系統失敗政策正確、
STOP 不依賴 LLM，且 STOP 後不接受 late success。

Definition of Done 達成後，由 Product Owner 決定切換至 Inspection Mission，或另行授權 ADR 0006
所描述的跨介面 Authority migration。在該更新合併前，不得自行開始下一個 Epic，也不得留在
Execution／Runtime abstraction 層繼續無界打磨。
