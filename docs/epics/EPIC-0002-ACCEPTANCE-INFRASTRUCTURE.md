# EPIC-0002 — Acceptance Infrastructure

- State: Completed / Maintenance
- Type: Infrastructure

## Goal

建立足以區分產品行為、環境問題與證據不足的受控測試與 fail-closed 診斷基線。

## Deliverables

- Live Isaac HIL 與固定 Evidence contract；
- Differential Harness 與公平配對邊界；
- Motion Safety／Geometry readiness 設計與可信 `PASS`／`BLOCK` 語意；
- Stage Export／Headless 研究工具與 limitation；
- Development、Acceptance、Certification Research 的 claim boundary。

## Definition of Done

工具能保存失敗、拒絕不足 Evidence、避免把 pytest／simulation 冒充 live／physical proof，
且 accepted ADR 已將產品 Runtime、離線校準與 Acceptance 分離。Epic 完成不宣稱所有 live
commissioning 或 certification research 已完成；剩餘工作進維護模式。

## Non-goals

- 持續增加 Evidence version 或 research collector；
- 為讓 gate PASS 而放寬契約；
- 成為產品 Runtime 或 motion authority；
- 阻塞 Robot Runtime v0 的一般研究完善。

## Dependencies

[ADR 0007](../adr/0007-simulation-differential-control-arm.md)、
[ADR 0008](../adr/0008-motion-readiness-precedes-simulation-motion.md) 與既有 Navigation baseline。
