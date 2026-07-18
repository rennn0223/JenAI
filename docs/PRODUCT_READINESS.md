# PRODUCT_READINESS — 六角色產品化驗收矩陣

> 本文件把工程師、PM、經營者、教授、業務與買家的要求轉成可驗證的交付條件。
> 狀態只能使用 `PASS`、`PARTIAL`、`OPEN`；沒有證據不得標成完成。

## 產品定位與邊界

JenAI v2.0.1 是一個**受監督、具執行邊界的 ROS2 高階決策與工作流代理**。它使用自然語言
或 Slash 指令理解任務、查詢 live ROS graph、選擇已註冊能力、經批准與可選 Twin Gate
驗證後呼叫 Nav2／ROS2 API，最後以 odom、Nav2 result 與 audit 回報結果。

它不是底層控制器、未知空間 frontier explorer、經功能安全認證的產品，也尚未具備常駐的
`perceive → decide → rehearse → act → feedback` 自主迴圈。`/explore` 是已知儲存點位的
有界巡遊，不是 SLAM 探索。

新版 workflow 會為 private release 產生 CycloneDX SBOM 與 `SHA256SUMS`；只有這些 assets 實際出現在該 GitHub Release 時才視為已發布，且 private path 不會產生或宣稱 GitHub artifact attestations。

下表的本機結果來自尚未提交的 v2.0.1 候選工作樹，不是 Git commit／tree／tag。
先前內容 fingerprint 只用於偵測驗證期間是否有檔案漂移，刻意不當作可重播的 release
revision。建立 PR、合併與 tag 時必須在對應 Git revision 重跑 gate，並以遠端 CI、
tag 與 release artifact 作為不可變證據。

## 六角色驗收矩陣

