# docs — 文件索引

根目錄只保留入口型指南；其餘文件依產品、驗證、維運與設計用途分組。
若不知道該讀哪份，從 [QUICKSTART](QUICKSTART.md) 開始。

## 1. 開始使用

| 文件 | 用途 |
|---|---|
| [QUICKSTART](QUICKSTART.md) | 安裝、設定精靈、健檢、第一次對話與第一次導航 |
| [COMMANDS](COMMANDS.md) | CLI、Slash 指令、批准語意與快捷鍵速查 |
| [SUPPORT_MATRIX](operations/SUPPORT_MATRIX.md) | 確認 OS、Python、ROS 2、模擬器、載具與模型支援等級 |
| [SUPPORT](../SUPPORT.md) | 支援範圍與問題回報需要提供的資料 |

## 2. 接上 ROS 2 與機器人

| 文件 | 用途 |
|---|---|
| [ONBOARDING](ONBOARDING.md) | 裸 ROS 2 → 感測 → 建圖 → AMCL → Nav2 → 第一次導航 |
| [ISAAC_NAV2_SETUP](ISAAC_NAV2_SETUP.md) | Isaac Sim Warehouse／Leatherback 與 Nav2 啟動流程 |
| [TWIN_SETUP](operations/TWIN_SETUP.md) | 模擬分身、domain 隔離與 Twin Gate G1–G5 |
| [DEMO_SCRIPT](operations/DEMO_SCRIPT.md) | 可重複執行的 15 分鐘展示腳本 |

## 3. 開發與維護

| 文件 | 用途 |
|---|---|
| [TECHNICAL_GUIDE](TECHNICAL_GUIDE.md) | 現行架構、建置、設定、模組與擴充方式；開發者主指南 |
| [CODE_TOUR](CODE_TOUR.md) | 逐目錄與逐檔閱讀路徑 |
| [PROJECT_DIRECTION](product/PROJECT_DIRECTION.md) | 產品方向、能力邊界與功能優先序 |
| [ROADMAP](product/ROADMAP.md) | 演進軌道、技術債、里程碑與風險 |
| [UX](design/UX.md) | 現行 TUI／WebUI 互動與視覺驗收基準 |
| [DATA_LIFECYCLE](operations/DATA_LIFECYCLE.md) | 本機資料、匯出、保留、清除與解除安裝邊界 |
| [VERSIONING](operations/VERSIONING.md) | SemVer、公開介面與遷移政策 |
| [ROLLBACK](operations/ROLLBACK.md) | 升級、回滾與回歸驗收 |
| [HANDOFF](product/HANDOFF.md) | 維護交接與專案狀態 |

## 4. 驗證、安全與研究證據

| 文件 | 用途 |
|---|---|
| [TEST](validation/TEST.md) | 一般測試、CI 與驗收入口 |
| [ISAAC_HIL_ACCEPTANCE](validation/ISAAC_HIL_ACCEPTANCE.md) | Isaac route／cancel／stop／Twin HIL 驗收 |
| [EXPERIMENTS](validation/EXPERIMENTS.md) | E1–E4、HIL、B4 與 soak runbook |
| [USABILITY_STUDY](validation/USABILITY_STUDY.md) | 手動 ROS 2、Slash、自然語言的使用者研究流程 |
| [EVIDENCE_LEDGER](validation/EVIDENCE_LEDGER.md) | 正式數字、artifact 雜湊與不可延伸主張 |
| [SAFETY_CASE](validation/SAFETY_CASE.md) | 危害、防護層、驗證證據與殘餘風險 |
| [THREAT_MODEL](validation/THREAT_MODEL.md) | WebUI、MCP、Shell 與部署信任邊界 |
| [SECURITY](../SECURITY.md) | 漏洞通報與安全修補政策 |

## 5. 產品與歷史

| 文件 | 用途 |
|---|---|
| [PRODUCT_BRIEF](product/PRODUCT_BRIEF.md) | 產品定位、核心價值、Demo 與採購驗收 |
| [PRODUCT_READINESS](product/PRODUCT_READINESS.md) | 六種角色的產品化驗收矩陣 |
| [ADOPTION_MODEL](product/ADOPTION_MODEL.md) | 採用方式、責任分界與商務 gate |
| [V1_GATE](product/V1_GATE.md) | v1 驗收基準（歷史 gate，仍供追溯） |
| [archive/design](archive/design/README.md) | v0.1 架構、資料模型、功能與優先級設計歸檔 |
| [releases](releases/README.md) | 版本化 release notes |

維護者的每次改動驗收合約見根目錄 `CLAUDE.md`。
