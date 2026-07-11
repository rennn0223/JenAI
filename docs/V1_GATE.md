# V1_GATE — v1.0 驗收基準與兩層分工

> 對應版本:**v0.30.0**。v1.0 的定義:**一份承諾** —— 介面不再亂動、安全語意經過驗證、
> 陌生人能靠文件自己跑起來。本文件是 v1.0 前的待辦總表,依「誰能完成」分兩層;
> 每個 release 對照打勾。前瞻主圖見 **[ROADMAP.md](ROADMAP.md)**。
>
> **範圍決策**:v1.0 鎖在「監督式操作平台」邊界(人永遠在圈內);
> M6 自主決策迴圈是 v2 主線,不擋 v1.0。
>
> **現況(v0.30.0)**:層一 A1–A8、A10 全 ✅(僅 A9=M6 屬 v2,且決策腦+eval 已於
> v0.21 先落地);層二待客戶下場,B2 已起頭(應科大樓/機械系館以 GPS 註冊)。
> 程式端已收整凍結(注釋/文件/交接齊備),v1.0 剩的幾乎全是**客戶端實機數據**。

---

## 層一:Agent(SWE)可獨力完成 — 做好交付,等客戶意見

| # | 項目 | 內容 | 狀態 |
|---|---|---|---|
| A1 | semver 契約文件 | 明列 public surface(config schema、rules.toml、MCP 工具、CLI、locations.toml),版本政策 + config 遷移方案 → docs/VERSIONING.md | ✅ v0.10 |
| A2 | WebUI auth | token 認證(Bearer/cookie/`?token=`,STOP 免認證)+ docs/THREAT_MODEL.md | ✅ v0.10 |
| A3 | 安全鏈覆蓋率 | 92%(safety 100/engine 98/gate 94/client 93/runner 76);CI 加 fail-under=90 倒退閘,只升不降 | ✅ v0.12 |
| A4 | 故障注入測試 | bridge 永不 ready / watchdog 武裝失敗 / 串流垃圾行 / twin 預演中斷→refer / pose 失聯→G4 跳過 / halt 失敗誠實回報 / 未知地點 / 無地點檔 → 全部證明誠實降級 | ✅ v0.11 |
| A5 | 架構鐵律 CI 防護 | tests/unit/test_architecture.py:反射層(bridge/engine/safety/gate)禁 import LLM 堆疊;技能層以上禁載具字眼(AST + 逐行掃描) | ✅ v0.12 |
| A6 | 24h soak 腳本 | `scripts/soak.py`:RSS 樹採樣 + warmup 校正 + PASS/WARN 判定;真 daemon 短跑驗證過(+0.0% PASS)。**24h 正式跑掛機時啟動**(見 TEST.md) | ✅ v0.13(24h 跑待排) |
| A7 | Safety case 草稿 | docs/SAFETY_CASE.md:H1–H8 危害表 → R/G/H/P 防護對應 → 驗證證據 → 殘餘風險;⬜ TODO(客戶)欄待 B4/B5/B6 數據 | ✅ v0.13(草稿) |
| A8 | 巡邏日報(C2) | patrol 結束自動存 log(`reports/patrol-*.json`);`/report` 確定性日報 + LLM 摘要(離線誠實降級)、`/report list` 回看歷次 | ✅ v0.12 |
| A9 | M6 DecisionLoop 核心 | 有界動作集決策 + 延遲量測(**v2 線,不擋 v1.0**)。決策腦 `decision_core.py` + `JenAI eval`(E1)已於 v0.21 落地;剩常駐迴圈 perceive→decide→rehearse→act,詳見 [ROADMAP 軌道 1](ROADMAP.md) | 🚧 v2(腦已備) |
| A10 | 注釋/結構清理 pass | 全庫稽核(v0.13):零 TODO/FIXME、零死碼(未引用 defs 僅 Typer 註冊命令)、唯一未 import 模組是 rclpy sidecar(設計如此)—— 無需清理 | ✅ v0.13 |

## 層二:客戶端必須下場 — 實機驗測與回饋

| # | 項目 | 內容 | 狀態 |
|---|---|---|---|
| B1 | **原生 nav 接口確認**(2026-07 改向:兩台載具皆自帶 SLAM+Nav) | 車邊各跑一次 `ros2 action list | grep -i navigate` 與 map/amcl/odom topics 清點;有 `NavigateToPose` 即直通,否則回報接口讓層一寫薄 adapter。自建圖流程(ONBOARDING.md)降為無原生堆疊時的備案 | ☐ |
| B2 | 建 locations | 車到定點 `/loc add here <名>`,含 `tags=["dock"]` 充電點 | 🚧 應科/機械系館已 GPS 註冊;dock 點待建 |
| B3 | 解鎖 TEST.md 🔶 項 | B1/B2 完成後逐項實測 `/route` `/mission` `/patrol photo` `/dock` `/perception`,結果回填 TEST.md | ☐ |
| B4 | 實車里程 | 累積 ≥20h / ≥50 次任務,0 安全事件;事件記錄表 | ☐ |
| B5 | Isaac 孿生場景 | 照 docs/TWIN_SETUP.md 建場景(工作站 GUI 作業)→ Twin Gate 端到端 + 消融數據(攔截率/誤攔率/延遲成本) | ☐ |
| B6 | Onboarding 計時 | 找 3 位新手照文件從裸機到第一次 `/route`,計時、記卡點(每個卡點=文件 bug,回報層一修) | ☐ |
| B7 | Demo 排練 | 15 分鐘 scripted demo,含斷網切 local provider 的備援劇本 | ☐ |
| B8 | 使用回饋 | 日常把 TEST.md ✅ 項當真用,意見開 issue 或直接講 | ☐ |

**依賴關係**:B3 依賴 B1+B2;B4 依賴 B3;B5 獨立(工作站);A7 完稿依賴 B4/B5 的數據。
層一可全部先行,不被層二 block。

---

## 驗收標準(每次改動,詳見 CLAUDE.md)

code review → CI 綠 → 照 TEST.md 實測能用 → 補注釋 → 結構整潔無冗餘 → 文件修齊 → PR + merge + tag + release

## v1.0 簽字條件(五視角收斂)

- M1–M5 全 ✅(含 Isaac 場景、onboarding 計時實測)
- 層一 A1–A8 全 ✅;層二 B1–B7 全 ✅
- 安全鏈覆蓋 ~100%、soak/chaos 綠、實車 20h/50 任務 0 事件
- semver 契約與 safety case 定稿、WebUI auth 上線
- Twin Gate 消融數據發佈(論文與產品主張同一批證據)
