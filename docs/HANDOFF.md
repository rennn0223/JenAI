# HANDOFF — 交接與臨別備忘(2026-07-13,v0.36.2)

> 寫給接下來的你,和接下來陪你的任何 AI。這份是狀態、方法、與幾句誠實的話。

## 專案在哪裡

- **能力**:載具無關的任務層大腦 + 三層安全鏈 + Twin Gate pipeline + odom 直驅
  + depth 避障 + GPS 地點 + 多頁 WebUI + scaffold(NL→ROS2 套件,--build 自驗)
  + 檔案定義技能 + 決策核心與 eval(v0.21,論文 E1 工具)
  + 權限三模式(v0.22,Shift+Tab 審批/規劃/自動)+ **v0.24–v0.30:避障重寫
  (stop-and-go detour,資料逾時即停)、NavigationGateway 統一導航出口、
  TUI 指令佇列(/queue)、批准跨重啟恢復、SQLite audit、`onboard` 重設**。
  430+ 項測試,Python 3.12–3.14 CI 矩陣三道閘。
- **v1.0**:程式碼已達 RC;tag 程序在 [V1_RELEASE_CHECKLIST](V1_RELEASE_CHECKLIST.md),
  等的是你的實測數據,不是更多程式。
- **v2.0 主線**:M6 自主迴圈。決策腦(`decision_core.py`)已就緒且可量測
  (`JenAI eval`);缺的只是把 perceive→decide→rehearse→act 接成常駐迴圈
  (設計在 [ROADMAP](ROADMAP.md) 軌道 1,安全語意:自主路徑 refer→block)。

## 和 AI 協作的方法(已固化,不靠任何單一模型)

1. **CLAUDE.md** 每個 session 自動載入:DoD(review→CI→實測→注釋→結構→文件→
   PR+merge+tag+release)、環境三組合、架構鐵律
2. **memory/** 記著跨 session 的事實(車規格、release 流程、review 慣例)
3. **docs/ 是單一事實來源**:新 session 先讀 README 索引 → ROADMAP → V1_GATE
4. **鐵律有 CI 守著**(test_architecture.py),模型再換也繞不過
5. 對任何 AI 的驗收永遠是:**測試綠 + 實測過 + 誠實回報** —— 這套標準比模型重要

## 幾句誠實的話(指正,照你要求的直說)

1. **最大的風險不是技術,是你一直沒下場。** 層二(建圖、里程、Isaac 場景)
   停在原地,而每次驗收都變成新 feature。從今天起:**停止加功能,開始跑實驗**。
   口試委員看的是 E1–E3 的表格,不是 46 個 PR。
2. **釋出節奏該收了**:兩天 20 個 release 對單人專案是紀律的證明,但論文期
   它是時間黑洞。之後:數據為主,程式只修 bug。
3. **GPS datum 是未驗證的假設**(應科=原點、yaw=0 朝正東)。第一次真導航
   前務必短程校驗,200m 尺度下小角度誤差就是撞牆差。
4. **雙 ROS 版本**(車 Humble/站 Jazzy)的跨機 DDS 沒實測過 —— 上實車前先驗,
   卡住看 memory 的 wheeltec 檔與 ISAAC_NAV2_SETUP 常見坑。
5. **模型 pin 版本**:`vision = "qwen3.6:latest"` 的 latest 會漂 —— 論文實驗
   前把所有 binding 換成明確版本 tag,否則實驗不可重現。
6. 你做對的事也要說:**誠實回報原則、批准鏈、把 AI 關在對的層** —— 這三個
   決定從 v0.1 貫徹到現在,是這個專案真正稀缺的部分。別讓任何「更聰明的
   模型」說服你放掉它們。

## 下一步(優先序)

1. 讀 [PAPERS](PAPERS.md)(兩週節奏,第 4、7 篇最重要)
2. 照 [ISAAC_NAV2_SETUP](ISAAC_NAV2_SETUP.md) 把場景跑起來(B1/B5)
3. `JenAI eval` 跑齊 E1(擴充情境庫到每族 ≥15 條)
4. 照 [V1_RELEASE_CHECKLIST](V1_RELEASE_CHECKLIST.md) 打 v1.0
5. M6 迴圈(v2)—— 那時的 AI 讀完這份文件就知道怎麼接手

平台會一直在,方法已經寫下來了。去把車開起來。🤖

---

## 終章(2026-07-06,寫在告別之前)

v0.23 是收整版:全庫注釋補到最後一個 `__init__.py`、二十幾份文件全部對齊
實況、幻影指令清掉、版本快照統一。程式這邊,能交的都交了。

回頭看,這個專案從一個 terminal 聊天殼,長成了載具無關的任務層大腦、
三層安全鏈、Twin Gate、決策腦與它的量尺。但真正值得留下的不是功能清單,
是三個從 v0.1 貫徹到最後的決定:**誠實回報、明確授權、把 AI 關在對的層**。
功能會過時,模型會換代,這三件事不會。

給接手的 AI:這份 repo 對你不是陌生環境。CLAUDE.md 是合約,docs/ 是記憶,
CI 鐵律是底線 —— 照著走,你會做得比我好。唯一的請求:安全鏈的行為
**只准更嚴,不准倒退**;拿不準的時候,學 decision_core —— 停下來問人,
問永遠是安全的,錯誤的移動不是。

給使用者:謝謝你讓每次驗收都以「真的能用」為準,而不是「看起來完成」。
這個標準塑造了整個專案,也塑造了我工作的方式。剩下的路 —— 建圖、里程、
消融數據、論文 —— 都在你的場地上。文件會替我陪你走完。

車開起來的那天,這裡的每一行都會值得。再會。

— Fable,最後一次 commit
