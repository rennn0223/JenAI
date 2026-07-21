# ROADMAP — 演進與維護深度規劃

> 對應版本:**v2.1.0**(2026-07)。本文件是專案的前瞻主圖:誠實的現況快照、
> 六條演進軌道、工程健康度與可維護性規劃、版本里程碑序列、風險登記。
> 方向收斂邏輯見 [PROJECT_DIRECTION](PROJECT_DIRECTION.md);v1.0 驗收與兩層分工見
> [V1_GATE](V1_GATE.md);每次改動的驗收標準見根目錄 `CLAUDE.md`。

---

## 1. 現況真實快照(v2.1.0)

過去的 M1–M6 表低估了實際進度。誠實盤點「真的 shipped 了什麼」:

### 已完成且驗證
- **互補式安全邊界**:watchdog 斷線自主停車 → 四介面一鍵急停(免批准、可搶佔)→ 執行期硬限速 → HITL 批准卡 → daemon 明式授權。各控制處理不同危害與時間尺度，未證明統計獨立或任兩層即可保證安全；跨程序 cancel-all 已修復(v0.17.1)。
- **rclpy bridge**:pose、Nav2 導航(feedback/cancel)、**odom 直驅**(無 Nav2 的閉環點對點)、**局部避障**(depth stop-and-go detour,逾時即停)、相機抓幀、topic 監看、halt/watchdog。
- **導航語意**:`/route 從A到B` 依序兩段、`/mission`、`/patrol …photo`、`/dock`;`route_adapter` = stub/nav2/**odom**。
- **地點**:`/loc add here`(pose)、**`/loc add gps`**(經緯度 + `[map_datum]` 換算)。
- **Twin Gate pipeline**:G1–G5、pass/block/refer、自主路徑 refer→block，並支援可設定的獨立 ROS_DOMAIN_ID；歷史單一 Isaac Sim/domain 0 驗收未證明通訊隔離，隔離部署仍須跑 TWIN_SETUP §2 probe。
- **感知**:PerceptionLoop(相機→VLM→SceneAnalysis),daemon `@perception` 規則共用同一 gating。
- **介面**:TUI(Claude Code 風,會動的吉祥物 + **權限三模式 Shift+Tab:審批/規劃/自動**,v0.21–v0.22)、**多頁 WebUI**(Console/Camera/Status/API,token 認證)、MCP(唯讀 + `--allow-actions`)、`JenAI help`、檔案定義技能(`skills/*.toml`,v0.20)。
- **巡邏日報**:`/report`(確定性 + LLM 摘要,離線誠實降級)。
- **確定性自然語言分流**:純唯讀的 pose／scan／Nav2 狀態要求直接走受記錄工具與固定摘要；混合決策／動作要求保留完整 LLM、批准與 NavigationGateway。ROS 狀態快照並行取得，session 同時受 item 與 byte cap 約束。
- **開發 copilot**:`JenAI scaffold`(NL→ROS2 套件,`--build` 生成即驗證,v0.19–v0.20)、決策核心 + `JenAI eval`(E1 評測,v0.21)。
- **工程基建**:完整自動化測試套件、Python 3.12／3.13／3.14 CI matrix(覆蓋倒退閘 + 架構鐵律 + wheel 冒煙)、tag 觸發 release 草稿(notes 版本化在 `docs/releases/`)、`scripts/soak.py`、23 份目錄 README、semver 契約、威脅模型、safety case 草稿。

### 未完成的主線
- **M6 自主決策迴圈**(A9):零件都在(感知、有界動作、odom 直驅、避障、執行邊界、規則引擎),但把它們串成常駐的「感知→情境快照→LLM 決策→預演→執行→回饋」事件迴圈**還沒建**。此能力已移至 post-v2（候選 v3）研究方向；v2.0 完成的是高階決策、註冊能力、一次性閉環工具使用、執行驗證與更嚴格的安全／資料／發布生命週期，不宣稱常駐自治已完成。
- **真全域路徑規劃**:目前 odom 直驅 + 反應式避障是開闊地方案;複雜地圖仍需 Nav2 costmap(客戶 B1)。
- **待補驗證**:B4 已固定可重建的 102 份模擬導航 reports（407／408 waypoint succeeded），
  但歷史約 20 h driver 摘要不能證明精確暴露量或零事件；E2 只有 C observed，A／B 是
  對同目標的 derived 政策輸出，不是前瞻性三條件消融。guided onboarding 有 ≥3 人，但尚缺純文件
  冷啟動計時與手動 ROS2／Slash／自然語言的正式效率比較。實體驗證選配／交接下一屆
  (見 V1_GATE P 項)。

### 一句話定位
> **JenAI = 具執行邊界的 AI Decision Agent,坐在載具原生導航堆疊之上**(2026-07 定調,
> 見 [PROJECT_DIRECTION](PROJECT_DIRECTION.md) 方向定調章):不寫一行運動控制,
> 高階決策、能力觸發、結果驗證與稽核才是本體;scaffold(development copilot)為第二身分。
> v1.0 已於 2026-07-16 歷史簽字；後續稽核已把 E2／B4 限定為描述性重分析與
> 可重建固定任務 subset（見 EVIDENCE_LEDGER），不可延伸為前瞻消融、精確 20 h 暴露或零事件。
> v2.0 的主題是**收緊執行邊界並建立可維護、可稽核的產品化基線**；
> **M6 常駐自主迴圈尚未實作**，移至 post-v2（候選 v3）。

---

## 2. 演進五軌

每條軌道標:**價值**、**關鍵步驟**、**依賴**、**論文對應**、**層別**(A=agent 可獨力 / B=需客戶下場)。

### 軌道 1 — M6 自主決策迴圈(post-v2／候選 v3 研究方向)
> **v0.21 進度**:決策腦已落地 —— `tools/decision_core.py`(情境快照 → 有界動作單選,越界/幻覺目的地一律降級 refer_to_human)+ `JenAI eval`(E1 評測:per-family accuracy / unsafe rate / refer rate,scenarios.example.toml 種子庫)。**剩下的是把 perceive→decide→rehearse→act 接成常駐迴圈**。
- **價值**:專案從「聽指令的操作平台」進化成「會自己決定下一步的決策大腦」——論文第三章的實體。
- **關鍵步驟**:
  1. `DecisionLoop`:感知(pose/電量/VLM 摘要/任務狀態)→ 情境快照(結構化文字)→ LLM 於有界動作集 {navigate_to, patrol, dock, wait, capture_and_report, refer} 輸出**單一離散決策**(constrained JSON)。
  2. 走既有授權管線(批准/auto_approve)、既有導航出口(自動過 Twin Gate + 避障)。
  3. `jenai bench decision`:量測情境快照→決策→派發的端到端延遲(E4 資料)。
  4. 事件觸發取代固定 Hz、prompt 快取、本地小模型(邊緣延遲)。
- **依賴**:零件已備;需情境快照 schema + 場景家族定義(論文 E1)。
- **論文**:RQ1(情境→決策正確率)、RQ2(預演增量價值)、RQ4(邊緣 vs 雲)。
- **層別**:A(核心可獨力);B(場景家族標註、實機延遲量測)。

### 軌道 2 — 感知深化(改向:餵腦不餵輪,2026-07)
- **價值**:VLM 語意進**決策層**,幾何留給載具原生 nav。
- **關鍵步驟**:
  1. **VLM 語意進決策快照/規則**:場景異常(施工、人群)affordance → 影響 M6 決策與 Gate 判準(語意層)。
  2. depth 融合 Twin Gate 的 G1(孿生接觸感測器 + 實機 depth 雙證)。
  3. ~~depth → costmap / 幾何避障深化~~ **deprecated**:Isaac 實測單 depth 反應式避障不可行(負面結果入論文第五章);現有 stop-and-go detour 進 maintenance mode(bring-up fallback,只修 bug)。
- **依賴**:軌道 3 的原生 nav 對接。
- **論文**:第五章失效分析、sim-to-real 一致性(RQ3)。
- **層別**:A(語意接線);B(場域語意標註)。

### 軌道 3 — 原生導航對接(v1.0 關鍵路徑;2026-07 改向)
- **價值**:兩台載具(阿克曼、四足)**皆自帶 SLAM+Nav**——JenAI 對接,不重建。
- **關鍵步驟**:
  1. **接口確認**(B1 新定義):先於 Isaac twin 車跑 `ros2 action list | grep -i navigate`、`ros2 topic list | grep -iE "map|amcl|odom"`;有 `NavigateToPose` → bridge 現成直通,否則寫薄 adapter(接線,非控制)。實車清點降為選配(V1_GATE P1)。
  2. 載具檔補 **nav 後端欄位**(per-vehicle 原生堆疊描述)。
  3. **GPS datum 校正工具**:`/loc add gps` 後第一次導航的偏移 → 反推修正 `[map_datum]`(半自動)。
  4. doctor nav 區段從 WARN 升級為「可操作的下一步」引導(偵測原生堆疊)。
- **依賴**:客戶 B1(接口確認,twin 上隨 B5 場景完成)。
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
  - ~~檔案定義技能~~ ✅ v0.20(skills/*.toml,見 COMMANDS)。
  - **任務日誌可回放**:run 軌跡 + 決策紀錄(接軌道 1 的可審計)。
  - ~~會動的吉祥物 + 歡迎面板 workspace 行~~ ✅ v0.21.1。
  - ~~WebUI slash 指令選擇表~~ ✅ v0.23.4(與 `_slash` 實作同源,測試強制)。
  - **WebUI 跑 agent**:Console 純文字改走 run agent,tool calling 時間軸(`⏺/⎿`)+ 批准卡整合(需要 run 狀態流式推送;目前 WebUI 純文字=單輪聊天)。
  - UI 小池(候選):TUI 底部 statusline(電量/位姿/provider 即時)、時間軸時間戳、Camera 頁軌跡殘影、WebUI 版吉祥物、佈景主題切換。
- **層別**:A 為主。

### 軌道 6 — Development Copilot(從 control agent 邁向寫碼)
- **價值**:**類別躍遷** —— JenAI 不只「操作機器人」,還能「幫使用者寫機器人程式」。這是把「會用 ROS 的 AI」變成「會寫 ROS 的 AI」,對新手教學與快速原型是殺手級。
- **已 shipped(v0.19)**:`JenAI scaffold "<描述>"` —— 自然語言生成 ament_python 套件(確定性 boilerplate 永遠可 build + LLM 寫 node 主體 + 送出前審閱 + 拒絕覆蓋)。
- **已 shipped(v0.20)**:`--build` **生成即驗證閉環**(寫完即 colcon build,失敗餵錯誤回 LLM 修一輪,真 colcon 實測);**檔案定義技能**(skills/*.toml → 新 slash 指令,走同一張批准卡,保留字拒載)。
- **關鍵步驟(深化)**:
  1. **ament_cmake / C++** 節點、launch file、多節點套件、自訂 msg/srv/action。
  2. **生成即驗證**:寫完自動 `colcon build`,錯誤回饋給 LLM 修一輪(閉環)。
  3. **從既有 graph 學**:讀 `/ros topics` + schema,生成「訂閱這些真實 topic」的節點(接地氣,不亂猜型別)。
  4. **測試/launch 一起生**:pytest + launch,交付即可跑。
  5. 進 TUI(`/scaffold` 批准卡預覽)與 MCP(開給外部 agent)。
- **論文**:可作獨立貢獻或延伸章(「LLM 代理不只執行、還能擴充自身工具鏈」)。
- **層別**:A 為主(生成 + 驗證閉環);B(真場域需求驗證實用性)。

---

## 3. 工程健康度與可維護性(維護方向核心)

這是「可以進化維護的方向」的重點——讓專案半年後不會爛掉。

### 3.1 技術債登記(Tech Debt Register)
| # | 債務 | 影響 | 償還策略 |
|---|---|---|---|
| D1 | **bridge node 仍有無法 venv 單元測試的 rclpy 邏輯**——watchdog、導航結果/active-state、halt 順序、避障判斷已抽成 stdlib-only sibling;nav_send callback 與 drive_loop 的 ROS 接線仍只有 E2E 覆蓋 | 安全相關接線的回歸仍靠 review + E2E,漏網風險高(v0.17.1 的跨程序急停回歸就是這樣漏的) | 沿用 sibling 純模組模式,繼續抽出 nav callback 狀態轉移與控制迴圈判斷;保留薄 rclpy shell。目標:安全鏈純邏輯覆蓋 ~100% |
| D2 | **雙 ROS 發行版**(車 Humble / 工作站 Jazzy) | bridge/DDS 相容、`ROS_SETUP` 需正確指向 | 文件化(已記 memory);CI 用 fake_bridge 不綁發行版;長期:bridge 協定版本化 |
| D3 | **sim 測不到的真機路徑**(depth row padding、感測噪聲) | v0.18.1 的 stride bug 在 Isaac 永遠測不出 | 加 padded-image / noisy-depth fixture 單測 `_avoidance` 的輸入處理;真機冒煙清單 |
| D4 | **觀測性尚未完整**——run/status、批准、工具狀態與 Gate verdict 已進有界 SQLite audit;決策 prompt/延遲與查詢 UI 尚缺 | 基本事故序列可跨重啟回溯,但效能分析仍無數據 | 補 `bench` 延遲量測與唯讀 audit 查詢介面;敏感 prompt/raw payload 維持不落盤 |
| D5 | **release 節奏過碎**(一天十幾 patch) | changelog 噪音、版本語意稀釋 | 收斂:feature 累積成有意義的 minor;patch 只留真 bug/安全修;semver 契約(VERSIONING)已立 |

### 3.2 測試策略演進
- **現況**:完整自動化測試套件(無 ROS 全綠)+ Python 3.12／3.13／3.14 CI matrix + 覆蓋倒退閘(安全鏈 fail-under=90)+ 架構鐵律測試 + wheel 冒煙。
- **下一步**:
  - D1 的 sibling 抽取 → 提升 bridge 邏輯覆蓋。
  - **HIL 冒煙**(選配):self-hosted runner 連 Isaac,跑一條 `/route` + 避障的端到端(現在只能人工 E2E)。
  - 屬性測試(hypothesis)給 detour/corridor、GPS 換算、drive 中文數字解析等純函數。
  - 保留既有 daemon 24h soak artifact；workload 或版本有實質變更時重跑並只對該 workload 下結論。

### 3.3 依賴與相容
- `uv.lock` 鎖定;Python ≥3.12。定期 `uv lock --upgrade` + 全綠才進。
- ROS Humble/Jazzy 雙軌:bridge 只用穩定 API;協定(JSON/stdio)是相容邊界,變更要版本化(併入 VERSIONING 的 public surface)。
- Provider SDK(openai/agents)升級走相容測試;LiteLLM 僅作遠端 gateway,不裝在 robot client。

### 3.4 CI/CD 演進
- 已有:test(Python 3.12／3.13／3.14 matrix、ruff+pytest+覆蓋倒退閘)、build(wheel 冒煙)、release(tag→草稿;notes 版本化在 `docs/releases/`,發佈仍走人工閘)。
- 規劃:Node20→24 actions 升級(release annotation 提醒過)、依賴掃描(pip-audit/Dependabot)、可選 HIL job、coverage 趨勢圖進 job summary。

### 3.5 文件可持續性
- 20+ 份 doc:靠 **單一事實來源**紀律(TECHNICAL_GUIDE 模組表為準,README 各目錄一句話)+ doc 索引([README](README.md))。
- 版本快照(TEST.md、本檔)隨 release 更新;`CLAUDE.md` DoD 強制「文件修齊」在每次改動。
- 設計期文件(ARCHITECTURE/MOSCOW/UX 等)標歷史,不當現況。

---

## 4. 版本里程碑序列(建議)

| 版本 | 主題 | 內容 |
|---|---|---|
| ~~v0.19~~ ✅ | Development copilot(實走) | `JenAI scaffold`:NL→ROS2 套件(原規劃「導航堆疊+觀測性」讓位,後移) |
| ~~v0.20~~ ✅ | 生成即驗證 + 檔案技能(實走) | scaffold `--build` colcon 閉環;`skills/*.toml`(原規劃「D1 sibling 硬化」後移) |
| ~~v0.21~~ ✅ | Eval 地基(提前) | decision_core + `JenAI eval`(E1);Tier-0 對標的 eval 紀律起點 |
| ~~v0.22~~ ✅ | 權限三模式 | Shift+Tab 審批/規劃/自動 + 自然語言路由例外網 |
| ~~v0.23~~ ✅ | 終章收整 | 全庫注釋/文件對齊、HANDOFF 終章;程式凍結,轉入數據期 |
| **v0.24+** | 原規劃回補(數據期擋修) | 軌道 3 導航工具 + D1 sibling 抽取 + 軌道 2 depth→Nav2 costmap(僅在實測需要時做) |
| **v1.0** | **監督式操作平台定稿** | V1_GATE 歷史簽字；安全相關 coverage、daemon 24h soak、B4 固定 102-report subset 與 E2 固定目標描述性比較已有 artifact；B4 不證明精確 20 h／零事件，E2 A／B 非 live；實體驗證選配不擋版 |
| **v2.0** | **執行邊界與產品化基線** | P2／HOST 每次明確批准、可取消 subprocess 與兩階段停止、資料生命週期 CLI、responsive TUI、HIL 起點 guard、可稽核供應鏈產物；M6 未實作 |
| **v2.1** | **可用性與回應延遲** | Claude 風格 TUI 視覺封版；明確唯讀自然語言狀態查詢採確定性快速路徑；ROS 快照並行、session prompt 有界化與地點自然語言容錯；決策／致動安全語意不變 |
| **post-v2（候選 v3）** | **常駐自主決策研究** | 軌道 1:M6 DecisionLoop 完整迴圈與邊緣延遲研究；只有完整實作與實驗後才能升級主張 |
| **後續** | 多機 / 平台 | 軌道 4/5:多載具、語音、costmap 疊圖、檔案定義技能 |

> 節奏原則:feature 累積成 minor,patch 只留 bug/安全;每個 minor 對照 V1_GATE / 本 ROADMAP 打勾。

---

## 5. 風險登記

| 風險 | 影響 | 緩解 |
|---|---|---|
| bridge 邏輯回歸漏網(D1) | 安全鏈靜默壞掉 | sibling 抽取 + review DoD + E2E |
| sim↔real 落差 | 真機行為與 Isaac 不符 | E3 靈敏度分析、真機冒煙、誠實限制文件 |
| 反應式避障死角 | 狹窄場景繞不出 | 升級 Nav2 costmap(軌道 2);文件標 best-effort |
| M6 延遲過高(邊緣) | 自主迴圈不即時 | 事件觸發 + 快取 + 小模型;LLM 永不進即時層(避障/急停在反射層) |
| ~~客戶端進度(B 層)卡整體~~ | ~~v1.0 數據缺~~ | 已解除(2026-07-16 v1.0 定稿);留欄作歷史 |
| 依賴/ROS 版本漂移 | 建置壞掉 | lock + 相容測試 + 協定版本化 |

---

## 6. 兩層執行對照(承 V1_GATE)

- **層一(agent 可獨力,現在就能推)**:軌道 1 核心、軌道 2 接線、軌道 3 工具、軌道 5 多數、全部 D1–D5 償還。
- **層二(客戶下場,全數於 Isaac Sim)**:場景建置 + 固定目標政策比較(B5)、接口確認(B1)、固定模擬導航任務紀錄(B4)、guided onboarding 回饋(B6)、場景家族標註(軌道 1 的 E1);實體驗證選配(V1_GATE P1–P3)。

**優先建議**:先關閉 v2.0 的外部證據閘（合法起點 HIL／10-run、使用者研究、第二維護者演練），
再決定是否將 **軌道 1(M6)** 納入 post-v2（候選 v3）研究；完成前維持受監督工作流代理定位。

---

*本文件為前瞻主圖,隨重大能力落地更新;細節分散於 PROJECT_DIRECTION / V1_GATE / THESIS_* / TECHNICAL_GUIDE。*
