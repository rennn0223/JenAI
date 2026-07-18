# V1_GATE — v1.0 驗收基準與兩層分工

> 對應版本:**v1.0.0(2026-07-16 簽字達成)**。v1.0 的定義:**一份承諾** —— 介面不再亂動、
> 安全語意經過驗證、安裝與操作路徑已有文件。本文件是 v1.0 的歷史驗收總表，依「誰能完成」
> 分兩層,已全數打勾;留作驗收證據與 P 項(實體選配)的追蹤。前瞻主圖見 **[ROADMAP.md](ROADMAP.md)**。
>
> **範圍決策**:v1.0 鎖在「監督式操作平台」邊界(人永遠在圈內);
> M6 自主決策迴圈是 v2 主線,不擋 v1.0。
>
> **歷史簽字(v1.0.0)**:層一 A1–A8、A10 與層二 B1–B8 於 2026-07-14~16
> 標為完成（A9=M6 屬 v2）。2026-07-18 證據稽核後，可保留的結果是 E1 64 條
> 84.4%／unsafe 6.25%、E2 固定目標的描述性配對重分析（困難案例介入
> A／B／C=0／20／60、normal 誤介入皆 0／20；A／B derived、C observed），以及 B4
> 固定 subset 的 102 份 reports（101／102 為 4／4、407／408 waypoint succeeded）。
> B4 的約 20 h 僅是歷史 driver task-time 摘要，無法證明精確暴露量或 0 安全事件；
> 因此原 B4 安全／暴露證據等級改列 🟨，不以歷史勾選取代現在的限制。daemon 24 h
> soak PASS(+1.2%)只支持該 workload 的記憶體穩定性。實體驗證(P1–P3)屬選配。

---

## 層一:Agent(SWE)可獨力完成 — 做好交付,等客戶意見

| # | 項目 | 內容 | 狀態 |
|---|---|---|---|
| A1 | semver 契約文件 | 明列 public surface(config schema、rules.toml、MCP 工具、CLI、locations.toml),版本政策 + config 遷移方案 → docs/VERSIONING.md | ✅ v0.10 |
| A2 | WebUI auth | token 認證(Bearer/cookie/`?token=`,STOP 免認證)+ docs/THREAT_MODEL.md | ✅ v0.10 |
| A3 | 安全鏈覆蓋率 | 92%(safety 100/engine 98/gate 94/client 93/runner 76);CI 加 fail-under=90 倒退閘,只升不降 | ✅ v0.12 |
| A4 | 故障注入測試 | bridge 永不 ready / watchdog 武裝失敗 / 串流垃圾行 / twin 預演中斷→refer / pose 失聯→G4 跳過 / halt 失敗誠實回報 / 未知地點 / 無地點檔 → 全部證明誠實降級 | ✅ v0.11 |
| A5 | 架構鐵律 CI 防護 | tests/unit/test_architecture.py:反射層(bridge/engine/safety/gate)禁 import LLM 堆疊;技能層以上禁載具字眼(AST + 逐行掃描) | ✅ v0.12 |
| A6 | 24h soak 腳本 | `scripts/soak.py`:RSS 樹採樣 + warmup 校正 + PASS/WARN 判定;真 daemon 短跑驗證過(+0.0% PASS)。**24h 正式跑 PASS**(2026-07-15 01:25 → 07-16 01:25,1439.6 min/2880 樣本,RSS baseline 212188 kB → final 214728 kB,**+1.2%**,限 20%;報告 `soak-20260715-012527/report.md`) | ✅ v0.13 + 24h PASS(2026-07-16) |
| A7 | Safety case 草稿 | docs/SAFETY_CASE.md：v0.13 建立 H1–H8，2026-07-17 證據稽核追加 H9；均對應 R／G／H／P 防護、驗證證據與殘餘風險 | ✅ v0.13(草稿) |
| A8 | 巡邏日報(C2) | patrol 結束自動存 log(`reports/patrol-*.json`);`/report` 確定性日報 + LLM 摘要(離線誠實降級)、`/report list` 回看歷次 | ✅ v0.12 |
| A9 | M6 DecisionLoop 核心 | 有界動作集決策 + 延遲量測(**v2 線,不擋 v1.0**)。決策腦 `decision_core.py` + `JenAI eval`(E1)已於 v0.21 落地;剩常駐迴圈 perceive→decide→rehearse→act,詳見 [ROADMAP 軌道 1](ROADMAP.md) | 🚧 v2(腦已備) |
| A10 | 注釋/結構清理 pass | v0.13 完成 TODO/FIXME、死碼與未引用模組稽核；這只代表當時清單完成，不代表大型核心模組已無拆分空間。現況與後續門檻以 `PRODUCT_READINESS` ENG-3 為準 | ✅ v0.13 歷史項 |

## 層二:客戶端下場 — Isaac Sim 驗證與回饋(2026-07 改向:sim 為主要驗證平台)

> **驗證策略**(見 PROJECT_DIRECTION 驗證策略更新):v1.0 的驗收證據以
> **Isaac Sim 孿生場景**為主 —— 接口確認、地點、任務實測、固定任務紀錄與政策比較都在
> twin 上完成(DGX Spark 工作站作業)。實體驗證降為選配(下表 P 項),
> 有機會再做或交接下一屆。高階契約可重用，但新平台仍可能需要 profile／薄 adapter、
> Nav2／控制器調校與完整物理驗收。

