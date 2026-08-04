# TECH_LEAD — Current Product Focus

> 這是現在式工作授權，不是長期 roadmap。

本文件是 Current Milestone 的唯一可變來源。`PRODUCT.md`、`ROADMAP.md`、`MILESTONES.md`
與 Epic 的位置文字都是 Constitution 核准時的投影，不得獨立授權或切換 Milestone。

## Current milestone

[`EPIC-0003 — Robot Runtime v0`](../epics/EPIC-0003-ROBOT-RUNTIME-V0.md)

## Focus

交付一個由 Robot Runtime Authority 完整擁有、可批准、可停止、可重啟後誠實恢復、可驗證
並能產生 Receipt 的 Task／Workflow Instance lifecycle。TUI／WebUI 可將其呈現為 MissionRun，
但不擁有第二份狀態。成功以可展示的完整生命週期衡量，不以新增類別或 protocol 文件數量衡量。

## Authorized work

- typed high-level Task、Runtime-owned Workflow Instance 與其 MissionRun projection；
- Approval resource 與 lifecycle；
- command lease、safety epoch 與 startup reconciliation；
- deterministic Workflow instance execution；
- ordered durable Runtime Events 與 projections；
- STOP／cancel、Completion Contract、Task Outcome 與 Task Receipt；
- in-memory／transport seam 及既有 Isaac Navigation Gateway parity；
- 讓既有 TUI／WebUI 讀取同一 Runtime truth 所需的最小 thin-client integration。

## Intentionally ignored

- Geometry、Motion Safety、Differential、Acceptance、Stage Export、Headless parity；
- Inspection Mission 的完整產品流程（屬 EPIC-0004）；
- NXDog effectful capability；
- React WebUI rewrite 或 TUI redesign；
- multi-robot、Certification Research 與其他 deferred work。

上述項目只有符合 Product Governance 的 minimal critical-blocker exception 才可觸及。

## Exit gate

必須完成 EPIC-0003 的可展示 Definition of Done，包含 single authority、restart honesty、
LLM-independent STOP、跨介面一致 truth 與 Isaac path parity。Definition of Done 達成後，
由 Product Owner 更新本文件，將 Current Milestone 切換至 EPIC-0004。

在該更新合併前，EPIC-0004 仍未取得工作授權；不得以「EPIC-0003 已完成」自行開始下一個
Epic，也不得留在 Runtime 抽象層繼續無界打磨。
