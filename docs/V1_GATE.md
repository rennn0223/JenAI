# V1_GATE — v1.0 驗收基準與兩層分工

> 對應版本:**v1.0.0(2026-07-16 簽字達成)**。v1.0 的定義:**一份承諾** —— 介面不再亂動、
> 安全語意經過驗證、陌生人能靠文件自己跑起來。本文件是 v1.0 的驗收總表,依「誰能完成」
> 分兩層,已全數打勾;留作驗收證據與 P 項(實體選配)的追蹤。前瞻主圖見 **[ROADMAP.md](ROADMAP.md)**。
>
> **範圍決策**:v1.0 鎖在「監督式操作平台」邊界(人永遠在圈內);
> M6 自主決策迴圈是 v2 主線,不擋 v1.0。
>
> **現況(v1.0.0)**:層一 A1–A8、A10 全 ✅(A9=M6 屬 v2,決策腦+eval 已於 v0.21 落地);
> 層二 B1–B8 全 ✅(2026-07-14~16,全數於 Isaac Sim 完成)。驗收數據:E1 64 條
> 84.4%/unsafe 6.25%、E2 消融硬陷阱 SIR 100%/FPR 0%、B4 里程 20h/102 趟/0 事件、
> 24h soak PASS(+1.2%)。實體驗證(P1–P3)屬選配,交接下一屆。

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
| A7 | Safety case 草稿 | docs/SAFETY_CASE.md:H1–H8 危害表 → R/G/H/P 防護對應 → 驗證證據 → 殘餘風險;⬜ TODO(客戶)欄待 B4/B5/B6 數據 | ✅ v0.13(草稿) |
| A8 | 巡邏日報(C2) | patrol 結束自動存 log(`reports/patrol-*.json`);`/report` 確定性日報 + LLM 摘要(離線誠實降級)、`/report list` 回看歷次 | ✅ v0.12 |
| A9 | M6 DecisionLoop 核心 | 有界動作集決策 + 延遲量測(**v2 線,不擋 v1.0**)。決策腦 `decision_core.py` + `JenAI eval`(E1)已於 v0.21 落地;剩常駐迴圈 perceive→decide→rehearse→act,詳見 [ROADMAP 軌道 1](ROADMAP.md) | 🚧 v2(腦已備) |
| A10 | 注釋/結構清理 pass | 全庫稽核(v0.13):零 TODO/FIXME、零死碼(未引用 defs 僅 Typer 註冊命令)、唯一未 import 模組是 rclpy sidecar(設計如此)—— 無需清理 | ✅ v0.13 |

## 層二:客戶端下場 — Isaac Sim 驗證與回饋(2026-07 改向:sim 為主要驗證平台)

> **驗證策略**(見 PROJECT_DIRECTION 驗證策略更新):v1.0 的驗收證據以
> **Isaac Sim 孿生場景**為主 —— 接口確認、地點、任務實測、里程、消融全部在
> twin 上完成(DGX Spark 工作站作業)。實體驗證降為選配(下表 P 項),
> 有機會再做或交接下一屆。高階契約可重用，但新平台仍可能需要 profile／薄 adapter、
> Nav2／控制器調校與完整物理驗收。

