# EPIC-0004 — Inspection Mission

- Roadmap position at Constitution ratification: Next; live authorization: `docs/product/TECH_LEAD.md`
- Type: Product vertical slice

## Goal

證明 Robot Runtime 能完成一個操作員真正需要的巡檢 Mission，而不只是提供漂亮的基礎設施。

## Deliverables

- typed Factory／Site Inspection Task（product-facing MissionSpec input）；
- Inspection Area、Inspection Point、required observation 與 coverage policy；
- Approval、bounded retry／skip、Evidence attachment 與 Return Home；
- Task Outcome、coverage／skipped reason 與 operator-readable Mission Report；
- TUI／WebUI 對同一 Runtime-owned Workflow Instance 的 MissionRun progress projection。

## Definition of Done

```text
Operator 提出巡檢目標
→ typed Task（MissionSpec input）
→ Approval
→ 覆蓋必要區域
→ 收集 Inspection Evidence
→ bounded retry / skip
→ Return Home
→ Mission Report
```

報告必須誠實列出 coverage、未完成區域、原因與 Evidence；不可因 navigation terminal success
就宣稱 inspection completed。這條 vertical slice 完成後，才能宣稱 Runtime 已產生產品價值。

## Non-goals

- 未知空間 frontier exploration 或幾何全覆蓋；
- 每一步重新詢問 LLM；
- NXDog effectful integration；
- UI 全面重寫；
- multi-robot mission orchestration。

## Dependencies

EPIC-0003 完整 Definition of Done、active Site Profile、registered Inspection Areas／Points、
Observation Capability 與 Return Home contract。
