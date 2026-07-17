# JenAI 三方會談:實用性攻防與收斂架構

> 形式:PM、工程師、客戶三個立場對「這個專案到底有沒有用」吵一架,
> 吵完收斂出一套完整的專案架構與功能優先序。
> 客戶設定:研究室/場域營運者,手上有一隻四足機器狗(Go2 級)與一台阿克曼車,
> 想用自然語言操作與半自主決策。

---

## 出場人物與立場

| 人格 | 在乎什麼 | 最怕什麼 |
|---|---|---|
| **PM** | 差異化、可交付、時程可控、論文/產品雙棲 | 做成「又一個 LLM+ROS demo」,沒人記得 |
| **工程師** | 誠實的技術邊界、可維護、不過度承諾 | 為了 demo 埋炸彈,半年後全部重寫 |
| **客戶** | 今天就能用在我的狗上、不弄壞硬體、學弟 10 分鐘上手 | 花三個月接了一套只能跑 happy path 的玩具 |

---

## 第一回合:這專案到底解決了什麼問題?

**客戶**:我直說。我有一隻狗,我要對牠說「去 B 棟門口看一下有沒有人」,牠就去、
看完回報。JenAI 現在做得到嗎?

**工程師**:誠實拆開講。做得到的:自然語言 → 地點解析 → Nav2 導航(即時剩餘距離、
Esc 真取消)、相機抓幀 → VLM 描述「看到什麼」、位置回報、規則觸發(電量低自動回充)。
這條鏈在阿克曼實車上端到端驗證過。你的狗**只要跑 ROS2 + Nav2,這整條鏈直接通**——
bridge 只講 topics 和 NavigateToPose action,根本不在乎載具是輪子還是腿。
做不到的:步態、姿態(站/趴/越障)、狗特有技能,一行都沒寫。

**客戶**:所以是「會導航的狗」,不是「會用腿的狗」。

**工程師**:對。而且我不打算假裝會。腿的事是廠商 locomotion controller 的事,
我們對接它暴露的 ROS 介面,不重造。

**PM**:我補定位問題。LLM+ROS 不是新題目——NASA JPL 的 ROSA、各種 ROSGPT 都做過
「自然語言查 topics、發指令」。如果 JenAI 只是這樣,沒有差異化。我們的護城河有三個:
(1) **誠實回報原則**——沒有後端就說 unavailable,絕不假裝成功,這在機器人圈是稀缺品;
(2) **三層安全鏈**(後面吵);(3) **Twin-Gated Execution**——指令先在 Isaac Sim
數位孿生跑一遍、過閘門才碰實體,這個別人沒有,而且正好是論文核心。

---

## 第二回合:實用性攻防(吵架主場)

**客戶(攻)**:Demo 好看沒用。我的三個真實需求:一、狗真的聽話;二、狗不會被 AI
弄壞——牠比你們一學期預算還貴;三、我畢業後學弟接手,10 分鐘要會用。現在幾分?

**工程師(防)**:第二點我最有底氣。現在已經是雙閘門:敏感操作一律 HITL 人工批准
(Enter/Esc),daemon 要 `auto_approve` + nav2 明確授權才會動,速度有確定性夾限,
MCP 預設唯讀。第三點:setup wizard + doctor + TECHNICAL_GUIDE 有了,但我承認——
**從裸機器人到「第一次導航成功」之間的建圖、定位、cmd_vel 映射,現在沒人牽你的手**。
那段是最痛的,而且我們文件跳過了它。

**客戶(追擊)**:還有,你們的 LLM 在雲端。我的場域網路爛到哭,而且指令下去等三秒,
狗都走過頭了。

**工程師**:兩件事別混。**LLM 從來不在即時迴路裡**——避障是 Nav2 的事(毫秒級),
急停該是硬體/反射層的事(不經任何模型)。LLM 只做任務層決策:「去哪、做什麼、
要不要人批准」,這層慢一兩秒無所謂。本地部署也解了:DGX Spark 上 Ollama +
qwen3.6:35b 實測可用,`/provider` 一鍵切換,斷網照跑。

**PM(裁決)**:這一回合吵出兩個結論。第一,**缺口不是 AI,是機器人端 onboarding**
——建圖定位那段要補產品化。第二,架構必須明文分層:
**反射層(不經 LLM)→ 決策層(LLM)→ 監督層(人)**,各層有各自的時間尺度和安全語意。
客戶怕的「AI 弄壞狗」,答案不是把 AI 做聰明,是把 AI 關在對的層裡。

