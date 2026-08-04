# EPIC-0003 — Robot Runtime v0

- Roadmap position at Constitution ratification: Current; live authorization: `docs/product/TECH_LEAD.md`
- Type: Product

## Goal

建立單一 Robot Runtime Authority，讓所有 interaction surfaces 對同一個 accepted Task、
Runtime-owned Workflow Instance、Approval、STOP、Evidence、Task Outcome 與 Receipt 取得一致 truth。

## Canonical vocabulary

- Product-facing `MissionSpec` means the typed high-level `Task` request; it is not a second stored task aggregate.
- Product-facing `MissionRun` is the projection of one accepted `Task` and its Runtime-owned
  `Workflow Instance`; only that Workflow Instance holds mutable execution progress.
- Product-facing `MissionOutcome` is a presentation name for the canonical `Task Outcome`.

These mappings preserve `CONTEXT.md` and ADR 0006. Implementations must not introduce a parallel
Mission lifecycle or outcome model.

## Deliverables

- typed high-level Task 與 Runtime-owned Workflow Instance／MissionRun projection；
- authenticated principal、Approval resource 與 lifecycle；
- command lease、durable safety epoch、authority generation 與 startup reconciliation；
- deterministic Workflow instance 與 Capability Executor；
- ordered Event Store、projection、Evidence、Completion Contract、Task Outcome 與 Task Receipt；
- STOP／cancel pre-emption，不依賴 LLM；
- in-memory seam 與最小 HTTP／SSE Runtime transport；
- 既有 Isaac Navigation Gateway 路徑的行為 parity；
- 既有 TUI／WebUI 對同一 Runtime truth 的最小 client integration。

## Definition of Done

以下流程可由自動測試與受控 Isaac parity evidence 展示：

```text
提交 typed high-level Task（MissionSpec input）
→ 建立 Runtime-owned Workflow Instance（MissionRun projection）
→ 要求並解析 Approval
→ Runtime 取得 command lease
→ 執行 deterministic Workflow
→ 發布有序 Event
→ 支援 STOP / cancel
→ 更新 safety epoch
→ 形成 Task Outcome（MissionOutcome presentation）
→ 保存 Task Receipt
→ TUI / WebUI 看到同一份 Runtime truth
```

並且：

- 只有一個 Runtime Authority 能修改執行狀態；
- Runtime restart 不會把未知舊任務宣稱為成功；
- STOP 不依賴 LLM，且停止後不允許 late success；
- Approval、active command 與 Outcome 不會在不同介面分叉；
- Isaac 舊路徑與 Runtime 新路徑具備可審查的行為 parity；
- 不需要 NXDog effectful capability 才能完成 v0；
- 不需要先重做 TUI 或 React WebUI。

## Non-goals

- Inspection Mission 的完整 coverage product；
- NXDog motion、remote Authority→Edge protocol 或 multi-robot；
- React WebUI rewrite、TUI redesign；
- Geometry、Motion Safety、Differential 或 Certification Research 擴張；
- 讓 Runtime 取代 Navigation Gateway、Nav2 或底層 safety controller。

## Dependencies

[ADR 0006](../adr/0006-single-high-level-http-robot-runtime.md)、既有 Capability Registry、
Navigation Gateway、RunStore／Task Receipt 與 v2.6.0 lifecycle hardening baseline。
