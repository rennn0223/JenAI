# SAFETY_CASE — 危害分析與防護對應(HARA-lite 草稿)

> V1_GATE A7。輕量版危害分析:每個危害 → 成因 → 防護層 → 驗證證據 → 殘餘風險。
> 防護層代號:**R** 反射層(不經 LLM/網路)、**G** Twin Gate、**H** HITL 批准、**P** 程序(人的規範)。
> v1.0(2026-07-16)完稿:sim 驗證項全數填畢;殘餘 🅿 標記 = 實體場域選配項(V1_GATE P1–P3),交接下一屆。
>
> **免責聲明**:JenAI 是研究平台,不是經認證的功能安全系統。部署前提:隔離 LAN、
> 有人監督、場域內人員知情。本文件是誠實的風險盤點,不是安全認證。

## 系統邊界

會動的東西:一台 Ackermann 載具(未來:四足)。JenAI 下的指令最終都收斂到兩條路:
`cmd_vel`(直接速度)與 Nav2 goal(導航)。**任何危害分析都只需要盯住這兩條路。**

## 危害清單

| # | 危害 | 成因 | 防護 | 驗證證據 | 殘餘風險 |
|---|---|---|---|---|---|
| H1 | 撞到人/障礙物 | 導航中環境變化;規劃錯誤 | **R**:避障是 Nav2/controller 的事(毫秒級,LLM 不在迴路);odom 避障 depth 逾時 fail-closed;**G**:goal 先在孿生預演,G1 碰撞即 block | test_architecture、test_avoidance(freshness/走廊)、test_twin_gate;🟩 sim 里程 20h/102 任務 0 事件(2026-07-16,B4);實車避障實測屬選配(P 項) | 感測器盲區、twin 與實景偏差 —— 靠限速與人員知情減輕 |
| H2 | 進入危險區域(樓梯口、充電樁後方) | 目標點合法但路徑/終點不當 | **G**:forbidden zones,G3 進禁區即 block(軌跡採樣);**H**:目標點由人批准 | test_twin_gate(禁區 block、終點在禁區不預演直接 block);✅ sim 禁區清單(`SW-narrow-aisle`)+ E2 消融驗證(2026-07-15:zone_inside 20/20 block、FPR 0/20,見 THESIS_DRAFT 5.4);實車場域清單屬選配(P 項) | 禁區清單不完整;gate 關閉時只剩 H |
| H3 | 失控高速 | LLM 生成過大速度;指令打錯 | **R**:`[vehicle]` 硬夾限在 `/ros pub`、`/ros drive`、`/drive` 的執行路徑,LLM 碰不到夾限值;`/ros drive` 定時自動歸零 | test_ros2_core(夾限)、TEST.md `/drive` 實測 | 夾限值設錯 —— sim 場景以預設夾限實測(B4 20h/0 事件);🅿 選配:實體場域速度上限進 config(P 項) |
| H4 | 通訊失效後持續移動 | TUI/daemon 崩潰、網路斷、行程被 kill | **R**:bridge watchdog —— client 斷線/卡死 >6s 自主停車,每 2s 重發 halt;watchdog 武裝失敗 = 啟動失敗(絕不發無保護 bridge) | test_bridge_client(武裝失敗擋啟動)、v0.8 實測(watchdog 斷線停車) | DDS 本身斷連時 halt 到不了 —— 硬體 estop 是最後防線(🅿 選配:實車硬體 estop 配置,P 項;sim 驗證以 watchdog 斷線停車 + /stop 實測覆蓋) |
| H5 | AI 聽錯/亂決策(意圖層錯誤) | LLM 誤解自然語言;VLM 誤判場景 | **H**:敏感操作一律批准卡;daemon 自主動作需 `auto_approve`+nav2 明式授權;感知規則走同一套 gating 無捷徑;twin refer 在自主路徑視為 block | test_daemon(twin refer → NOT moved、confidence 亂填不觸發)、批准機制橫切驗收(TEST.md) | 人批准了「聽起來對」的錯指令 —— 由 G 接手(執行層) |
| H6 | 緊急停止失效 | 停止路徑本身出 bug;停止被排隊 | **R+設計**:`/stop` 免批准、Esc 取消不了停止本身、halt publisher 預建、halt 先送零速度再做可能耗時的跨程序 cancel-all,最後再補零速度;WebUI STOP 免 token、不等待 body 且撤銷舊確認 | test_safety_order + 安全鏈覆蓋 CI 倒退閘;halt 失敗誠實回報(test_daemon);TEST.md `/stop` 實測 | 多重防護後仍是軟體 —— 硬體 estop 為最後防線 |
| H7 | 電量耗盡途中拋錨 | 任務過長、忘記回充 | **R**:daemon 電量規則自動回充(`goto Dock`);**P**:巡邏日報記錄 | rules.example.toml + test_daemon(goto gating) | 電量 topic 不準;dock 點未建(誠實提示) |
| H8 | 未授權者操作(LAN) | WebUI 曝露、MCP 濫用 | token 認證(401 不洩 token)、MCP 預設唯讀、`/shell` 批准卡 | test_webui auth 6 例、THREAT_MODEL.md | 見 THREAT_MODEL「明確不在範圍」 |

## 分層防護的邏輯(為什麼這樣夠)

- **H(批准)攔意圖層錯誤**:AI 聽錯人話 → 人在批准卡看到真實動作內容(server 端持有,瀏覽器改不了)
- **G(孿生)攔執行層錯誤**:指令沒錯、執行會出事 → 預演先撞;**啟用時絕不靜默放行**
- **R(反射)攔一切上層失效**:上面全掛,watchdog/夾限/estop 照常 —— 因為它們**不 import LLM**(CI 強制)
- 三層正交:任何單層失效不塌,兩層失效仍有最後一層

## 事件記錄(P 層程序)

任何非預期的實體行為(急停觸發、擦撞、誤闖)→ 開 GitHub issue,標 `incident`,附:時間、
指令來源(TUI/WebUI/daemon)、`reports/` 對應 log、當時 doctor 輸出。累積供 B4 里程統計。

## 完稿條件(對應 V1_GATE)

- 🟩 B4 模擬里程數據(2026-07-16 完成:20.0h / 102 趟 patrol / 0 安全事件;唯一任務級失敗 = Nav2 發現逾時誠實回報,#92 已修,見 V1_GATE B4)
- 🟩 B5 禁區清單 + gate 消融數據(E2 2026-07-15:硬陷阱 SIR 100%、FPR 0%,見 THESIS_DRAFT 5.4)
- 🟨 B6 onboarding 指導試用(2026-07-16,學弟妹 ≥3 順跑零卡點);純冷啟動與正式碼表未執行,不可作效率量化證據
- 🅿 場域安全速度上限與硬體 estop 配置(實體選配,P 項)
