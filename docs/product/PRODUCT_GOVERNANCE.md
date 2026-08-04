# Product Governance

本文件定義 JenAI 如何維持產品焦點。它不是自動審批系統，也不建立 bot、workflow 或
governance engine。

## Authority order

```text
JenAI v1 Product Constitution
        ↓
Current Product Milestone / TECH_LEAD
        ↓
Accepted ADR
        ↓
Epic
        ↓
Design Brief
        ↓
Implementation PR
```

- **Product Constitution**：決定 JenAI 為何存在、現在做什麼及刻意不做什麼。
- **Current Milestone／TECH_LEAD**：授權目前唯一投入的產品成果。
- **Accepted ADR**：在已授權範圍內記錄不應隨意逆轉的技術決策。
- **Epic**：定義產品成果、交付物、完成門檻、non-goals 與依賴。
- **Design Brief**：說明已授權成果的實作方法。
- **Implementation PR**：落實上層決策，不得重新定義政策。

ADR 若與 Constitution 或 Current Milestone 衝突，不得默默覆蓋上層決策。工作必須停止，
由 Product Owner 決定是延後、修改 ADR、改變 Milestone 或修訂 Constitution。

## Roles

- **Product Owner**：核准產品方向、Current Milestone、Freeze 例外及 Constitution 變更。
- **Tech Lead function**：維護 [`TECH_LEAD.md`](TECH_LEAD.md) 的現在式範圍，拒絕失焦工作，
  並確認 Epic 的 Definition of Done。
- **SWE／AI Agent**：只實作已授權範圍；發現旁支改善時記入 Deferred queue，不自行開工。
- **Reviewer**：分別檢查產品授權、accepted ADR、spec、工程標準與證據，不以 CI 綠燈取代範圍審查。

## Infrastructure Freeze

以下子系統已進入 maintenance mode：

- Geometry Calibration
- Motion Safety
- Differential Harness
- Acceptance Infrastructure
- Stage Export
- Headless Parity
- Certification Research

Freeze 允許：

- 可重現 regression；
- security finding；
- false-success／false-PASS correctness violation；
- 有證據證明會阻塞 Current Epic 的缺陷；
- Product Owner 核准之必要 migration。

Freeze 不允許：

- 新 Evidence version、研究型 contract 或 collector；
- 新 abstraction 或「順便整理」；
- 為追求更完整而擴張工具；
- 因可信 `BLOCK` 而持續修改 gate；
- 為取得 PASS 而放寬 Evidence 或安全邊界。

例外必須符合 [`DECISION_POLICY.md`](DECISION_POLICY.md) 的 demonstrated critical blocker
證據與核准規則。一次例外只解除已證明問題，不解凍整個子系統。

## Constitution maintenance mode

本 Constitution 文件組合併後立即 Freeze。允許的修改只有：

- Product Owner 明確改變 North Star 或 Current Milestone；
- 修正文件間可證明的矛盾；
- 更新已完成 Epic 狀態；
- security／correctness 需要的最小治理澄清。

禁止為追求更完整而增加治理層、schema、bot、workflow 或自動審批。治理的成果是停止不必要
工作；若治理本身持續成為主線，即視為失焦。
