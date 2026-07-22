# V1_RELEASE_CHECKLIST — v1.0 上版程序(由你執行)

> 約定:**程式碼已到 v1.0-RC 標準;v1.0 的 tag 由你在實測完成後打**。
> 本檔是那一天的精確程序 —— 照著跑就好,不用回憶任何 session。
> 前置狀態見 [V1_GATE](V1_GATE.md);v1.0 = 監督式操作平台(M6 自主迴圈屬 v2)。

## A|tag 前必須完成的實測(你的部分)

按順序,每項完成就在這裡打勾(直接 commit 這個檔):

- [x] **B1 車端後端**(✅ 2026-07-14,Carter 場景):照 [ONBOARDING](../ONBOARDING.md) + [ISAAC_NAV2_SETUP](../ISAAC_NAV2_SETUP.md):
      RGB/odom/scan → 建圖 → AMCL → Nav2(sim 先行,實車跟上)
- [x] **B2 完成**(✅ 2026-07-14,4 點含 dock):dock 點建立(`/loc add here Dock`);GPS 兩點第一次導航實測,
      偏移大就修 `[map_datum]`(整批平移特性)
- [x] **B3 解鎖 TEST.md 全部 🔶 項**(✅ 2026-07-14~15,含 WebUI/MCP/daemon):`/route`(nav2 模式)、`/mission`、
      `/patrol photo`、`/dock`、`/perception`、Camera 頁 —— 結果回填 TEST.md
- [x] **B4 固定模擬導航任務紀錄**（2026-07-16 歷史簽字；後續稽核可重建 102 份 reports：
      101／102 為 4／4、407／408 waypoint succeeded）：約 20 h 僅為 driver task-time 摘要，
      reports 無 duration／run ID／incident 欄或獨立觀察者，故不得表述為已驗證 20 h 暴露或
      0 安全事件；2026-07-17 H9 另列，完整限制見 EVIDENCE_LEDGER／SAFETY_CASE
- [x] **B5 Isaac 場景 + Twin Gate 端到端**（舊 C=full-twin 100 筆 observed；後續 E2-PAIR
      主要描述 subset 80 組：A／B derived、C observed，20 組 `zone_crossing` 全列探索性）：
      `[twin]` 啟用實跑；完整分母與限制見 EVIDENCE_LEDGER，不得表述為前瞻性三條件 live run
      或以 Q／McNemar p 值作確認性推論
- [x] **B6 Guided onboarding**（✅ 2026-07-16 客戶簽核：學弟妹 ≥3 在指導下完成、未回報阻擋問題；非純冷啟動、未碼表計時、無結構化卡點紀錄）
- [x] **B7 Demo 排練**(✅ 2026-07-16 第二輪跑順:twin 實啟、G3 block 當場確認):15 分鐘劇本 + 斷網切 local 備援,跑順一次
- [x] **A6 尾巴:24h soak 正式跑**(✅ 2026-07-16 PASS,RSS +1.2%/限 20%):`python3 scripts/soak.py --rules rules.example.toml`,
      `report.md` 為 PASS
- [x] **SAFETY_CASE 歷史草稿更新**(2026-07-16 填入 B4/B5；2026-07-18 證據稽核重開 B4 incident 狀態):目前 B4 只支持可重建任務結果，零事件與實車安全仍為 OPEN

## B|tag 當天的程序(跟平常 release 一樣,只是版本是 1.0.0)

```bash
git checkout -b release/v1.0.0
# 1) 版本
sed -i 's/^version = ".*"/version = "1.0.0"/' pyproject.toml && uv lock
# 2) 快照文件對齊:docs/validation/TEST.md 標頭版本、測試數;V1_GATE 全打勾
# 3) 驗
env -u PYTHONPATH uv run pytest        # 全綠
env -u PYTHONPATH uv run ruff check scripts src tests
# 4) PR + merge(CI 綠後)
git add -A && git commit -m "Release v1.0.0" && git push -u origin release/v1.0.0
gh pr create --title "Release v1.0.0" --body "V1_GATE 全項完成,見 V1_RELEASE_CHECKLIST"
gh pr merge <PR#> --merge --delete-branch
# 5) notes 先寫進 docs/releases/v1.0.0.md(隨 PR review)→ tag → workflow 草稿 → 人工發佈
git tag -a v1.0.0 -m "JenAI v1.0.0" && git push origin v1.0.0
gh release edit v1.0.0 --notes-file docs/releases/v1.0.0.md --draft=false
```

**v1.0 notes 該寫什麼**(素材都在):五視角簽字條件逐條、與 v0.1 對照的能力表、
安全鏈與 Twin Gate 的數據、致謝。

## C|tag 之後立刻做

- `docs/operations/VERSIONING.md` 的承諾正式生效:public surface 變更從此走 semver 紀律
- 論文第四章開跑:E1(`JenAI eval` 已就緒)→ E2(前瞻性政策比較；既有 A／B 為 derived)→ E3(虛實一致性)
- v2 主線 = M6 自主迴圈(決策核心 `decision_core.py` 已就緒,缺的是把
  perceive→decide→rehearse→act 接成常駐迴圈 —— 見 ROADMAP 軌道 1)

## 求助時

任何一步卡住:開新 AI session,說「照 docs/product/V1_RELEASE_CHECKLIST.md 的第 X 項陪我除錯」——
CLAUDE.md 的 DoD 與 memory 會讓它接得上下文。
