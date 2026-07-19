# SAFETY_CASE — 危害分析與防護對應(HARA-lite 草稿)

> V1_GATE A7。輕量版危害分析:每個危害 → 成因 → 防護層 → 驗證證據 → 殘餘風險。
> 防護層代號:**R** 反射層(不經 LLM/網路)、**G** Twin Gate、**H** HITL 批准、**P** 程序(人的規範)。
> 狀態：**研究草稿／PARTIAL**。2026-07-19 證據稽核後，模擬任務完成紀錄與
> 安全事件觀測分開處理；尚無獨立安全觀察者、正式 HARA/FMEA 審查或實體場域驗證。
>
> **免責聲明**:JenAI 是研究平台,不是經認證的功能安全系統。部署前提:隔離 LAN、
> 有人監督、場域內人員知情。本文件是誠實的風險盤點,不是安全認證。

## 系統邊界

會動的東西:一台 Ackermann 載具(未來:四足)。JenAI 下的指令最終都收斂到兩條路:
`cmd_vel`(直接速度)與 Nav2 goal(導航)。**任何危害分析都只需要盯住這兩條路。**

## 危害清單

| # | 危害 | 成因 | 防護 | 驗證證據 | 殘餘風險 |
|---|---|---|---|---|---|
| H1 | 撞到人/障礙物 | 導航中環境變化;規劃錯誤 | **R**:避障是 Nav2/controller 的事(毫秒級,LLM 不在迴路);odom 避障 depth 逾時 fail-closed;**G**:goal 先在孿生預演,G1 碰撞即 block | test_architecture、test_avoidance(freshness/走廊)、test_twin_gate；B4 subset 可重建 102 份 sim 任務報告、407/408 waypoint succeeded，但 report 無 incident 欄且無獨立觀察者，不能作「0 安全事件」證據；HIL-FS-20260719 在固定模擬環境完成兩條 route 且 0 recoveries，但未設獨立碰撞觀察欄，不能作「0 安全事件」證據；實車避障未量測 | 感測器盲區、twin 與實景偏差、事件漏記 —— 靠限速、監督與後續獨立觀測減輕 |
| H2 | 進入危險區域(樓梯口、充電樁後方) | 目標點合法但路徑/終點不當 | **G**:forbidden zones,G3 進禁區即 block(軌跡採樣);**H**:目標點由人批准 | test_twin_gate(禁區 block、終點在禁區不預演直接 block);sim 禁區清單（`SW-narrow-aisle`）＋ E2 主要描述 subset：C observed 在 zone_inside 20/20 block、normal 0/20 誤介入；A/B 為 derived，p 值不作確認性推論 | 禁區清單不完整;gate 關閉時只剩 H；實車場域與未知地圖未驗證 |
| H3 | 失控高速 | LLM 生成過大速度;指令打錯 | **R**:`[vehicle]` 硬夾限在 `/ros pub`、`/ros drive`、`/drive` 的執行路徑,LLM 碰不到夾限值;`/ros drive` 定時自動歸零 | test_ros2_core(夾限)、TEST.md `/drive` 實測；B4 僅補充任務完成紀錄，不含可驗證的逐時速度或事件標註 | 夾限值設錯；實體場域速度上限仍須校定，B4 不證明高速風險為零 |
| H4 | 通訊失效後持續移動 | TUI/daemon 崩潰、網路斷、行程被 kill | **R**:bridge watchdog —— client 斷線/卡死 >6s 自主停車,每 2s 重發 halt;watchdog 武裝失敗 = 啟動失敗(絕不發無保護 bridge) | test_bridge_client(武裝失敗擋啟動)、v0.8 實測(watchdog 斷線停車) | DDS 本身斷連時 halt 到不了 —— 硬體 estop 是最後防線(🅿 選配:實車硬體 estop 配置,P 項;sim 驗證以 watchdog 斷線停車 + /stop 實測覆蓋) |
| H5 | AI 聽錯/亂決策(意圖層錯誤) | LLM 誤解自然語言;VLM 誤判場景 | **H**:自動模式只允許可記憶的 bounded non-host P0/P1 操作略過批准卡；`HOST_COMMAND` 或 P2 每次均須明確批准、不可 session remember，P2 預設 No；daemon 自主動作仍需 `auto_approve`+nav2 明式授權；感知規則走同一套 gating 無捷徑；twin refer 在自主路徑視為 block | test_daemon(twin refer → NOT moved、confidence 亂填不觸發)、批准政策與工具分級驗收(TEST.md) | 人仍可能批准語意錯誤的動作；G 只檢查已建模執行後果，不能保證補救意圖錯誤 |
| H6 | 緊急停止失效 | 停止路徑本身出 bug;停止被排隊 | **R+設計**:`/stop` 免批准、Esc 取消不了停止本身、halt publisher 預建；Esc/stop 先送立即 halt 並撤銷 Nav2，終止且 reap 活動 subprocess/publisher，確認舊 producer 不再發送後才送 final zero；WebUI STOP 免 token、不等待 body 且撤銷舊確認 | test_safety_order + 安全鏈覆蓋 CI 倒退閘；halt 失敗誠實回報(test_daemon)；HIL-FS-20260719 記錄 cancel propagated、zero-velocity halt 與停止後漂移 0.0000 m（sim）；TEST.md `/stop` 實測 | 多重防護後仍是軟體 —— 硬體 estop 為最後防線 |
| H7 | 電量耗盡途中拋錨 | 任務過長、忘記回充 | **R**:daemon 電量規則自動回充(`goto Dock`);**P**:巡邏日報記錄 | rules.example.toml + test_daemon(goto gating) | 電量 topic 不準;dock 點未建(誠實提示) |
| H8 | 未授權者操作(LAN) | WebUI 曝露、MCP 濫用 | token 認證(401 不洩 token)、MCP 預設唯讀、`/shell` 批准卡 | test_webui auth 6 例、THREAT_MODEL.md | 見 THREAT_MODEL「明確不在範圍」 |
| H9 | 殘留實驗程序於模擬器重啟後恢復致動 | 背景 B4 driver 無時限存活；Stop 未終止外部程序 | **P＋R**：圈數／時間上限、`flock` 單實例、EXIT 發送 `/stop`、Play 前程序／goal／`cmd_vel` 清查；最後由 halt 歸零 | 2026-07-17 事後重建 subset selection window 之後的操作事件；`EXPERIMENTS` 背景清查 runbook；`b4_driver.sh` 有界生命週期 | SIGKILL 或主機故障可略過 trap；每次 Play 前仍須人工確認無活動 goal 與非零速度 |

