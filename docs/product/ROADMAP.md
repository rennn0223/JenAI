# JenAI Product Roadmap

> 對應版本:**v2.6.0**；產品順序由本文件與 Current Milestone 管理。

本文件只回答「產品現在在哪裡」。產品目的見
[`PRODUCT.md`](PRODUCT.md)，里程碑完成門檻見
[`MILESTONES.md`](MILESTONES.md)，工作授權與 Freeze 規則見
[`PRODUCT_GOVERNANCE.md`](PRODUCT_GOVERNANCE.md)。歷史版本與舊多軌規劃由 release notes、
[`PRODUCT_READINESS.md`](PRODUCT_READINESS.md) 與 Git history 保存。

## Completed

- [EPIC-0001 — Navigation](../epics/EPIC-0001-NAVIGATION.md)：共用 Navigation Gateway、
  Nav2 結果與終點證據、批准、取消及 STOP 的產品基線。
- [EPIC-0002 — Acceptance Infrastructure](../epics/EPIC-0002-ACCEPTANCE-INFRASTRUCTURE.md)：
  Differential Harness、Motion Safety、Geometry、Stage Export 與 Headless 研究工具已達
  維護模式；可信 `BLOCK` 是有效結果，不會自動開啟更多工具工作。

## Current

- Current Milestone 的唯一授權來源是 [`TECH_LEAD.md`](TECH_LEAD.md)；目前指向
  [EPIC-0003 — Robot Runtime v0](../epics/EPIC-0003-ROBOT-RUNTIME-V0.md)，交付單一 Runtime
  Authority 擁有的 Task／Workflow Instance 生命週期與跨介面一致 MissionRun projection。

## Next

- [EPIC-0004 — Inspection Mission](../epics/EPIC-0004-INSPECTION-MISSION.md)：用一條可展示的
  巡檢 vertical slice 驗證 Runtime 產生實際產品價值。

## Deferred

- [EPIC-0005 — NXDog](../epics/EPIC-0005-NXDOG.md)：Runtime 與 Inspection Mission 完成後，
  才評估 read-only Edge projection 與 effectful capability。
- React／TypeScript Operator WebUI 漸進遷移。
- Geometry calibration implementation、Headless parity、Certification Research、multi-robot、
  Mission marketplace 與其他研究型擴張。

Deferred 不表示拒絕；它表示目前沒有工作授權。完整佇列與解凍依據見
[`DEFERRED_WORK.md`](DEFERRED_WORK.md)。
