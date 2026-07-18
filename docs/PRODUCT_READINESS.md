# PRODUCT_READINESS — 六角色產品化驗收矩陣

> 本文件把工程師、PM、經營者、教授、業務與買家的要求轉成可驗證的交付條件。
> 狀態只能使用 `PASS`、`PARTIAL`、`OPEN`；沒有證據不得標成完成。

## 產品定位與邊界

JenAI v1 是一個**受監督、具執行邊界的 ROS2 高階決策與工作流代理**。它使用自然語言
或 Slash 指令理解任務、查詢 live ROS graph、選擇已註冊能力、經批准與可選 Twin Gate
驗證後呼叫 Nav2／ROS2 API，最後以 odom、Nav2 result 與 audit 回報結果。

它不是底層控制器、未知空間 frontier explorer、經功能安全認證的產品，也尚未具備常駐的
`perceive → decide → rehearse → act → feedback` 自主迴圈。`/explore` 是已知儲存點位的
有界巡遊，不是 SLAM 探索。

## 六角色驗收矩陣

| ID | 角色 | 驗收條件 | 目前證據 | 狀態 | 關閉條件 |
|---|---|---|---|---|---|
| ENG-1 | 工程師 | 無 ROS 的單元／整合測試、lint、三版本 CI、wheel 冒煙全綠 | 511 tests 與完整 lint 本機全綠；Python 3.12–3.14 CI；安全鏈 coverage gate；v1.1.2 wheel | PASS | 每次 PR 持續維持 |
| ENG-2 | 工程師 | ROS／Isaac 關鍵路徑可自動回歸，不只人工 TUI 實測 | 現有 fake bridge、E2/E3/B4 runbook；尚無 self-hosted Isaac/HIL job | PARTIAL | 自動跑 `/route`、取消、stop、Twin verdict 並保存 artifact |
| ENG-3 | 工程師 | 核心模組職責可維護 | `tui/app.py`、`tui/robot_commands.py`、`bridge/ros_bridge.py` 仍是大型熱點 | PARTIAL | 依狀態、導航、批准、呈現責任拆模組且行為測試不變 |
| ENG-4 | 工程師 | 依賴與供應鏈可稽核 | Dependabot、PR dependency review、locked runtime `pip-audit` 與 uv CycloneDX workflow 已加入；本機 audit 無已知漏洞 | PARTIAL | 合併後遠端 Supply Chain workflow 首次全綠並保存 SBOM artifact |
| PM-1 | PM | ICP 與主要任務明確 | 主要 ICP：已有 ROS2/Nav2 的研究室與機器人開發團隊；主要任務：高階任務觸發與 ROS 開發輔助 | PASS | 新功能必須服務主要任務之一 |
| PM-2 | PM | v1 與 v2 承諾分開 | v1 是受監督工作流代理；M6 常駐決策迴圈列入 v2 | PASS | README、論文、demo 不得把 M6 當現有能力 |
| PM-3 | PM | 新手可從安裝走到第一個成功任務 | ONBOARDING 與 doctor 已有；三位使用者曾在指導下試用，但未做純冷啟動計時 | PARTIAL | ≥5 位新手只看文件完成任務並保存時間／卡點 |
| BIZ-1 | 經營者 | 授權與發布可供外部採用 | Apache-2.0、GitHub release、wheel/sdist | PASS | 維持 release provenance |
| BIZ-2 | 經營者 | 有商業模式、成本與責任邊界 | `ADOPTION_MODEL`：Apache 核心＋整合／訓練／維護服務、TCO 輸入表、責任分界；現在明示無付費 SLA | PASS | 報價前以真實 pilot 工時填成本，不先造 ROI |
| BIZ-3 | 經營者 | 不依賴單一維護者 | 文件與 CI 完整，但主要提交與場域知識仍集中於一人 | PARTIAL | 第二位維護者完成一次 release 與 Isaac 故障演練 |
| RES-1 | 教授 | 研究問題、方法、證據與限制一致 | `EVIDENCE_LEDGER` 統一 E1–E4/B4/soak/TUI 數字、artifact 雜湊、可支持與不可延伸主張；living docs 已對齊 simulation-first | PASS | 新結果只能追加並保留失敗，不覆蓋舊基準 |
| RES-2 | 教授 | 「降低記憶負擔／提升效率」有對照資料 | 目前只有使用者主觀回饋，沒有手動 ROS2 vs Slash vs NL 的正式計時 | OPEN | 執行隨機化三條件使用者研究，報成功率、時間、錯誤與查詢次數 |
| RES-3 | 教授 | 跨載具主張符合證據 | Vehicle Profile 與高階 API 支持介面可移植；物理泛化未驗證 | PARTIAL | 至少一個非 Ackermann 平台完成固定 PoC 任務集 |
| SALES-1 | 業務 | 三分鐘內可穩定展示核心價值 | `PRODUCT_BRIEF` 已凍結 doctor→NL safe goal→feedback→block/refer hero demo | PARTIAL | 同 commit／模型／場景連跑 10 次，≥9 次完整成功 |
| SALES-2 | 業務 | 有可引用的 ROI／案例 | 有技術實驗，沒有節省時間、導入成本或客戶案例 | OPEN | 完成效率研究並寫一頁案例研究 |
| SALES-3 | 業務 | 不過度承諾 | 誠實回報與限制文件已有 | PASS | 不說「通用實體安全」「認證」「未知空間自主探索」 |
| BUY-1 | 買家 | 能直接安裝、啟動、診斷與移除 | 隔離 `/tmp` 執行 `uv tool install .`，成功產生 `JenAI`／`jenai`；version/help 與無設定 doctor 診斷通過 | PARTIAL | 真正 fresh machine 只照 README 完成 onboard、doctor 與移除，無維護者介入 |
| BUY-2 | 買家 | 資安與部署邊界清楚 | `SECURITY`、`THREAT_MODEL`、`SUPPORT`、Supply Chain workflow；明示 `/shell`、DDS、公網與功能安全限制 | PARTIAL | 遠端 workflow 首次全綠並保存 SBOM artifact |
| BUY-3 | 買家 | 有支援載具／ROS／模型矩陣與驗收方式 | `SUPPORT_MATRIX` 分 Validated／Supported／Experimental／Planned；`VEHICLE_POC` 固定驗收 | PASS | 新組合有 artifact 才能升級等級 |
| BUY-4 | 買家 | 有 SLA、升級、回滾與事故處理 | `SUPPORT` 明示目前 best-effort／無 SLA；`ROLLBACK` 涵蓋 wheel、source、Isaac 與實體回歸；安全通報另見 `SECURITY` | PASS | 若推出付費方案，須另簽回應時段與嚴重度 SLA |

## 可對外使用的主張

| 可以說 | 不可以說 |
|---|---|
| Agent 觸發已註冊的 ROS2／Nav2 高階能力，不直接取代底層控制 | LLM 直接安全控制任何機器人 |
| 在 Isaac Sim/Nav2 完成高階任務、Twin Gate 與耐久驗證 | 已證明所有實體載具皆可安全部署 |
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