## 分層防護的邏輯：互補危害映射

R、G、H 與 P 處理不同時間尺度與危害類別，但**未證明統計獨立或正交**。共同設定、
ROS graph、感測器或操作者錯誤可能同時削弱多層，因此不得宣稱「任一層失效仍安全」
或「兩層失效仍有保證」。

| 防護層 | 主要對應危害 | 能提供的限制 | 明確不覆蓋／共因依賴 |
|---|---|---|---|
| **H 批准** | H5、H8；亦輔助 H2 | 讓人看到具體動作並決定是否授權 | 人會誤批；無法由座標預知完整物理後果；依賴卡片內容與工具登記正確 |
| **G Twin Gate** | H1、H2 | 執行前檢查已建模場景中的碰撞、禁區、距離與可達性 | 不理解任務意圖；不看見未同步障礙、感測故障或模型偏差；依賴 map/pose/zone 設定 |
| **R 反射／原生控制** | H1、H3、H4、H6 | 執行中避障、硬夾限、watchdog 與停止 | 不判斷意圖或禁區語意；DDS/感測/硬體共因故障可同時影響多項反射 |
| **P 程序** | H7、H9；部署前提 | 背景程序清查、實體關機、速限與事件記錄 | 依賴人確實執行，不能取代技術防護或獨立安全驗證 |

因此本案例只主張**危害覆蓋互補**：例如 H 可攔意圖錯誤，G 可攔已建模的路徑後果，
R 可在執行時限制速度或停車；各層的殘餘風險仍須由相鄰層與程序承接。是否達到可接受
風險需由特定場域的 HARA/FMEA、失效注入與實體驗證另行判定。

## 事件記錄(P 層程序)

任何非預期的模擬或實體行為(急停觸發、擦撞、誤闖)→ 開 incident 紀錄,附:時間、
指令來源(TUI/WebUI/daemon)、`reports/` 對應 log、當時 protocol-specific doctor 輸出。
`doctor` 必須依實驗依賴判定：E2/B4 要求 ROS/Nav/map/pose/Twin readiness；Isaac HIL 另要求 10 筆 `/scan` 品質門檻與合法起點；E3 要求
domain 42 fixture；E1/E4 不以 ROS/Twin 為 gate。純模擬 domain 0 的
`twin_isolation` 不適用於場次通過，也不得因此宣稱虛實隔離已驗證。正式事件率研究另須
獨立觀察者與明確 incident 欄；一般 patrol report 不能代替安全事件量測。

## 完稿條件(對應 V1_GATE)

- 🟩 HIL-FS-20260719：clean `fb56456…b1e` 在 Isaac Sim 完成兩條 route、cancel/halt 與 scan-quality gate；Twin 同 domain 0 為 skip。此項只關閉模擬執行鏈，不關閉實體安全、隔離或事件率研究
- 🟨 B4 任務結果可重建：本機 subset manifest 固定 102 份 reports、407/408 waypoint succeeded、唯一 unavailable 的 goal 未送出；約 20 h 僅為歷史 driver task-time 摘要，reports 橫跨約 25.4 h wall time 且缺 per-report duration/run ID；無 incident 欄與獨立觀察者，故安全事件狀態仍為 OPEN
- 🟨 B5 禁區清單＋E2 主要描述 subset（80 組：困難案例介入 A／B／C=0／20／60，normal 三政策皆 0／20 誤介入；A／B derived、C observed；20 組 `zone_crossing` 全列探索性；不以 Q／McNemar p 值作確認性推論）
- 🟨 B6 onboarding 指導試用（2026-07-16，學弟妹 ≥3 完成且未回報阻擋問題）；
  純冷啟動、正式碼表與結構化卡點紀錄未執行，不可作效率量化證據
- 🅿 場域安全速度上限與硬體 estop 配置(實體選配,P 項)
