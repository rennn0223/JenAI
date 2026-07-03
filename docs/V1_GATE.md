# V1_GATE — v1.0 驗收基準與兩層分工

> 對應版本:v0.9.x。v1.0 的定義:**一份承諾** —— 介面不再亂動、安全語意經過驗證、
> 陌生人能靠文件自己跑起來。本文件是 v1.0 前的待辦總表,依「誰能完成」分兩層;
> 每個 release 對照打勾。
>
> **範圍決策**:v1.0 鎖在「監督式操作平台」邊界(人永遠在圈內);
> M6 自主決策迴圈是 v2 主線,不擋 v1.0。

---

## 層一:Agent(SWE)可獨力完成 — 做好交付,等客戶意見

| # | 項目 | 內容 | 狀態 |
|---|---|---|---|
| A1 | semver 契約文件 | 明列 public surface(config schema、rules.toml、MCP 工具、CLI、locations.toml),版本政策 + config 遷移方案 → docs/VERSIONING.md | ✅ v0.10 |
| A2 | WebUI auth | token 認證(Bearer/cookie/`?token=`,STOP 免認證)+ docs/THREAT_MODEL.md | ✅ v0.10 |
| A3 | 安全鏈覆蓋率 | estop/watchdog/gate/approval 路徑覆蓋 → ~100%,CI 標記不可倒退 | ☐ |
| A4 | 故障注入測試 | bridge 導航中死亡、provider 斷線、DDS 斷連、topic 停更 → 每項證明誠實降級 | ☐ |
| A5 | 架構鐵律 CI 防護 | import-linter/架構測試:技能層以上無載具字眼、LLM 不進即時迴路 | ☐ |
| A6 | 24h soak 腳本 | daemon + perception 跑 24h,記憶體/觸發統計自動報告 | ☐ |
| A7 | Safety case 草稿 | HARA-lite:危害清單 → 對應防護層 → 殘餘風險;場域細節留白給客戶補 | ☐ |
| A8 | 巡邏日報(C2) | patrol 快照 + 事件 log → LLM 日報;任務日誌可回放 | ☐ |
| A9 | M6 DecisionLoop 核心 | 有界動作集決策 + `jenai bench decision` 延遲量測(**v2 線,不擋 v1.0**) | ☐ |
| A10 | 注釋/結構清理 pass | 全庫一輪:補 why 注釋、刪冗餘與死碼 | ☐ |

## 層二:客戶端必須下場 — 實機驗測與回饋

| # | 項目 | 內容 | 狀態 |
|---|---|---|---|
| B1 | 車端後端搭建 | 照 docs/ONBOARDING.md:RGB 相機 → odom/scan → slam_toolbox 建圖 → AMCL → Nav2 bringup(人要在車邊;agent 可陪跑除錯) | ☐ |
| B2 | 建 locations | 車到定點 `/loc add here <名>`,含 `tags=["dock"]` 充電點 | ☐ |
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
