# PRODUCT_BRIEF — JenAI 一頁產品摘要

## 一句話

JenAI 是給已有 ROS2／Nav2 團隊的**受監督高階決策與工作流代理**：開發者用自然語言或
Slash 指令查 live ROS graph、觸發已註冊的導航／感知 API，系統經批准與可選 Twin Gate
後執行，再以 Nav2／odom／audit 回報真實結果。

## 誰會用、為何採用

| 角色 | 現在的痛點 | JenAI 提供的價值 |
|---|---|---|
| ROS2 開發者／研究生 | 記憶長指令、topic/type/參數，工具分散 | Slash 壓縮常用流程；自然語言依 live graph 發現 schema |
| 機器人整合工程師 | Demo 腳本、Nav2、Twin、紀錄各自為政 | 同一 TUI 觸發既有 API、批准、取消、stop 與 audit |
| 研究室／PoC 團隊 | LLM 會說成功，但載具未必真的完成 | 以 Nav2 result、odom 與固定實驗保留失敗證據 |
| 專案負責人 | 想重用於不同載具，又不想讓 LLM 接管底層 | Vehicle Profile＋薄 adapter；LLM 不進毫秒級控制迴路 |

最適 ICP：已有可用 ROS2 graph、Nav2 或等價高階 API，願意保留人工批准與實體安全程序
的研究室、教育單位、原型團隊。沒有底層控制、地圖、定位或現場安全負責人的團隊不是
v1 的合適客戶。

## 已證明與未證明

- 已證明：Isaac Sim/Nav2 高階任務、Twin Gate 固定場景、20 小時模擬巡航、24 小時
  daemon soak、隔離 domain 的自然語言發現—執行—回授鏈。數字以 `EVIDENCE_LEDGER` 為準。
- 未證明：正式實車安全、跨運動學物理泛化、未知地圖 frontier exploration、功能安全
  認證、多人／公網 SaaS，以及可量化的效率提升。

## 三分鐘 hero demo（凍結內容）

1. `JenAI doctor`：顯示 provider、ROS、Nav2、Twin 與設定狀態。
2. 自然語言「帶我到 `<safe-location>`」：展示 live graph／能力選擇與批准卡。
3. Nav2 執行：顯示進度，抵達後以 result／位置回授結案。
4. 以不可達或禁區目標展示 `block`／`refer`，強調不把失敗包裝成成功。

對外展示前，固定同一 commit、模型、場景與地點連跑 10 次，至少 9 次完整成功；失敗
照樣記錄。這是銷售 demo gate，不等於安全認證。

## 採購驗收

買家先用 `SUPPORT_MATRIX` 判斷組合，再依 `QUICKSTART` 安裝，以 `doctor`、一個唯讀查詢、
一個安全導航、取消／stop、不可達目標與 audit 作現場驗收。升級／回滾依 `ROLLBACK`；
安全、支援與通報邊界分別見根目錄 `SECURITY.md`、`SUPPORT.md`。

## 建議導入路徑

`離線桌面評估 → Isaac／廠商模擬器 → 隔離場域低速 PoC → 受監督現場試用`。任何一階段
未通過便停止升級，不以簡報承諾取代 artifact。
