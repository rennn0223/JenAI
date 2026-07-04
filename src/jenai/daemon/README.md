# daemon — 規則引擎(反射層)

`JenAI daemon --rules <toml>` 的核心:監看 topics,條件成立就動作 ——
**不經 LLM,LLM 掛了照樣保命**(引擎部分;`@perception` 規則的 VLM 輸入
屬決策層,但其動作 gating 與數值規則完全相同,無捷徑)。

| 檔案 | 職責 |
|---|---|
| `engine.py` | 純邏輯(可單測):規則解析(below/above/equals/affordance+min_confidence)、冷卻、動作驗證。動作:`notify`(預設)/ `halt`(免批准 —— 停車永遠安全)/ `goto <地點>`(需 `auto_approve` **且** nav2 明式授權) |
| `runner.py` | 接線:bridge watch → queue → engine → 動作。halt 搶佔進行中導航;goto 走 Twin Gate(**自主路徑 refer 一律視為 block —— 無人可問就不動**);導航失敗誠實回報,絕不靜默 |

規則檔範例見 repo 根目錄 `rules.example.toml`。
