# Product Milestones

本文件定義里程碑狀態與產品交付順序；每個成果的細節只在對應 Epic 定義。
State 欄是 Constitution 核准時的 roadmap projection；唯一可變的 Current Milestone 授權在
[`TECH_LEAD.md`](TECH_LEAD.md)，本表不能獨立啟動工作。

| Order | Epic | State | Product gate |
|---|---|---|---|
| 1 | [EPIC-0001 — Navigation](../epics/EPIC-0001-NAVIGATION.md) | Completed | 共用 navigation path、批准、STOP 與誠實結果基線已存在 |
| 2 | [EPIC-0002 — Acceptance Infrastructure](../epics/EPIC-0002-ACCEPTANCE-INFRASTRUCTURE.md) | Completed / Maintenance | 診斷與安全工具可 fail closed；不再主動擴張 |
| 3 | [EPIC-0003 — Robot Runtime v0](../epics/EPIC-0003-ROBOT-RUNTIME-V0.md) | Current projection | 一個可展示、唯一 Authority 擁有的 Task／Workflow Instance 完整生命週期 |
| 4 | [EPIC-0004 — Inspection Mission](../epics/EPIC-0004-INSPECTION-MISSION.md) | Next | 巡檢 vertical slice 完成 coverage、Evidence、Return Home 與 Report |
| 5 | [EPIC-0005 — NXDog](../epics/EPIC-0005-NXDOG.md) | Backlog | Runtime 與 Mission 契約先在 Isaac 成立，再評估平台擴充 |

## State rules

- `TECH_LEAD.md` 同一時間只能授權一個 Current；其 Definition of Done 未達成前，不啟動 `Next`。
- `Completed` 表示對應產品成果已達到 Epic 門檻，不表示所有相關研究或安全問題已結束。
- `Maintenance` 只接受 [`PRODUCT_GOVERNANCE.md`](PRODUCT_GOVERNANCE.md) 定義的例外。
- `Deferred`／`Backlog` 沒有實作授權；候選改善記入
  [`DEFERRED_WORK.md`](DEFERRED_WORK.md)。
- Product Owner 才能改變 Current Milestone；ADR 或 Implementation PR 不得自行改變順序。
