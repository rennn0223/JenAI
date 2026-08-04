> **JenAI 的存在目的，是完成機器人的任務，而不是建造機器人的基礎設施。 / JenAI exists to execute robot missions, not to build robot infrastructure.**
>
> **一次只能有一個 Current Product Milestone；**
>
> **其唯一可變授權來源是 [`TECH_LEAD.md`](TECH_LEAD.md)。**
>
> **本 Constitution 核准時的 Milestone 為 Robot Runtime v0。**
>
> **不能直接推進目前里程碑、且不是已證明關鍵阻礙的工作，不實作。**

# JenAI v1 Product Constitution

本文件是 JenAI v1 的最高產品原則。它決定 JenAI 為何存在、目前服務誰、刻意不做什麼，
以及什麼算產品成功。技術事實仍由原始碼、測試、Architecture 與 accepted ADR 證明；
工作授權由本 Constitution 與目前 Milestone 決定。

## North Star

JenAI 把操作員的高階目標轉成可批准、可停止、可驗證並能產生 Receipt 的機器人 Mission。
它使用既有的 ROS 2、Nav2 與平台 API，不取代底層控制、定位、局部避障或硬體安全。

## Product positioning

JenAI 是給已有可靠導航或平台 API 團隊的受監督 Mission Runtime 與高階決策產品。LLM
負責理解意圖與選擇已註冊 Capability；Robot Runtime Authority 擁有接受後的執行生命週期；
確定性 Workflow 負責正常步驟、有限重試、取消、證據、完成判定與返航。

JenAI 不是通用低階控制器、任意 ROS API proxy、多 Agent 聊天框架、安全認證產品，亦不是
為研究工具本身而存在的 acceptance framework。

## Target users

- 已有 ROS 2／Nav2 或等價高階 API 的機器人整合團隊。
- 需要操作員批准、跨介面一致狀態、STOP 與誠實任務結果的受監督 PoC 團隊。
- 需要把巡檢流程、Evidence 與 Return Home 變成可重複產品能力的開發者。

沒有底層導航、安全負責人、可驗證狀態來源或現場接管程序的團隊，不是 v1 的合適部署者。

## Current scope

v1 只聚焦兩個連續產品成果：

1. Robot Runtime v0：單一 Authority 擁有 accepted Task、Runtime-owned Workflow Instance、
   Approval、lease、safety epoch、Events、Task Outcome 與 Receipt；產品畫面可將這份唯一
   lifecycle 投影為 MissionRun，但不得建立第二個 mutable aggregate。
2. Inspection Mission：以巡檢覆蓋、Evidence、bounded skip／retry 與 Return Home 證明 Runtime
   能完成使用者任務。

既有 Navigation、TUI、WebUI、NXDog read-only observation 與 acceptance 工具只在支撐上述
成果時維護；它們不是平行里程碑。

## Out of scope

- 讓 LLM 直接控制速度、轉向、關節、局部避障或即時安全迴路。
- 在 Runtime v0 前重做 React WebUI、TUI 外觀或新增 NXDog effectful capability。
- 主動深化 Geometry、Motion Safety、Differential、Stage Export、Headless parity 或
  Certification Research。
- 未知空間自主探索、多機協作、Mission marketplace 與安全認證。
- 以 simulation PASS 外推實體安全或跨載具物理泛化。

## Architecture principles

- 只有 Robot Runtime Authority 能改變 accepted Mission 的執行、批准與結果 truth。
- Interaction surfaces 是 thin clients；不得各自建立第二套 Workflow、Approval 或 STOP。
- Agent 選擇 typed Capability；正常執行由確定性 Workflow 完成，不逐步詢問 LLM。
- 所有 navigation 仍經既有 Navigation Gateway；載具差異只存在於 profile 與 adapter。
- 缺少 Evidence 時回報 blocked、unavailable、partial 或 unverified，不偽裝成功。
- 基礎設施一旦足以支撐目前產品成果即進入維護模式，可信 `BLOCK` 是工具成功。

## v1 success criteria

JenAI v1 的成功不是類別數量或工具完整度，而是操作員可以提交一個 typed Mission，經批准後
由唯一 Runtime Authority 建立 Workflow Instance 並執行，隨時 STOP，跨 TUI／WebUI 看到
同一份 MissionRun projection，最後取得由 Evidence 支撐的 Task Outcome 與 Receipt。完成門檻由
[`EPIC-0003`](../epics/EPIC-0003-ROBOT-RUNTIME-V0.md) 與
[`EPIC-0004`](../epics/EPIC-0004-INSPECTION-MISSION.md) 定義。

## Governance

本 Constitution 高於 Milestone、ADR、Epic、Design Brief 與 Implementation PR。衝突處理、
Freeze 與例外規則見 [`PRODUCT_GOVERNANCE.md`](PRODUCT_GOVERNANCE.md)。本文件合併後自身
進入 maintenance mode；只有 Product Owner 明確核准的產品方向變更才能修改。目前里程碑的
唯一可變授權來源是 [`TECH_LEAD.md`](TECH_LEAD.md)，本頁首頁文字不是平行狀態機。