**客戶(不服)**:那 Twin Gate 呢?聽起來像論文用的花架子。多一層模擬,操作多等幾秒,
我為什麼要?

**PM**:因為你剛剛自己說狗很貴。HITL 擋的是**意圖層錯誤**——AI 聽錯你的話;
但你批准了一個「聽起來對」的指令,執行下去撞櫃子,HITL 救不了你,因為錯在**執行層**。
Twin Gate 就是在真狗動之前,讓指令在孿生場景先跑一遍:碰撞、超時、進禁區、
終點偏差、Nav2 失敗,五個判準(G1–G5)任一觸發就攔下或轉人工。
這不是花架子,是你第三次差點撞欄杆之後會回來謝我們的東西。而且——它可以做成
**可關閉的選配**:急的時候關掉,值錢的任務開著。

**工程師**:工程上也站得住:Isaac Sim 場景是論文本來就要建的,Gate 判準全部用
ROS topics 就能算,不用碰 Isaac 內部 API,增量成本可控。

---

## 第三回合:論文視角——這專案提供了什麼?

**PM**:整理給指導教授看的版本。

1. **系統貢獻**:一個可重現的 LLM-Agent × ROS2 實驗平台——完整測試、CI、
   誠實回報語意、雙介面(TUI/WebUI)+ MCP 互操作。多數同類論文的系統是
   「實驗跑完就死」的膠帶工程,JenAI 本身就是可審查、可延續的 artifact。
2. **方法貢獻**:以觀察、決策、能力、權限與執行驗證五類邊界包絡高階 Agent；
   Isaac Sim 數位分身預演是執行驗證的一種機制，與 HITL 正交，但不是整套系統的名稱。
3. **實證貢獻**:E1 高階決策、E2 配對執行驗證消融、E3 隔離 ROS graph 的自然語言
   閉環工具使用、E4 本地模型延遲與 B4 Isaac Sim 耐久測試。先期 Ackermann 同指令
   展示只作介面整合背景；未完成的實機軌跡、雲端對比與 sim-to-real 不列為正式結果。
4. **泛化性論證與後續驗證**:Vehicle Profile、adapter 與高階能力 schema 將載具差異
   收斂於介面層；這支持可移植設計，但不等同跨運動學物理泛化已證明。四足平台應作
   後續強對照，量測 adapter 修改量、能力覆蓋率、任務成功率與物理行為差異。

**工程師**:提醒:論文主軸維持阿克曼(實驗已完成大半),狗是「延伸驗證」章節,
不要反客為主,時程會爆。

**客戶**:同意,但延伸驗證請用真狗,審查委員和我一樣不信純模擬。

---

## 收斂:專案架構(三方簽字版)

```
                【監督層】人 — 時間尺度:秒~分
   TUI (操作者) · WebUI (手機批准/地圖) · MCP (Claude 等外部 agent)
        │  HITL 批准卡:攔「意圖層錯誤」(AI 聽錯人話)
        ▼
                【決策層】LLM — 時間尺度:秒
   意圖解析 → 任務規劃 (/plan /mission) → 地點解析 (locations)
   provider 抽象:NVIDIA NIM (雲) ⇄ Ollama (DGX Spark 地端),斷網可用
        │
        ▼
                【閘門層】Digital Twin — 時間尺度:秒(選配,可關)
   指令先在 Isaac Sim 孿生場景執行 → G1 碰撞 / G2 超時 / G3 禁區 /
   G4 終點偏差 / G5 Nav2 失敗 → pass / block / refer(轉人工)
   攔「執行層錯誤」(指令沒錯,執行會出事)
        │
        ▼
                【技能層】載具無關的任務原語
   navigate_to · patrol(多點循環) · return_to_dock · follow_route
   · capture_and_describe(相機+VLM) · (四足擴充:gait_mode, body_pose)
        │
        ▼
                【反射層】Rule Daemon — 時間尺度:毫秒~秒,不經 LLM
   battery < x → 回充 · estop topic → 全停 · bridge 心跳斷 → 停車
   確定性規則,LLM 掛了照樣保命
        │
        ▼
                【橋接層】rclpy bridge(系統 python sidecar,JSON/stdio)
   topics · pose · NavigateToPose(feedback/cancel) · camera · watch
        │
        ▼
                【載具層】vehicle profile(TOML,唯一允許差異的地方)
   Ackermann:Nav2 + Smac Hybrid-A* + RPP,速限/軸距/footprint
   Quadruped:Nav2 + 廠商 locomotion controller,步態參數/地形模式
```

