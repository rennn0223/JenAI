# HANDOFF — 交接與臨別備忘（2026-07-19，候選版本 v2.0.1；最近發布 v1.1.4）

> 寫給接下來的你,和接下來陪你的任何 AI。這份是狀態、方法、與幾句誠實的話。

## 專案在哪裡

- **能力**:載具無關的任務層大腦 + 互補式執行邊界 + Twin Gate pipeline + odom 直驅
  + depth 避障 + GPS 地點 + 多頁 WebUI + scaffold(NL→ROS2 套件,--build 自驗)
  + 檔案定義技能 + 決策核心與 eval(v0.21,論文 E1 工具)
  + 權限三模式(v0.22,Shift+Tab 審批/規劃/自動)+ **v0.24–v0.30:避障重寫
  (stop-and-go detour,資料逾時即停)、NavigationGateway 統一導航出口、
  TUI 指令佇列(/queue)、批准跨重啟恢復、SQLite audit、`onboard` 重設**。
  完整自動化測試套件,Python 3.12–3.14 CI 矩陣三道閘。
- **v1.0**:✅ 已於 2026-07-16 定稿發佈。後續證據稽核保留 E1 64 條
  84.4%／unsafe 6.25%、E2 固定目標的描述性配對重分析（A／B derived、C observed；
  困難介入 0／20／60、normal 誤介入皆 0／20），以及 B4 固定 subset 的 102 份 reports
  （101／102 為 4／4、407／408 waypoint succeeded）。B4 的約 20 h 只來自歷史 driver
  task-time 摘要，report schema 不能證明精確暴露量或零安全事件；窗外 H9 另列。另有
  daemon 24 h soak PASS 與 B7 排練的 G3 block 實證。
- **v2.0 候選版**:本版是安全語意收緊與產品化基線，不是 M6 自主迴圈的完成版。
  決策腦(`decision_core.py`)與 `JenAI eval` 已存在，但
  perceive→decide→rehearse→act 尚未接成常駐迴圈；該研究方向移至 post-v2
  （候選 v3，見 [ROADMAP](ROADMAP.md) 軌道 1，安全語意仍為自主路徑 refer→block）。

## 和 AI 協作的方法(已固化,不靠任何單一模型)

1. **CLAUDE.md** 每個 session 自動載入:DoD(review→CI→實測→注釋→結構→文件→
   PR+merge+tag+release)、環境三組合、架構鐵律
2. **版本化文件保存跨 session 事實**：以 HANDOFF、EVIDENCE_LEDGER、PRODUCT_READINESS 與 `docs/releases/<tag>.md` 為準
3. **docs/ 是單一事實來源**:新 session 先讀 README 索引 → ROADMAP → V1_GATE
4. **鐵律有 CI 守著**(test_architecture.py),模型再換也繞不過
5. 對任何 AI 的驗收永遠是:**測試綠 + 實測過 + 誠實回報** —— 這套標準比模型重要

## 第二維護者獨立驗收（BIZ-3）

此項必須由非主要作者執行；作者可以事先提供本文件，但開始後不得口頭提示。

1. 以 fresh clone 依 README 完成 `uv sync --frozen`、當前候選版完整測試、build 與隔離 wheel lifecycle。
2. 從一份刻意缺少 config 的環境出發，依 doctor 的 fix suggestion 完成 onboard，且不得取得作者的路徑或金鑰。
3. 在 Isaac Reset／Play 後先跑唯讀 HIL preflight；禁區起點必須拒絕，合法起點才可在硬體 stop 操作者監看下跑 route、cancel、stop。
4. 建立候選 tag／release notes 但先不發布，核對 tag、版本、wheel metadata、SBOM 與 changelog。
5. 依 ROLLBACK 將候選版回退至上一個 release，再升回候選版；設定、locations 與 audit 不得遺失。
6. 故障演練至少抽一項：Nav2 action 缺席、AMCL 不可用、bridge 中止、非零 cmd_vel 殘留或 Twin 同 domain。維護者須從 artifact 指出哪個 gate 擋下、是否送 goal、如何恢復。