| # | 項目 | 內容 | 狀態 |
|---|---|---|---|
| B5 | **Isaac 孿生場景**（關鍵路徑第一步） | 照 docs/TWIN_SETUP.md 建場景（DGX Spark GUI 作業）→ Twin Gate 端到端＋固定目標政策比較（攔截／誤介入／延遲） | ✅ 2026-07-15 場景、contact sensor、doctor twin 與 G1–G5 實跑完成。舊協議 `e2-20260715c/` 為 C=full-twin 的 100 筆 observed 前導；後續 `e2-20260715c-paired-reanalysis/` 將同目標的 A=no-gate、B=rules-only 以決定性政策離線重算，C 保留 observed。主要描述 subset 排除全部 20 組舊 `zone_crossing` 類，剩 80 組：60 組困難案例介入 A／B／C=0／20／60，20 組 normal 三條件皆 0 誤介入。歷史計算的 Q(2)=93.33、p<.001 可留作探索性摘要，但 A／B 非獨立觀測，不能作確認性推論。這是配對離線重分析，不是前瞻性三條件 live run；詳見 EVIDENCE_LEDGER E2-PAIR。 |
| B1 | 原生 nav 接口確認 | 於 twin 車跑 `ros2 action list \| grep -i navigate` 與 map/amcl/odom topics 清點;有 `NavigateToPose` 即直通(twin 車 = 第一個「原生 nav 載具」) | ✅ 2026-07-14(Carter 場景實測) |
| B2 | 建 locations | twin 場景內 `/loc add here <名>` 建點,含 `tags=["dock"]` 充電點 | ✅ 2026-07-14(4 點含 dock;貼牆點以 /loc move 重定位) |
| B3 | 解鎖 TEST.md 🔶 項 | B1/B2 完成後於 Isaac Sim 逐項實測 `/route` `/mission` `/patrol photo` `/dock` `/perception`,結果回填 TEST.md | ✅ 2026-07-14~15(slash + NL 全通;WebUI/MCP/daemon 亦實測,見 TEST.md) |
| B4 | 固定模擬導航任務紀錄 | 歷史目標為 ≥20 h／≥50 次任務與獨立事件記錄；目前只按可重建證據評級 | 🟨 2026-07-18 事後稽核固定 102 份 patrol reports：101／102 為 4／4，407／408 waypoint `succeeded`；唯一 `unavailable` 明記 goal 未送出。約 20 h 是歷史 driver task-time 摘要，reports 實際橫跨約 25.4 h wall time，且缺 per-report duration、run ID、incident 欄與獨立觀察者，因此不能驗證精確 20 h 暴露量或宣稱 0 安全事件。2026-07-17 H9 為 subset selection window 後的事後事件，另行保留。可重建 manifest／hash 見 EVIDENCE_LEDGER B4。 |
| B6 | Guided onboarding 回饋 | 找 3 位新手完成第一次 sim `/route`；正式冷啟動計時另列產品化研究 | ✅ 2026-07-16 客戶簽核：學弟妹（≥3）在客戶親自教學下完成，未回報阻擋問題；沒有結構化卡點紀錄。這不是純文件冷啟動，也未使用碼表；不得作效率量化證據 |
| B7 | Demo 排練 | 15 分鐘 scripted demo(Isaac Sim),含斷網切 local provider 的備援劇本 | ✅ 2026-07-16 排練完成(第二輪):`[twin] enabled = true` 實跑,`/route 去 sw_test_zone` 預演 **G3 block 當場確認**,全段跑順。第一輪發現的四項問題(twin 未開、NL 先反問、/model 編號制、Nav2 單次 abort)已修劇本 + 出 `/model` 箭頭選單(PR #95) |
| B8 | 使用回饋 | 日常把 TEST.md ✅ 項當真用,意見開 issue 或直接講 | ✅ 2026-07-16 客戶回報:日常使用順暢（B4 背景 driver 期間即真實日用；E2/E1 由本平台跑）；意見管道 = 對話即回饋，異常照 SAFETY_CASE 事件程序開 issue。這是非結構化回饋，不是效率或安全證據。 |

**依賴關係**:B5 是第一步(其餘 B 項全在其場景上進行);B3 依賴 B1+B2;
B4 依賴 B3;A7 完稿依賴 B4/B5 的數據。層一可全部先行,不被層二 block。

### 選配 / 交接下一屆:實體驗證(不擋 v1.0)

| # | 項目 | 內容 |
|---|---|---|
| P1 | 實車接口確認 | 車邊跑一次 B1 的清點指令;無 `NavigateToPose` 則回報接口讓層一寫薄 adapter。自建圖流程(ONBOARDING.md)為無原生堆疊時的備案 |
| P2 | 實車暴露與任務 | 以預先定義時鐘、run ID、獨立 incident 記錄重跑 B4 任務；做 sim-to-real 一致性對照(論文 future work) |
| P3 | 真機冒煙 | D3 真機冒煙清單(sim 測不到的感測路徑) |

---

## 驗收標準(每次改動,詳見 CLAUDE.md)

code review → CI 綠 → 照 TEST.md 實測能用 → 補注釋 → 結構整潔無冗餘 → 文件修齊 → PR + merge + tag + release

## v1.0 簽字條件(五視角收斂;2026-07 改向:Isaac Sim 驗證版)

- M1–M4 全 ✅；M5 軟體與文件完成，≥3 位使用者在指導下順跑，但純冷啟動與正式碼表計時未執行
- 層一 A1–A8 歷史簽字；層二 B1–B7 在 Isaac Sim 留有功能或任務 artifact，B4 證據等級依上表 🟨
- 安全相關程式 coverage 與 soak/chaos 綠；B4 僅支持固定 subset 的 102 份任務報告與 407／408 waypoint succeeded，不支持零事件結論
- semver 契約與 safety case 定稿、WebUI auth 上線
- Twin Gate 固定目標政策資料已發佈；A／B derived、C observed，僅作描述性比較
- 實體驗證(P1–P3)為選配,不擋 v1.0;完成後補進 safety case 與論文對照章
