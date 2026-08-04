# EPIC-0005 — NXDog

- Roadmap position at Constitution ratification: Backlog; live authorization: `docs/product/TECH_LEAD.md`
- Type: Platform expansion

## Goal

以既有 Robot Runtime contract 驗證 JenAI 的平台無關性，而不是新增直通 vendor API 的第二條
execution path。

## Deliverables

- NXDog read-only Edge projection；
- Runtime-owned indicator write/read-back；
- stationary STOP contract；
- compute-route、短距離 navigation 與 navigation STOP 的分階段 physical acceptance；
- vendor gaps、licensing、authentication 與 evidence limitations 的正式處理。

## Definition of Done

同一套 Runtime task、Approval、lease、Events、STOP、Outcome 與 Receipt contract 能在 Isaac
與 NXDog Adapter 上成立，且 effectful capability 依 physical acceptance phase 逐步開放。

## Non-goals

- Agent／TUI／MCP 直接呼叫 vendor Flask `/navigate`；
- 將 HTTP 200、accepted 或 cancel requested 宣稱為硬體成功／停止；
- 在 Runtime v0 與 Inspection Mission 前啟動 motion；
- 將 simulation Evidence 外推為 physical safety。

## Dependencies

EPIC-0003、EPIC-0004、[ADR 0005](../adr/0005-nxdog-http-is-an-observation-only-runtime-adapter.md)、
[ADR 0006](../adr/0006-single-high-level-http-robot-runtime.md)、vendor written answers 與
physical acceptance authorization。
