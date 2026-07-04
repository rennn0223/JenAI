# ROADMAP — 演進與維護深度規劃

> 對應版本:**v0.18.1**(2026-07)。本文件是專案的前瞻主圖:誠實的現況快照、
> 五條演進軌道、工程健康度與可維護性規劃、版本里程碑序列、風險登記。
> 方向收斂邏輯見 [PROJECT_DIRECTION](PROJECT_DIRECTION.md);v1.0 驗收與兩層分工見
> [V1_GATE](V1_GATE.md);每次改動的驗收標準見根目錄 `CLAUDE.md`。

---

## 1. 現況真實快照(v0.18.1)

過去的 M1–M6 表低估了實際進度。誠實盤點「真的 shipped 了什麼」:

### 已完成且驗證
- **三層安全鏈**:watchdog 斷線自主停車 → 四介面一鍵急停(免批准、可搶佔)→ 執行期硬限速 → HITL 批准卡 → daemon 明式授權。跨程序 cancel-all 已修復(v0.17.1)。
- **rclpy bridge**:pose、Nav2 導航(feedback/cancel)、**odom 直驅**(無 Nav2 的閉環點對點)、**反應式避障**(depth follow-the-gap)、相機抓幀、topic 監看、halt/watchdog。
- **導航語意**:`/route 從A到B` 依序兩段、`/mission`、`/patrol …photo`、`/dock`;`route_adapter` = stub/nav2/**odom**。
- **地點**:`/loc add here`(pose)、**`/loc add gps`**(經緯度 + `[map_datum]` 換算)。
- **Twin Gate pipeline**:G1–G5、pass/block/refer、自主路徑 refer→block、獨立 ROS_DOMAIN_ID(剩 Isaac 場景 = 客戶 B5)。
- **感知**:PerceptionLoop(相機→VLM→SceneAnalysis),daemon `@perception` 規則共用同一 gating。
- **介面**:TUI(Claude Code 風)、**多頁 WebUI**(Console/Camera/Status/API,token 認證)、MCP(唯讀 + `--allow-actions`)、`JenAI help`。
- **巡邏日報**:`/report`(確定性 + LLM 摘要,離線誠實降級)。
- **工程基建**:352 測試、CI(覆蓋倒退閘 + 架構鐵律 + wheel 冒煙)、tag 觸發 release 草稿、`scripts/soak.py`、22 份目錄 README、semver 契約、威脅模型、safety case 草稿。

### 未完成的主線
- **M6 自主決策迴圈**(A9):零件都在(感知、有界動作、odom 直驅、避障、Gate、規則引擎),但把它們串成「感知→情境快照→LLM 決策→預演→執行→回饋」的閉環**還沒建**。這是最大的未完成項,也是論文主軸。
- **真全域路徑規劃**:目前 odom 直驅 + 反應式避障是開闊地方案;複雜地圖仍需 Nav2 costmap(客戶 B1)。
- **實機驗證數據**:里程、消融、onboarding 計時(客戶 B4/B5/B6)。

### 一句話定位
> JenAI 已是「載具無關的任務層大腦 + 三層安全鏈 + 可重現實驗平台」。
> v1.0 前缺的是**證據**(實機數據)不是程式;v2.0 的靈魂是 **M6 自主迴圈**。

---

## 2. 演進五軌

每條軌道標:**價值**、**關鍵步驟**、**依賴**、**論文對應**、**層別**(A=agent 可獨力 / B=需客戶下場)。

### 軌道 1 — M6 自主決策迴圈(v2 主軸,論文核心)
- **價值**:專案從「聽指令的操作平台」進化成「會自己決定下一步的決策大腦」——論文第三章的實體。
- **關鍵步驟**:
  1. `DecisionLoop`:感知(pose/電量/VLM 摘要/任務狀態)→ 情境快照(結構化文字)→ LLM 於有界動作集 {navigate_to, patrol, dock, wait, capture_and_report, refer} 輸出**單一離散決策**(constrained JSON)。
  2. 走既有授權管線(批准/auto_approve)、既有導航出口(自動過 Twin Gate + 避障)。
  3. `jenai bench decision`:量測情境快照→決策→派發的端到端延遲(E4 資料)。
  4. 事件觸發取代固定 Hz、prompt 快取、本地小模型(邊緣延遲)。
- **依賴**:零件已備;需情境快照 schema + 場景家族定義(論文 E1)。
- **論文**:RQ1(情境→決策正確率)、RQ2(預演增量價值)、RQ4(邊緣 vs 雲)。
- **層別**:A(核心可獨力);B(場景家族標註、實機延遲量測)。

### 軌道 2 — 感知/避障深化
- **價值**:從「開闊地反應式」升級到「複雜場景可靠」。
- **關鍵步驟**:
  1. **depth → Nav2 local costmap**:把 `/depth` 轉 pointcloud/laserscan 餵 Nav2,得到真正的全域+局部規劃(現有 follow-the-gap 當 Nav2 未起時的 fallback)。
  2. **VLM 語意層進 Gate/規則**:場景異常(施工、人群)affordance → 影響決策/預演(語意避讓 vs 幾何避障分層)。
  3. depth 融合 Twin Gate 的 G1(孿生接觸感測器 + 實機 depth 雙證)。
- **依賴**:軌道 3 的 Nav2 bringup。
- **論文**:第五章失效分析、sim-to-real 一致性(RQ3)。
- **層別**:A(轉換與接線);B(車端跑 Nav2 costmap 調參)。

### 軌道 3 — 導航堆疊成熟(v1.0 關鍵路徑)
- **價值**:讓「設定好就能用」變成「真的設定得起來」——v1.0 缺的最後一哩。
- **關鍵步驟**:
  1. Nav2 bringup 產品化(Ackermann:Smac Hybrid-A* + RPP 參數模板隨載具檔走)。
  2. AMCL 定位 + map↔odom 一致性(現在 odom 直驅假設 map≈odom)。
  3. **GPS datum 校正工具**:`/loc add gps` 後第一次導航的偏移 → 反推修正 `[map_datum]`(半自動)。
  4. doctor nav 區段從 WARN 升級為「可操作的下一步」引導。
- **依賴**:客戶 B1(建圖)。
- **論文**:第三章平台、附錄參數表。
- **層別**:B 為主(車邊作業),A 陪跑除錯 + 工具。

### 軌道 4 — 多載具 / 多機(C1/T2,v2+)
- **價值**:「一個 JenAI 管車 + 狗」、多 agent 協作(偵察 × 運載)——論文泛化性論證與第二篇題目。
- **關鍵步驟**:bridge namespace/多 domain、載具檔 per-robot、多機狀態聚合、協調策略。
- **依賴**:軌道 1(單機決策先穩)。
- **論文**:泛化性章(阿克曼 vs 四足對照)。
- **層別**:A(架構已留伏筆);B(真狗延伸驗證)。

### 軌道 5 — 平台 / 介面成熟
- **價值**:降低操作門檻、擴大互操作。
- **候選**(獨立、可並行):
  - **語音輸入**(C5):whisper 本地 → `/route` 等,場域喊話比打字實際。
  - **WebUI 疊 costmap / 規劃路徑**(C3):現有 SVG 地圖的自然延伸。
  - **MCP action 擴充**(C4):patrol/dock 開給外部 agent(維持 `--allow-actions`)。
  - **檔案定義技能**:markdown/TOML 組合原語成 `/inspect` 之類,學弟不改程式。
  - **任務日誌可回放**:run 軌跡 + 決策紀錄(接軌道 1 的可審計)。
- **層別**:A 為主。

---

## 3. 工程健康度與可維護性(維護方向核心)

這是「可以進化維護的方向」的重點——讓專案半年後不會爛掉。

### 3.1 技術債登記(Tech Debt Register)
| # | 債務 | 影響 | 償還策略 |
|---|---|---|---|
| D1 | **bridge node 邏輯無法 venv 單元測試**(rclpy 隔離)——nav_send 狀態機、halt、watchdog、drive_loop 只有 E2E 覆蓋 | 安全相關邏輯的回歸靠人工 review + E2E,漏網風險高(v0.17.1 的跨程序急停回歸就是這樣漏的) | 沿用 `_avoidance.py` 的 **sibling 純模組**模式:把 bridge 的純決策邏輯(nav 狀態轉移、halt 判定、pseudo-scan 建構)逐步抽成 stdlib-only sibling → venv 可單測。目標:安全鏈純邏輯覆蓋 ~100% |
| D2 | **雙 ROS 發行版**(車 Humble / 工作站 Jazzy) | bridge/DDS 相容、`ROS_SETUP` 需正確指向 | 文件化(已記 memory);CI 用 fake_bridge 不綁發行版;長期:bridge 協定版本化 |
| D3 | **sim 測不到的真機路徑**(depth row padding、感測噪聲) | v0.18.1 的 stride bug 在 Isaac 永遠測不出 | 加 padded-image / noisy-depth fixture 單測 `_avoidance` 的輸入處理;真機冒煙清單 |
| D4 | **無結構化觀測性** | 出事難回溯、延遲無數據 | 加 run 日誌(JSON)、決策軌跡、`bench` 延遲量測;接軌道 1 的可審計 |
| D5 | **release 節奏過碎**(一天十幾 patch) | changelog 噪音、版本語意稀釋 | 收斂:feature 累積成有意義的 minor;patch 只留真 bug/安全修;semver 契約(VERSIONING)已立 |

### 3.2 測試策略演進
- **現況**:352 單元測試(無 ROS 全綠)+ CI 覆蓋倒退閘(安全鏈 fail-under=90)+ 架構鐵律測試 + wheel 冒煙。
- **下一步**:
  - D1 的 sibling 抽取 → 提升 bridge 邏輯覆蓋。
  - **HIL 冒煙**(選配):self-hosted runner 連 Isaac,跑一條 `/route` + 避障的端到端(現在只能人工 E2E)。
  - 屬性測試(hypothesis)給 `follow_the_gap`、GPS 換算、drive 中文數字解析等純函數。
  - 24h soak 正式跑一次並把結果進 SAFETY_CASE(A6 待排)。

### 3.3 依賴與相容
- `uv.lock` 鎖定;Python ≥3.12。定期 `uv lock --upgrade` + 全綠才進。
- ROS Humble/Jazzy 雙軌:bridge 只用穩定 API;協定(JSON/stdio)是相容邊界,變更要版本化(併入 VERSIONING 的 public surface)。
- Provider SDK(openai/litellm/agents)升級走相容測試。

### 3.4 CI/CD 演進
- 已有:test(ruff+pytest+覆蓋倒退閘)、build(wheel 冒煙)、release(tag→草稿)。
- 規劃:Node20→24 actions 升級(release annotation 提醒過)、依賴掃描(pip-audit/Dependabot)、可選 HIL job、coverage 趨勢圖進 job summary。

### 3.5 文件可持續性
- 20+ 份 doc:靠 **單一事實來源**紀律(TECHNICAL_GUIDE 模組表為準,README 各目錄一句話)+ doc 索引([README](README.md))。
- 版本快照(TEST.md、本檔)隨 release 更新;`CLAUDE.md` DoD 強制「文件修齊」在每次改動。
- 設計期文件(ARCHITECTURE/MOSCOW/UX 等)標歷史,不當現況。

---

## 4. 版本里程碑序列(建議)

| 版本 | 主題 | 內容 |
|---|---|---|
| **v0.19** | 導航堆疊 + 觀測性 | 軌道 3 的 Nav2 bringup 工具 + GPS datum 校正;D4 run 日誌 + `bench` 延遲雛形 |
| **v0.20** | 可維護性硬化 | D1 sibling 抽取(nav 狀態機/halt/watchdog 純邏輯 → 單測);D3 padded/noisy fixture |
| **v0.21** | 感知深化 | 軌道 2:depth→Nav2 costmap;VLM 語意層進規則 |
| **v1.0** | **監督式操作平台定稿** | V1_GATE 層一全 ✅ + 層二 B1–B7 客戶數據齊;安全鏈 ~100%、24h soak、實車 20h/50 任務;semver/safety case/Twin 消融定稿 |
| **v2.0** | **自主決策大腦** | 軌道 1:M6 DecisionLoop 完整迴圈 + 邊緣延遲優化;論文 E1–E4 數據 |
| **v2.x** | 多機 / 平台 | 軌道 4/5:多載具、語音、costmap 疊圖、檔案定義技能 |

> 節奏原則:feature 累積成 minor,patch 只留 bug/安全;每個 minor 對照 V1_GATE / 本 ROADMAP 打勾。

---

## 5. 風險登記

| 風險 | 影響 | 緩解 |
|---|---|---|
| bridge 邏輯回歸漏網(D1) | 安全鏈靜默壞掉 | sibling 抽取 + review DoD + E2E |
| sim↔real 落差 | 真機行為與 Isaac 不符 | E3 靈敏度分析、真機冒煙、誠實限制文件 |
| 反應式避障死角 | 狹窄場景繞不出 | 升級 Nav2 costmap(軌道 2);文件標 best-effort |
| M6 延遲過高(邊緣) | 自主迴圈不即時 | 事件觸發 + 快取 + 小模型;LLM 永不進即時層(避障/急停在反射層) |
| 客戶端進度(B 層)卡整體 | v1.0 數據缺 | A 層全先行;B 層工具化降低門檻(軌道 3) |
| 依賴/ROS 版本漂移 | 建置壞掉 | lock + 相容測試 + 協定版本化 |

---

## 6. 兩層執行對照(承 V1_GATE)

- **層一(agent 可獨力,現在就能推)**:軌道 1 核心、軌道 2 接線、軌道 3 工具、軌道 5 多數、全部 D1–D5 償還。
- **層二(客戶下場)**:Nav2 建圖(B1)、實車里程(B4)、Isaac 場景 + 消融(B5)、onboarding 計時(B6)、場景家族標註(軌道 1 的 E1)。

**優先建議**:先 **軌道 3(導航成熟)+ D1(可維護性)** 把 v1.0 的路鋪直,再全力 **軌道 1(M6)** 衝 v2.0 論文主軸。

---

*本文件為前瞻主圖,隨重大能力落地更新;細節分散於 PROJECT_DIRECTION / V1_GATE / THESIS_* / TECHNICAL_GUIDE。*