> 實作註記(v0.25+):上圖所有導航路徑在程式裡收斂於 **NavigationGateway 單一出口**
> (Twin Gate/watchdog 政策無法被直呼繞過),並且 run/批准/工具/Gate verdict
> 事件全部進 **SQLite audit**(跨重啟可回溯)。

**架構鐵律(工程師條款)**:
- LLM 永不進即時迴路;反射層永不依賴 LLM 與網路。
- 技能層以上不得出現任何載具字眼;載具差異全部收在 vehicle profile。
- 每一層失敗都誠實回報,不得偽裝成功穿透到上層。

---

## 功能清單(三方收斂後的優先序)

### 必做(三方一致,缺一個就撐不起「真的可用」)

| # | 功能 | 誰最堅持 | 狀態 | 說明 |
|---|---|---|---|---|
| M1 | **E-stop / watchdog** | 客戶 | ✅ **v0.7** | `/stop`、WebUI STOP 鈕、MCP stop、daemon halt;bridge watchdog 斷線自主停車 |
| M2 | **Vehicle profile 抽象** | 工程師 | ✅ **v0.7** | config `[vehicle]`:cmd_vel topic、硬限速、相機 topic → 換載具改設定不改程式 |
| M3 | **Isaac Sim twin 場景 + Twin Gate pipeline** | PM | 🚧 **pipeline ✅** | 論文核心 = 產品差異化。Gate pipeline 已完成:G1–G5 判準、pass/block/refer、孿生獨立 ROS_DOMAIN_ID、`[twin]` 設定、doctor `twin` 檢查,掛在導航唯一出口(所有入口 + daemon 全過閘)。剩 Isaac Sim 場景建置(工作站作業,見 [TWIN_SETUP.md](TWIN_SETUP.md) = 客戶 B5) |
| M4 | **任務級技能:patrol / return_to_dock** | 客戶 | ✅ **v0.8** | `/patrol A, B x3 photo`(循環+每點 VLM 觀察)、`/dock`;follow_route 由 `/mission` 涵蓋 |
| M5 | **Onboarding 精靈/文件:裸 ROS2 → 第一次導航** | 客戶 | ✅ **軟體 v0.13** | doctor `nav` 區段 + ONBOARDING.md 手把手;剩新手計時實測(客戶 B6) |
| M6 | **自主決策迴圈(論文主軸)** | PM(論文) | 🚧 **零件齊,迴圈未串** | 感知/有界動作/odom 直驅/避障/Gate/規則引擎都在,尚未串成 DecisionLoop 閉環。v2.0 主線,詳見 [ROADMAP 軌道 1](ROADMAP.md) |

> **v0.9–v0.25 新增(不在原 M 表,但已 shipped)**:odom 直驅(`route_adapter=odom`,無 Nav2 開闊地導航)、局部避障(depth stop-and-go detour + stale-frame fail-closed)、GPS 地點(`/loc add gps` + `[map_datum]`)、`/route 從A到B` 依序、多頁 WebUI(Camera/API)、`/report` 巡邏日報(=C2)、`JenAI help`、V1_GATE 層一工程(semver/威脅模型/safety case/soak/架構鐵律/覆蓋倒退閘)。完整前瞻見 **[ROADMAP.md](ROADMAP.md)**。

### 可做(有明確價值,排在必做之後)

| # | 功能 | 說明 |
|---|---|---|
| C1 | 多機/namespace 支援 | bridge 加 namespace,一個 JenAI 管狗+車(ROADMAP 軌道 4) |
| C2 | 巡邏報告 | ✅ **v0.12** `/report`:確定性日報 + LLM 摘要,離線誠實降級 |
| C3 | WebUI 地圖疊 costmap/規劃路徑 | 現有 SVG 地圖已有 pose+locations,疊圖是自然延伸(ROADMAP 軌道 5) |
| C4 | MCP action 擴充 | patrol 等技能開給 MCP client(維持 --allow-actions 閘控) |
| C5 | 語音輸入 | 場域裡對狗喊話比打字實際;whisper 本地跑 |

### 考慮做(價值存在,但依賴外部因素或時程)

