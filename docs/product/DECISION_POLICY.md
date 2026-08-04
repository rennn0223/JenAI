# Decision Policy

本文件是每項工作開始前的人工 Decision Gate。

## Pre-work gate

依序回答：

1. 這項工作是否直接推進 [`TECH_LEAD.md`](TECH_LEAD.md) 指定的 Current Milestone？
2. 若否，是否存在 demonstrated critical blocker，且例外已由 Product Owner 核准？
3. 變更是否觸及 [`PRODUCT_GOVERNANCE.md`](PRODUCT_GOVERNANCE.md) 的 frozen subsystem？
4. 是否有更小、能解除同一 blocker 的修正？

只有第 1 題為 Yes，或第 2 題為 Yes 且範圍最小時，才能實作。其他情況停止實作，將候選
記入 [`DEFERRED_WORK.md`](DEFERRED_WORK.md)。不要先寫程式再尋找里程碑理由。

## Demonstrated critical blocker

至少需要一項可審查證據：

- 可重現 failure；
- failing regression test；
- live artifact；
- security finding；
- correctness violation；
- 已核准 migration 的明確依賴。

「看起來可以改善」、模型推論、研究完整度或可能的未來需要不是 blocker。例外必須在 PR
說明中連結證據與 Product Owner 核准，且只修復該證據所證明的問題。

## Required PR declaration

每個 PR body 人工填寫：

```text
Current milestone:
<目前 Epic；若為例外則明確標示>

Direct milestone contribution:
<這個 PR 如何直接推進目前 Milestone>

Critical-blocker exception:
None / <證據與 Product Owner 核准>

Frozen subsystem touched:
None / <必要原因與最小範圍>

Explicit non-goals:
<本 PR 刻意不處理什麼>
```

這些欄位是人工 review contract，不是機器 schema。缺少清楚答案時 PR 維持 Draft。

## Decision outcomes

- `IMPLEMENT`：直接推進 Current Milestone。
- `MINIMAL_EXCEPTION`：有證據且已核准，只解除 blocker。
- `DEFER`：有價值但現在未授權，記入 Deferred queue。
- `REJECT`：違反 Constitution、安全邊界或 accepted ADR。

Implementation PR 不得利用 `MINIMAL_EXCEPTION` 改寫產品方向、增加相鄰功能或清理無關依賴。