| ID | 角色 | 驗收條件 | 目前證據 | 狀態 | 關閉條件 |
|---|---|---|---|---|---|
| ENG-1 | 工程師 | 無 ROS 的單元／整合測試、lint、三版本 CI、wheel 冒煙全綠 | 2026-07-19 v2.0.1 候選工作樹複審：Ruff 全綠、pytest 目前 597/597、安全鏈 coverage 93%，wheel／sdist／matching constraints／SBOM 與隔離安裝→診斷→移除生命週期全綠；這是以內容 fingerprint 防漂移的本機候選證據；對應 PR／merge revision 的遠端三版本 CI 尚未執行 | PARTIAL | 候選版 PR 的 Python 3.12–3.14 CI 全綠並保存 run URL |
| ENG-2 | 工程師 | ROS／Isaac 關鍵路徑可自動回歸，不只人工 TUI 實測 | HIL runner 有雙重 opt-in、NavigationGateway、cancel／halt 與起點重檢；2026-07-18 真 graph 的 ROS/Nav2 必要檢查全 pass，但 AMCL `(-7.16,-9.48)` 落入 `SW-narrow-aisle`，新版 guard 正確拒絕並保存 artifact，未送 goal | PARTIAL | Reset 合法起點後跑 route、取消、stop；隔離 Twin 場景另跑 verdict，保存成功與失敗 artifact |
| ENG-3 | 工程師 | 核心模組職責可維護 | `app.py` 1,789→1,245；批准生命週期抽為 `approval_flow.py`，已批准工具執行抽為 `direct_execution.py`，另有 `catalog.py`、location mixin 與 stdlib-only bridge protocol；完整回歸全綠 | PASS | 新功能維持 approval policy／execution／rendering 邊界與行為測試 |
| ENG-4 | 工程師 | 依賴與供應鏈可稽核 | Dependabot、locked runtime `pip-audit` 與 uv CycloneDX workflow；本機 v2.0.1 候選產物已通過 constraints、CycloneDX SBOM、checksum 與 fresh-wheel lifecycle 檢查；可執行 workflow 測試另證明 private 明確略過 attestations、public 缺任一 attestation 即失敗；已發布 v1.1.4 只有較早的 audit／SBOM workflow 證據，release assets 未含這套完整產物 | PARTIAL | 以新版本實際發布完整 private assets（含 SBOM／checksum）並保存 release 與 workflow URL；若未來公開則另要求 attestations；每週持續掃描 |
| PM-1 | PM | ICP 與主要任務明確 | 主要 ICP：已有 ROS2/Nav2 的研究室與機器人開發團隊；主要任務：高階任務觸發與 ROS 開發輔助 | PASS | 新功能必須服務主要任務之一 |
| PM-2 | PM | v2 與 post-v2 承諾分開 | v2.0.1 是受監督工作流代理與產品化安全基線；M6 常駐決策迴圈移至 post-v2（候選 v3），目前未實作 | PASS | README、論文、demo 不得把 M6 當現有能力 |
| PM-3 | PM | 新手可從安裝走到第一個成功任務 | ONBOARDING、doctor 與 responsive TUI 已有；三位使用者曾在指導下試用，但未做純冷啟動計時 | PARTIAL | ≥5 位新手只看文件完成任務並保存時間／卡點 |
| PM-4 | PM | TUI 資訊層級、鍵盤流程與窄螢幕可用 | Claude Code 風格雙欄 welcome、平面 transcript／approval、composer 與 3 級 responsive 已實作；70 項 TUI 測試全綠；使用者於 2026-07-19 明確接受目前 120×30／80×30 SVG 樣本（「UI 先這樣」） | PASS | 本版視覺封版；未來若改外觀，仍須先提供獨立樣本取得使用者批准 |
| BIZ-1 | 經營者 | 授權與發布可供外部採用 | Apache-2.0 已就緒；目前 repository 為 private、main／tag 未受保護，未授權買家沒有下載通道；v1.1.4 已發布但缺 matching constraints、checksum、SBOM 與 provenance assets；本機 v2.0.1 候選產物只證明新版發布機制可執行，不是 commit 且尚未發布 | PARTIAL | 決定並實作可交付的公開或受控私人通道、啟用適當 main／tag 保護，再發布含完整驗證產物的新版本 |
| BIZ-2 | 經營者 | 有商業模式、成本與責任邊界 | `ADOPTION_MODEL`：Apache 核心＋整合／訓練／維護服務、TCO 輸入表、責任分界；現在明示無付費 SLA | PASS | 報價前以真實 pilot 工時填成本，不先造 ROI |
| BIZ-3 | 經營者 | 不依賴單一維護者 | 文件、CI 與 HANDOFF 的第二維護者 release／rollback／Isaac 故障演練已具可執行步驟與 artifact 欄位，但尚未由第二人獨立完成 | PARTIAL | 第二位維護者在作者介入 0 次下完成一次 release、rollback 與 Isaac 故障演練 |
| RES-1 | 教授 | 研究問題、方法、證據與限制一致 | `EVIDENCE_LEDGER` 已明列 E2 A／B derived、C observed，並以可重建 manifest 固定 B4 102 份 reports／407-of-408；但 E1、E2-C、E3、E4、B4 的 execution revision 與歷史 doctor JSON 未記錄，B4 無 incident 欄或獨立觀察者，不能主張 0 安全事件 | PARTIAL | 以凍結 revision 與 protocol-specific preflight 重跑或找回可驗證 metadata；另保存獨立事件觀察紀錄。新結果只能追加且保留失敗 |
| RES-2 | 教授 | 「降低記憶負擔／提升效率」有對照資料 | 已有隨機化六序列 Williams 條件排程、匿名 trial 計時、失敗保留與分析骨架；目前排程器尚未實作 A／B／C 等價任務變體的交叉平衡，僅可作內部排練，也尚未招募受試者 | OPEN | 先實作並測試等價任務變體平衡，完成樣本數依據與倫理程序後，再執行三條件研究，報成功率、時間、錯誤與查詢次數 |
| RES-3 | 教授 | 跨載具主張符合證據 | Vehicle Profile 與高階 API 支持介面可移植；物理泛化未驗證 | PARTIAL | 至少一個非 Ackermann 平台完成固定 PoC 任務集 |
| SALES-1 | 業務 | 三分鐘內可穩定展示核心價值 | `PRODUCT_BRIEF` 已凍結 hero demo；TUI-R2 修正軟體阻擋，但同域車停在禁區，本輪成功導航 0 次且未啟動 10-run | PARTIAL | Reset 到合法起點後，同 commit／模型／場景連跑 10 次，≥9 次完整成功 |
| SALES-2 | 業務 | 有可引用的 ROI／案例 | 效率研究 protocol 與分析工具已可執行，但仍沒有受試資料、節省時間、導入成本或客戶案例 | OPEN | 完成效率研究並寫一頁案例研究 |
| SALES-3 | 業務 | 不過度承諾 | 誠實回報與限制文件已有 | PASS | 不說「通用實體安全」「認證」「未知空間自主探索」 |
| BUY-1 | 買家 | 能直接安裝、啟動、診斷與移除 | 本機 v2.0.1 候選 wheel 已在隔離目錄完成 matching constraints 安裝、雙入口、version／help、無設定 doctor 與 uninstall；但它尚未成為已發布 GitHub release；目前私人 repo 也不提供未授權買家下載 | PARTIAL | 新版本發布後，由非維護者在 fresh machine 只照 README 完成下載、驗證、onboard、doctor 與移除 |
| BUY-2 | 買家 | 資安與部署邊界清楚 | `SECURITY`、`THREAT_MODEL`、`SUPPORT` 已明示 `/shell`、DDS、雲端資料外送與功能安全限制；本機 v2.0.1 候選建置可產生 CycloneDX SBOM／checksum；目前 private 發布不支援 GitHub attestations，v1.1.4 release 亦未發布逐版 SBOM | PARTIAL | 發布第一個含逐版 SBOM／checksum 的 private release；若未來公開，再以 attestations 補強；企業部署另做場域 threat review |
| BUY-3 | 買家 | 有支援載具／ROS／模型矩陣與驗收方式 | `SUPPORT_MATRIX` 分 Validated／Supported／Experimental／Planned；`VEHICLE_POC` 固定驗收 | PASS | 新組合有 artifact 才能升級等級 |
| BUY-4 | 買家 | 有 SLA、升級、回滾與事故處理 | `SUPPORT` 明示目前 best-effort／無 SLA；`ROLLBACK` 涵蓋 wheel、source、Isaac 與實體回歸；安全通報另見 `SECURITY` | PASS | 若推出付費方案，須另簽回應時段與嚴重度 SLA |
| BUY-5 | 買家 | 本機敏感資料有可稽核的生命週期與最小權限 | `JenAI data status／harden／export／prune／purge`、symlink／hardlink 防護、0600 atomic write 與秘密遮罩已有測試；只讀盤點仍發現既有資料需要 harden，因權限調整可能影響既有群組分享，尚未在使用者未同意下改動 | PARTIAL | 使用者明確 opt-in 後執行 harden，再以只讀 status 證明無不安全項目並保存不含秘密的摘要 |