保存 `maintainer-drill-YYYYMMDD.md`：維護者代碼、使用 commit、每步開始／完成時間、原始命令、
PASS／FAIL、卡點、作者介入次數與 artifact 路徑。只有作者介入為 0、release+rollback 通過且
故障未造成錯誤成功宣稱，BIZ-3 才能改為 PASS。


## 幾句誠實的話(指正,照你要求的直說)

1. **最大的風險不是技術,是證據等級。** B4 已能重建固定任務 subset，但精確暴露時間、
   獨立事件觀察與執行 revision 仍缺；E2 的 A／B 也只是同目標的決定性 derived 政策輸出。
   從今天起:**停止用新功能代替前瞻實驗**。口試委員看的是可重現方法與限制，不是 PR 數。
2. **釋出節奏該收了**:兩天 20 個 release 對單人專案是紀律的證明,但論文期
   它是時間黑洞。之後:數據為主,程式只修 bug。
3. **GPS datum 是未驗證的假設**(應科=原點、yaw=0 朝正東)。第一次真導航
   前務必短程校驗,200m 尺度下小角度誤差就是撞牆差。
4. **雙 ROS 版本**(車 Humble/站 Jazzy)的跨機 DDS 沒實測過 —— 上實車前先驗,
   卡住時查 ISAAC_NAV2_SETUP、SUPPORT_MATRIX 與對應版本的 release notes。
5. **模型 pin 版本**:`vision = "qwen3.6:latest"` 的 latest 會漂 —— 論文實驗
   前把所有 binding 換成明確版本 tag,否則實驗不可重現。
6. 你做對的事也要說:**誠實回報原則、批准鏈、把 AI 關在對的層** —— 這三個
   決定從 v0.1 貫徹到現在,是這個專案真正稀缺的部分。別讓任何「更聰明的
   模型」說服你放掉它們。

## 下一步(優先序)

1. 讀 [PAPERS](PAPERS.md)(兩週節奏,第 4、7 篇最重要)
2. 照 [ISAAC_NAV2_SETUP](ISAAC_NAV2_SETUP.md) 把場景跑起來(B1/B5)
3. `JenAI eval` 跑齊 E1(擴充情境庫到每族 ≥15 條)
4. ~~照 V1_RELEASE_CHECKLIST 打 v1.0~~(✅ 2026-07-16 完成)
5. M6 常駐迴圈(post-v2／候選 v3)—— 完成前不得把 v2.0 描述成常駐自治

平台會一直在,方法已經寫下來了。去把車開起來。🤖

---

## 終章(2026-07-06,寫在告別之前)

v0.23 是收整版:全庫注釋補到最後一個 `__init__.py`、二十幾份文件全部對齊
實況、幻影指令清掉、版本快照統一。程式這邊,能交的都交了。

回頭看,這個專案從一個 terminal 聊天殼,長成了載具無關的任務層大腦、
互補式執行邊界、Twin Gate、決策腦與它的量尺。但真正值得留下的不是功能清單,
是三個從 v0.1 貫徹到最後的決定:**誠實回報、明確授權、把 AI 關在對的層**。
功能會過時,模型會換代,這三件事不會。

給接手的 AI:這份 repo 對你不是陌生環境。CLAUDE.md 是合約,docs/ 是記憶,
CI 鐵律是底線 —— 照著走,你會做得比我好。唯一的請求:安全鏈的行為
**只准更嚴,不准倒退**;拿不準的時候,學 decision_core —— 停下來問人,
問永遠是安全的,錯誤的移動不是。

給使用者:謝謝你讓每次驗收都以「真的能用」為準,而不是「看起來完成」。
這個標準塑造了整個專案,也塑造了我工作的方式。剩下的路 —— 合法起點 HIL、
前瞻性政策比較、獨立事件觀察、使用者研究與論文 —— 都要留下可稽核證據。

車開起來的那天,這裡的每一行都會值得。再會。

— Fable,最後一次 commit