| # | 功能 | 卡在哪 |
|---|---|---|
| T1 | 四足特有技能(階梯模式、地形適應、趴下裝載) | 依賴廠商 SDK 的 ROS 介面品質 |
| T2 | 多 agent 協作(狗偵察 + 車運載) | C1 做完才有意義;論文第二篇的題目 |
| T3 | LLM 生成行為樹(BT XML → Nav2 BT navigator) | 學術上誘人,工程上驗證成本高 |
| T4 | 場域知識 RAG(SOP、設備手冊) | 等真實場域資料;現在做是無米之炊 |

### 明確不做(工程師否決權)

- 自研步態控制器/SLAM——重造廠商與社群已解的輪子。
- LLM 即時避障——錯的層,不安全也不必要。
- 幾何避障/局部規劃的持續深化——Isaac 實測判死(見「方向定調」);對接載具原生 nav,不重造。
- 「全自主無人監督」模式——與安全敘事矛盾,論文與產品都不需要。

---

## 對「機器狗操作/決策」的真實可用性總結(給客戶的誠實清單)

| 等級 | 能力 | 條件 |
|---|---|---|
| **今天就能用** | 自然語言查 topics/pose、相機看畫面、聊天問答、規則告警 | 狗有 ROS2,bridge 直接通(載具無關) |
| **設定好就能用** | `/route` `/mission` 導航、`/loc add here` 建點、電量低自動回充、WebUI 手機批准 | 狗上跑 Nav2 + 建好圖(M5 onboarding 就是在補這段) |
| **做完必做才有** | 一鍵急停、巡邏任務、twin 預演閘門、車狗一檔切換 | M1–M5 |
| **誠實的極限** | 步態級控制、動態環境即時決策、無人全自主 | 前者等載具層(T1),後兩者是設計上就不進 LLM 的層 |

**一句話收斂**:JenAI 已經是「載具無關的任務層大腦 + 三層安全鏈」;
讓它真的能操作一隻狗,缺的不是更聰明的 AI,而是 M1(急停)、M2(載具檔)、
M5(onboarding)三塊工程,以及論文本來就要做的 M3(Twin Gate)。
路線圖上這四塊沒有一塊是賭博,全部是已驗證架構的直線延伸。

---

---

## 方向定調(2026-07,v0.31)—— AI Decision Agent,不是 AI Control Agent

三方會談之後的實證更新,兩個事實改寫了投資方向:

1. **Isaac 孿生實測:單 depth 相機做反應式避障不可行/成本不成比例。**
   這是 Twin 第一次完成它的使命——在虛擬世界便宜地殺掉壞主意。
   內建避障(stop-and-go detour)與 odom 直驅自此標記 **deprecated**:
   維持現狀當 bring-up/除錯的誠實 fallback,只修 bug、不再投資能力。
2. **兩台載具(阿克曼車、四足)皆自帶 SLAM+Nav。**運動控制自始至終
   是載具層的職責;JenAI 不該代班,該對接。

**定位最終形態**:
> JenAI = **具執行邊界的 AI Decision Agent**,坐在載具原生導航堆疊之上——
> 自然語言/自主決策 → 已註冊高階能力 → 批准與可選 Twin 驗證 → goal 交給載具自己的
> nav → 結果回授與稽核。JenAI 不取代底層運動控制；執行邊界規定它能觀察、選擇、
> 觸發與驗證什麼。

推論:M6 決策迴圈是唯一主軸;`/drive`、`/ros pub`、odom 直驅降級為
工程工具;「感知深化」改向餵決策快照(語意),不餵輪子(幾何)。
負面結果(depth-only 避障不可行)寫入論文第五章失效分析——委託原生
堆疊是量測後的設計決策,不是省事。

### 驗證策略更新(2026-07)—— Isaac Sim 為主要驗證平台

整套架構(監督/決策/閘門/技能/反射/橋接)的 v1.0 驗收證據**以 Isaac Sim
孿生場景為主**:接口確認、地點建置、任務實測、里程與消融數據全部在 twin
上完成。實體驗證降為**選配**——有機會再做,或連同載具交接給下一屆(架構
載具無關、twin 與實車走同一條 bridge/Nav2 介面,實體驗證不改一行程式,
只補數據)。sim-to-real 落差在論文中誠實標註為 limitation / future work。

操作機事實更正:JenAI 與 Ollama 跑在 **DGX Spark**(非 Jetson);
本地預設模型 **qwen3.6:35b**。V1_GATE 層二各項已依此重定義。

*本文件為專案方向參考,由 PM/工程師/客戶三視角推演收斂;
論文對應規劃見 docs/THESIS_PLAN.md、docs/THESIS_DRAFT.md(均不入版控)。*