## 2026-07-19 六角色第二輪內部複審

本輪共同驗證基準為上述 v2.0.1 本機候選工作樹：完整 Ruff 通過、pytest
目前 597/597、安全鏈 coverage 93%，以及 wheel、sdist、matching constraints、CycloneDX
SBOM 與隔離 install／doctor／uninstall lifecycle 通過。這些結果證明的是**候選機制與
本機產物**，不是 PR／merge revision 的遠端 CI 或已發布 release 證據；建立與合併 PR
時仍須在對應 revision 重跑所有 gate。尤其不能回填成 v1.1.4 已具有 constraints、
逐版 SBOM 或 provenance。

以下 verdict 的 `SATISFIED_WITH_EXTERNAL_GATES` 表示該角色在本輪審查範圍內沒有再提出
可由目前 worktree 單獨修正的內部 blocker。它**不會覆寫**上方矩陣的 `PASS`／`PARTIAL`／
`OPEN`：每一列仍須依自己的證據 gate 關閉，外部條件在取得 artifact 前一律不算通過。

| 角色 | 第二輪 verdict | 審查範圍／證據參照 | 仍未通過的外部 gate |
|---|---|---|---|
| 工程師 | `SATISFIED_WITH_EXTERNAL_GATES` | `tests/`、`.github/workflows/{ci,release,security}.yml`、`docs/TEST.md`、`docs/SAFETY_CASE.md`、`docs/ISAAC_HIL_ACCEPTANCE.md`；含目前 597/597、93% 與 v2.0.1 本機候選封裝生命週期；PR／merge revision 仍須由遠端 CI 重跑 | 候選版遠端 CI；合法起點的 Isaac route／cancel／stop artifact；新 release 的供應鏈 assets |
| PM | `SATISFIED_WITH_EXTERNAL_GATES` | `README.md`、`docs/PRODUCT_BRIEF.md`、`docs/UX.md`、responsive TUI 測試與已接受的 120×30／80×30 樣本 | ≥5 位新手冷啟動紀錄；未來視覺變更仍須先看樣本 |
| 經營者 | `SATISFIED_WITH_EXTERNAL_GATES` | `LICENSE`、`SUPPORT.md`、`docs/HANDOFF.md`、`docs/ROLLBACK.md` 與 release workflow 的 immutable-release guard | repo 仍 private／unprotected；新版尚未發布；第二維護者零介入演練 |
| 教授 | `SATISFIED_WITH_EXTERNAL_GATES` | `docs/EVIDENCE_LEDGER.md`、`docs/USABILITY_STUDY.md`、`docs/SAFETY_CASE.md` 與本機論文 v18；derived／observed 與限制已對齊 | prospective 使用者研究、合法起點 HIL、獨立事件觀察與非 Ackermann 實體 PoC |
| 業務 | `SATISFIED_WITH_EXTERNAL_GATES` | `docs/PRODUCT_BRIEF.md`、`docs/DEMO_SCRIPT.md`、`docs/TUI_LIVE_ACCEPTANCE_2026-07-17.md`；可說／不可說主張一致 | Isaac 10-run 目前仍為 0 次成功；效率結果與可引用案例 |
| 買家 | `SATISFIED_WITH_EXTERNAL_GATES` | `docs/QUICKSTART.md`、`docs/ROLLBACK.md`、`docs/SUPPORT_MATRIX.md`、`docs/DATA_LIFECYCLE.md` 與候選 wheel lifecycle | 可存取的新 release、非維護者 fresh-machine 驗收；本機 data harden 仍等待使用者 opt-in |

本輪沒有已知、可由目前 worktree 繼續修正的內部 blocker；這不是 product-ready 宣告。
目前外部事實仍是：Isaac/Nav2 live 驗證因非法起點而未送 goal、成功數為 0；目前 UI 樣本
已獲使用者接受並封版；既有本機資料尚未獲准 harden；repository 仍 private 且 main／tag
unprotected；新版 release 尚未發布。受試者、第二維護者、獨立觀察者與另一載具的 artifact
也尚未取得。

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
