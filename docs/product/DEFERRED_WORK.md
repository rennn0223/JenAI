# Deferred Work

此清單保存有價值但目前未授權的工作，避免「先記下」被誤解為「現在實作」。排序不是 roadmap
承諾；只有 Product Owner 修改 Current Milestone 或核准 critical-blocker exception 才能移出。

| Area | Deferred item | Reconsideration gate |
|---|---|---|
| Geometry | Headless USD extractor、Nova Carter attestation、physical geometry provenance | Current Milestone 明確授權 ADR 0008 migration |
| Motion Safety | 新 Evidence version、collector、uncertainty／collision research expansion | 可重現 false-PASS 或 Certification Research milestone |
| Differential | 正式五組 paired runs、額外診斷 contract | Product Owner 恢復 navigation diagnosis 且 motion gate 已核准 |
| Headless | ROS Bridge／Nav2 parity、GUI replacement | 有部署需求，不阻塞 Runtime v0 |
| UI | React／TypeScript Operator WebUI、TUI redesign | Runtime HTTP／SSE contract 穩定且 EPIC-0003 完成 |
| NXDog | Edge Adapter effectful commands、LED、STOP、navigation | EPIC-0003 與 EPIC-0004 完成；vendor／physical gates 已回答 |
| Platform | Multi-robot、remote Edge Control Protocol | 單機 Runtime 與 Mission 成熟後另立 Epic／ADR |
| Product | Mission marketplace、voice input、advanced analytics | 有使用者證據與新的 Current Milestone |
| Research | Certification、ground truth、完整 tamper-resistant reconstruction | 獨立 Certification Research milestone |

新增項目時只記錄問題、價值與 reconsideration gate；不得在本檔設計 implementation。