| # | 項目 | 內容 | 狀態 |
|---|---|---|---|
| B5 | **Isaac 孿生場景**(關鍵路徑第一步) | 照 docs/TWIN_SETUP.md 建場景(DGX Spark GUI 作業)→ Twin Gate 端到端 + 消融數據(攔截率/誤攔率/延遲成本) | ✅ 2026-07-15 **場景建置完成**:contact sensor 上線,doctor twin 三檢查全綠;帶 G1 的完整過閘實測(38.6s,G1–G5 全評,audit 留檔)。**消融數據完成(E2,2026-07-15)**:N=100(每類 20),硬陷阱 SIR 60/60=100%、良性 FPR 0/20=0%、zone_crossing 預演裁定 8/20 攔(餘 12 趟軌跡繞行未進區,放行正確);原始數據 `e2-20260715c/`,結果表已回填 THESIS_DRAFT 5.4 |
| B1 | 原生 nav 接口確認 | 於 twin 車跑 `ros2 action list | grep -i navigate` 與 map/amcl/odom topics 清點;有 `NavigateToPose` 即直通(twin 車 = 第一個「原生 nav 載具」) | ✅ 2026-07-14(Carter 場景實測) |
| B2 | 建 locations | twin 場景內 `/loc add here <名>` 建點,含 `tags=["dock"]` 充電點 | ✅ 2026-07-14(4 點含 dock;貼牆點以 /loc move 重定位) |
| B3 | 解鎖 TEST.md 🔶 項 | B1/B2 完成後於 Isaac Sim 逐項實測 `/route` `/mission` `/patrol photo` `/dock` `/perception`,結果回填 TEST.md | ✅ 2026-07-14~15(slash + NL 全通;WebUI/MCP/daemon 亦實測,見 TEST.md) |
| B4 | 模擬里程 | Isaac 場景累積 ≥20h / ≥50 次任務,0 安全事件;事件記錄表 | ✅ 2026-07-16 完成(2026-07-15 01:25 起算):**20.0h 任務時數(driver log)/ 102 趟 patrol / 408 個 waypoint goals(407 到達)/ 0 安全事件**。唯一非 4/4(07-15 17:27,3/4)= Nav2 action 發現逾時、goal 未送出、誠實回報 unavailable(#92 已修),非安全事件。原始記錄:`/tmp/b4_mileage.log` + `~/.config/jenai/reports/patrol-*.json` + audit;掛機工具 `scripts/b4_driver.sh` |
| B6 | Guided onboarding 回饋 | 找 3 位新手依文件完成第一次 sim `/route`，記錄卡點；正式冷啟動計時另列產品化研究 | ✅ 2026-07-16 客戶簽核:學弟妹(≥3)在客戶親自教學下完成、零卡點回報。這不是純文件冷啟動，也未使用碼表；不得作效率量化證據 |
| B7 | Demo 排練 | 15 分鐘 scripted demo(Isaac Sim),含斷網切 local provider 的備援劇本 | ✅ 2026-07-16 排練完成(第二輪):`[twin] enabled = true` 實跑,`/route 去 sw_test_zone` 預演 **G3 block 當場確認**,全段跑順。第一輪發現的四項問題(twin 未開、NL 先反問、/model 編號制、Nav2 單次 abort)已修劇本 + 出 `/model` 箭頭選單(PR #95) |
| B8 | 使用回饋 | 日常把 TEST.md ✅ 項當真用,意見開 issue 或直接講 | ✅ 2026-07-16 客戶回報:日常使用順暢(B4 里程期間即真實日用;E2/E1 全程由本平台跑);意見管道 = 對話即回饋,異常照 SAFETY_CASE 事件程序開 issue |

**依賴關係**:B5 是第一步(其餘 B 項全在其場景上進行);B3 依賴 B1+B2;
B4 依賴 B3;A7 完稿依賴 B4/B5 的數據。層一可全部先行,不被層二 block。

### 選配 / 交接下一屆:實體驗證(不擋 v1.0)

| # | 項目 | 內容 |
|---|---|---|
| P1 | 實車接口確認 | 車邊跑一次 B1 的清點指令;無 `NavigateToPose` 則回報接口讓層一寫薄 adapter。自建圖流程(ONBOARDING.md)為無原生堆疊時的備案 |
| P2 | 實車里程 | 實車重跑 B4 的里程與任務數;sim-to-real 一致性對照(論文 future work) |
| P3 | 真機冒煙 | D3 真機冒煙清單(sim 測不到的感測路徑) |

---

## 驗收標準(每次改動,詳見 CLAUDE.md)

code review → CI 綠 → 照 TEST.md 實測能用 → 補注釋 → 結構整潔無冗餘 → 文件修齊 → PR + merge + tag + release

## v1.0 簽字條件(五視角收斂;2026-07 改向:Isaac Sim 驗證版)

- M1–M4 全 ✅；M5 軟體與文件完成，≥3 位使用者在指導下順跑，但純冷啟動與正式碼表計時未執行
- 層一 A1–A8 全 ✅;層二 B1–B7 全 ✅(全數於 Isaac Sim 完成)
- 安全鏈覆蓋 ~100%、soak/chaos 綠、模擬 20h/50 任務 0 事件
- semver 契約與 safety case 定稿、WebUI auth 上線
- Twin Gate 消融數據發佈(論文與產品主張同一批證據)
- 實體驗證(P1–P3)為選配,不擋 v1.0;完成後補進 safety case 與論文對照章
