# PRODUCT_READINESS — 六角色產品化驗收矩陣

> 本文件把工程師、PM、經營者、教授、業務與買家的要求轉成可驗證的交付條件。
> 狀態只能使用 `PASS`、`PARTIAL`、`OPEN`；沒有證據不得標成完成。

## 產品定位與邊界

JenAI v2.1.0 候選是一個**受監督、具執行邊界的 ROS2 高階決策與工作流代理**。它使用自然語言
或 Slash 指令理解任務、查詢 live ROS graph、選擇已註冊能力、經批准與可選 Twin Gate
驗證後呼叫 Nav2／ROS2 API，最後以 odom、Nav2 result 與 audit 回報結果。

它不是底層控制器、未知空間 frontier explorer、經功能安全認證的產品，也尚未具備常駐的
`perceive → decide → rehearse → act → feedback` 自主迴圈。`/explore` 是已知儲存點位的
有界巡遊，不是 SLAM 探索。

新版 workflow 會為 private release 產生 CycloneDX SBOM 與 `SHA256SUMS`；只有這些 assets 實際出現在該 GitHub Release 時才視為已發布，且 private path 不會產生或宣稱 GitHub artifact attestations。

下表以 v2.0.1 tag 指向的 main@`79a295a` 為固定 revision；[PR #106](https://github.com/rennn0223/JenAI/pull/106)、
[main CI](https://github.com/rennn0223/JenAI/actions/runs/29654418747)、[Supply Chain](https://github.com/rennn0223/JenAI/actions/runs/29654418730) 與
[Release run](https://github.com/rennn0223/JenAI/actions/runs/29654500264) 均成功，[v2.0.1 Release](https://github.com/rennn0223/JenAI/releases/tag/v2.0.1)
已發布 wheel、sdist、matching constraints、CycloneDX SBOM 與 `SHA256SUMS`。本機 fingerprint
證據保留為防漂移輔助；遠端 revision、tag、run 與實際下載資產才是發布事實來源。

## 六角色驗收矩陣

| ID | 角色 | 驗收條件 | 目前證據 | 狀態 | 關閉條件 |
|---|---|---|---|---|---|
| ENG-1 | 工程師 | 無 ROS 的單元／整合測試、lint、三版本 CI、wheel 冒煙全綠 | v2.1.0 候選本機 675/675、Ruff、build／隔離 wheel smoke 全綠；遠端三版本 CI、build 與 audit/SBOM 只在本版 PR 實際通過後成立。v2.0.2 PR #108 是上一版獨立證據 | PASS | 後續每個 PR／main revision 持續維持三版本 CI 與 safety-chain gate |
| ENG-2 | 工程師 | ROS／Isaac 關鍵路徑可自動回歸，不只人工 TUI 實測 | clean `d942130…855` HIL 通過完整 scan metadata gate、兩條 route、Nav2 cancel acknowledgement 與 zero-drift software halt；clean `cc6d217…f6e` 另保留 0-goal fail-closed 與 10/10 固定 route legs；Twin 同 domain 明記 skip | PASS | 每個候選 release 維持同 protocol 回歸；separated-domain Twin verdict 另由安全／部署 gate 驗收 |
| ENG-3 | 工程師 | 核心模組職責可維護 | `app.py` 1,789→1,245；批准生命週期抽為 `approval_flow.py`，已批准工具執行抽為 `direct_execution.py`，另有 `catalog.py`、location mixin 與 stdlib-only bridge protocol；完整回歸全綠 | PASS | 新功能維持 approval policy／execution／rendering 邊界與行為測試 |
| ENG-4 | 工程師 | 依賴與供應鏈可稽核 | main Supply Chain 與 v2.0.1 Release run 全綠；已發布 wheel、sdist、matching constraints、CycloneDX SBOM 與自足 `SHA256SUMS`，並由實際下載資產重驗 checksum／install／doctor／uninstall。Private path 明確不宣稱 attestations；public path 缺任一 attestation 會 fail closed | PASS | 每週持續掃描；若未來公開，另以實際 attestation bundles 關閉 public provenance gate |
| PM-1 | PM | ICP 與主要任務明確 | 主要 ICP：已有 ROS2/Nav2 的研究室與機器人開發團隊；主要任務：高階任務觸發與 ROS 開發輔助 | PASS | 新功能必須服務主要任務之一 |
| PM-2 | PM | v2 與 post-v2 承諾分開 | v2.1.0 候選仍是受監督工作流代理；唯讀快速路徑不等於 M6 常駐決策迴圈，後者仍移至 post-v2（候選 v3） | PASS | README、論文、demo 不得把 M6 當現有能力 |
| PM-3 | PM | 新手可從安裝走到第一個成功任務 | ONBOARDING、doctor 與 responsive TUI 已有；三位使用者曾在指導下試用，但未做純冷啟動計時 | PARTIAL | ≥5 位新手只看文件完成任務並保存時間／卡點 |
| PM-4 | PM | TUI 資訊層級、鍵盤流程與窄螢幕可用 | Claude Code 風格雙欄 welcome、平面 transcript／approval、composer 與 3 級 responsive 已實作；70 項 TUI 測試全綠；使用者於 2026-07-19 明確接受目前 120×30／80×30 SVG 樣本（「UI 先這樣」） | PASS | 本版視覺封版；未來若改外觀，仍須先提供獨立樣本取得使用者批准 |
| BIZ-1 | 經營者 | 授權與發布可供外部採用 | Apache-2.0 與受控 private v2.0.1 Release 已就緒；有權限的協作者可下載完整驗證資產。Repository 仍 private、main／tag 未受保護，未授權買家仍沒有下載通道 | PARTIAL | 決定公開或具買家權限管理的受控交付通道，並啟用適當 main／tag 保護 |
| BIZ-2 | 經營者 | 有商業模式、成本與責任邊界 | `ADOPTION_MODEL`：Apache 核心＋整合／訓練／維護服務、TCO 輸入表、責任分界；現在明示無付費 SLA | PASS | 報價前以真實 pilot 工時填成本，不先造 ROI |
| BIZ-3 | 經營者 | 不依賴單一維護者 | 文件、CI 與 HANDOFF 的第二維護者 release／rollback／Isaac 故障演練已具可執行步驟與 artifact 欄位，但尚未由第二人獨立完成 | PARTIAL | 第二位維護者在作者介入 0 次下完成一次 release、rollback 與 Isaac 故障演練 |
| RES-1 | 教授 | 研究問題、方法、證據與限制一致 | `EVIDENCE_LEDGER` 分開 E2 derived／observed、B4、clean HIL-FS2、Hero10 與 dirty TUI-NL1；Hero 首次 pose-feed fail-closed 亦保留。較早 E1、E2-C、E3、E4、B4 仍缺 execution revision／歷史 doctor，B4 也無 incident 欄或獨立觀察者 | PARTIAL | 重跑或找回歷史 metadata；補獨立事件觀察。新結果只能追加且保留失敗，模擬 HIL 不外推實體 |
| RES-2 | 教授 | 「降低記憶負擔／提升效率」有對照資料 | 已有隨機化六序列 Williams 條件排程、匿名 trial 計時、失敗保留與分析骨架；目前排程器尚未實作 A／B／C 等價任務變體的交叉平衡，僅可作內部排練，也尚未招募受試者 | OPEN | 先實作並測試等價任務變體平衡，完成樣本數依據與倫理程序後，再執行三條件研究，報成功率、時間、錯誤與查詢次數 |
| RES-3 | 教授 | 跨載具主張符合證據 | Vehicle Profile 與高階 API 支持介面可移植；物理泛化未驗證 | PARTIAL | 至少一個非 Ackermann 平台完成固定 PoC 任務集 |
| SALES-1 | 業務 | 三分鐘內可穩定展示核心價值 | clean Hero 固定序列為 10/10 Nav2 route legs，另有 1 次自然語言單-goal 成功；但前者不是 10 次完整三分鐘 demo／LLM 試驗，後者執行時 dirty，且最慢單 leg 111.668 s | PARTIAL | 同 clean commit／模型／場景跑 10 次完整 demo 序列，≥9 次在三分鐘內成功並保留所有失敗 |
| SALES-2 | 業務 | 有可引用的 ROI／案例 | 效率研究 protocol 與分析工具已可執行，但仍沒有受試資料、節省時間、導入成本或客戶案例 | OPEN | 完成效率研究並寫一頁案例研究 |
| SALES-3 | 業務 | 不過度承諾 | 誠實回報與限制文件已有 | PASS | 不說「通用實體安全」「認證」「未知空間自主探索」 |
| BUY-1 | 買家 | 能直接安裝、啟動、診斷與移除 | v2.0.1 已發布；維護者從該 Release 重新下載全部 assets，四項 checksum 全部通過，並以 matching constraints 完成雙入口、version／help、預期無設定 doctor 與 uninstall。尚缺非維護者 fresh-machine 冷啟動；未授權者也無 private repo 下載權限 | PARTIAL | 由非維護者在 fresh machine 只照 README 完成下載、驗證、onboard、doctor 與移除 |
| BUY-2 | 買家 | 資安與部署邊界清楚 | `SECURITY`、`THREAT_MODEL`、`SUPPORT` 已明示 `/shell`、DDS、雲端資料外送與功能安全限制；v2.0.1 已發布逐版 CycloneDX SBOM／checksum。User-owned private repo 不支援 GitHub attestations，文件與 workflow 均未宣稱該證據層 | PASS | 若未來公開，再以 attestations 補強；每個企業場域仍須另做 deployment threat review |
| BUY-3 | 買家 | 有支援載具／ROS／模型矩陣與驗收方式 | `SUPPORT_MATRIX` 分 Validated／Supported／Experimental／Planned；`VEHICLE_POC` 固定驗收 | PASS | 新組合有 artifact 才能升級等級 |
| BUY-4 | 買家 | 有 SLA、升級、回滾與事故處理 | `SUPPORT` 明示目前 best-effort／無 SLA；`ROLLBACK` 涵蓋 wheel、source、Isaac 與實體回歸；安全通報另見 `SECURITY` | PASS | 若推出付費方案，須另簽回應時段與嚴重度 SLA |
| BUY-5 | 買家 | 本機敏感資料有可稽核的生命週期與最小權限 | `JenAI data status／harden／export／prune／purge`、symlink／hardlink 防護、0600 atomic write 與秘密遮罩已有測試；只讀盤點仍發現既有資料需要 harden，因權限調整可能影響既有群組分享，尚未在使用者未同意下改動 | PARTIAL | 使用者明確 opt-in 後執行 harden，再以只讀 status 證明無不安全項目並保存不含秘密的摘要 |

## 2026-07-19 六角色第二輪內部複審

上一版先以本機 frozen snapshot 驗 Ruff、597/597、0 warnings、安全鏈 coverage 93% 與
封裝生命週期；之後 PR #106、main@`79a295a`、Supply Chain 與 Release run 又在相對應
revision 重跑 gate，v2.0.1 assets 亦由 GitHub Release 重新下載完成 checksum 與隔離
install／doctor／uninstall。這些證據只屬 v2.0.1；不能回填成歷史 v1.1.4 已具有
constraints、逐版 SBOM 或 provenance，也不能取代不同 revision 的 HIL 或使用者研究。v2.0.2 候選另有本機 652/652、clean `d942130…855` HIL-FS2、clean `cc6d217…f6e` Hero10，以及 PR #108 三版本 CI／build／audit-SBOM 全綠；它們不能回填到 v2.0.1 release assets，也不能單獨證明 merged revision 或 v2.0.2 release assets，後兩者須由 GitHub 上實際可觀察的紀錄另驗。

以下 verdict 的 `SATISFIED_WITH_EXTERNAL_GATES` 表示該角色在本輪審查範圍內沒有再提出
可由目前 worktree 單獨修正的內部 blocker。它**不會覆寫**上方矩陣的 `PASS`／`PARTIAL`／
`OPEN`：每一列仍須依自己的證據 gate 關閉，外部條件在取得 artifact 前一律不算通過。

| 角色 | 第二輪 verdict | 審查範圍／證據參照 | 仍未通過的外部 gate |
|---|---|---|---|
| 工程師 | `SATISFIED_WITH_EXTERNAL_GATES` | PR #108 三版本 CI／build／audit-SBOM、本機 wheel smoke、clean `d942130…855` 完整 FullScan／cancel evidence，以及 clean Hero 10/10 legs | separated-domain Twin verdict；每版 self-hosted workflow artifact |
| PM | `SATISFIED_WITH_EXTERNAL_GATES` | `README.md`、`docs/product/PRODUCT_BRIEF.md`、`docs/design/UX.md`、responsive TUI 測試與已接受的 120×30／80×30 樣本 | ≥5 位新手冷啟動紀錄；未來視覺變更仍須先看樣本 |
| 經營者 | `SATISFIED_WITH_EXTERNAL_GATES` | `LICENSE`、`SUPPORT.md`、`docs/product/HANDOFF.md`、`docs/operations/ROLLBACK.md`、v2.0.1 Release 與 published-release mutation guard | repo 仍 private／unprotected；未授權買家無下載通道；第二維護者零介入演練 |
| 教授 | `SATISFIED_WITH_EXTERNAL_GATES` | `EVIDENCE_LEDGER`、`USABILITY_STUDY`、`SAFETY_CASE`、HIL-FS2 與 Hero10；derived／observed／sim-only 限制已對齊 | prospective 使用者研究、獨立事件觀察與非 Ackermann 實體 PoC；歷史 metadata 缺口仍在 |
| 業務 | `SATISFIED_WITH_EXTERNAL_GATES` | `PRODUCT_BRIEF`、`DEMO_SCRIPT`、HIL-FS2 與 Hero 10/10 固定 route legs；核心 route／Dock／cancel 已有正式模擬樣本 | 10 次完整三分鐘 demo／NL 試驗尚未執行；效率結果與可引用案例 |
| 買家 | `SATISFIED_WITH_EXTERNAL_GATES` | `docs/QUICKSTART.md`、`docs/operations/ROLLBACK.md`、`docs/operations/SUPPORT_MATRIX.md`、`docs/operations/DATA_LIFECYCLE.md`、v2.0.1 Release 與實際下載 wheel lifecycle | 非維護者 fresh-machine 驗收；未授權者的交付通道；本機 data harden 仍等待使用者 opt-in |

本輪沒有已知、可由目前 worktree 繼續修正的內部 blocker；這不是 product-ready 宣告。
目前外部事實更新為：clean `d942130…855` 已在合法 Dock 起點完成現行 FullScan gate、兩條
Isaac/Nav2 route 與 acknowledged cancel／software halt；clean `cc6d217…f6e` 的固定 Hero
序列為 10/10 route legs，且保留先前 0-goal fail-closed。Twin 同 domain 0 仍為 `skip`。
這不是 10 次完整 demo／NL 試驗、實體安全或跨載具證據。UI 樣本已獲接受並封版；v2.0.1
已在 private repository 發布；本機資料尚未獲准 harden，repository 仍 private 且
main／tag unprotected。受試者、第二維護者、未授權買家交付通道、獨立觀察者、
separated-domain Twin 與另一載具的 artifact 仍未取得。

## 可對外使用的主張

| 可以說 | 不可以說 |
|---|---|
| Agent 觸發已註冊的 ROS2／Nav2 高階能力，不直接取代底層控制 | LLM 直接安全控制任何機器人 |
| 在 Isaac Sim／Nav2 完成高階任務、Twin Gate 與固定 subset 的模擬導航任務紀錄 | 已證明精確 20 h 暴露、零安全事件，或所有實體載具皆可安全部署 |
| Slash 指令降低記憶長 ROS2 指令與參數的負擔 | 已量化提升開發效率（使用者研究完成前） |
| Vehicle Profile 讓介面層可移植 | 已證明跨運動學物理泛化 |
| `/explore` 在已知安全點位間做有界巡遊 | 可在未知地圖做 frontier exploration |
| Safety case 是可稽核的研究風險盤點 | 已取得功能安全認證 |

## Product-ready 關閉條件

「六角色都滿意」不是口頭投票，而是以下條件都有證據：

1. 所有 `OPEN` 關閉，`PARTIAL` 不是轉成 `PASS` 就是明確移到未來版本且不再行銷。
2. fresh-machine、Isaac HIL、效率研究與跨載具 PoC 都保存原始 artifact。
3. README、論文、V1_GATE、SAFETY_CASE、ROADMAP 使用同一組版本與實驗數字。
4. UI 改版需先以獨立樣本取得使用者批准，再做程式實作與鍵盤工作流回歸。
5. 最終由六角色依本表逐項重新審查；任何角色提出可驗證的新阻擋條件，就加入本表而非口頭略過。
