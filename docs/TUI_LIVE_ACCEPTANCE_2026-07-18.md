# JenAI 2026-07-18 TUI 自然語言導航修復驗收

日期：2026-07-18（Asia/Taipei）
平台：DGX Spark、Isaac Sim、ROS2 Jazzy、Nav2、本機 Ollama `qwen3.6:35b`
範圍：TUI approve 模式、自然語言導航 handoff、批准／拒絕與中斷後 session 收尾

## 前置狀態

- `JenAI doctor --json` 的 map、AMCL、laser、Nav2、`/cmd_vel` 與 provider 皆 pass。
- `twin_isolation` fail：使用者刻意讓 Twin 與 Isaac Sim 都使用 domain 0；只可視為純模擬。
- 地點檔包含四角與 dock；實體車未作為本次驗收對象。

## 實測與失敗保留

| 次序 | 輸入／操作 | 觀察結果 |
|---:|---|---|
| 1 | `帶我到 map_right_up`，批准 | 正常 handoff、lookup、preview 與批准卡；G3 偵測 `SW-narrow-aisle` 後 block |
| 2 | 同 session：`帶我到 map_right_down` | 修正前 supervisor 直接選 `loc_lookup_tool`，SDK 回報 `Tool not found in agent JenAI` |
| 3 | 修正後重啟但未清舊 session | 先前失敗留下連續 user turn；generation 192 秒後結束，三分鐘 gate 內由操作者 Esc 中止 |
| 4 | `/clear` 後重跑 `map_right_down`，批准 | 約 50 秒內到批准卡；因同域 rehearsal 已把模擬車留在禁區，仍由 G3 block |
| 5 | 同 session：`帶我到 map_left_up`，選 3 拒絕 | lookup、preview、批准卡皆正常，不再 `Tool not found`；拒絕後明示未移動 |
| 6 | 最新修正重啟，Thinking 中 Esc | UI 顯示 `Interrupted`；session 補 assistant marker，明示不得假設未回報動作成功 |

本輪沒有一筆安全導航成功，因此沒有啟動 10-run Hero demo。失敗不得從分母刪除；Isaac 車
須先 Reset／Stop→Play 回到合法起點，才可建立新的固定路線基準。

## 根因與修正

1. 某些 OpenAI-compatible 本機模型會在 follow-up 直接選 specialist tool 名稱，但省略
   handoff wrapper；supervisor 原本只鏡像 `explore_area_tool`，因此 SDK 在執行前拒絕。
2. failed／Esc interrupted run 可能只把 user turn 寫入 file session；下一輪會讀到不完整
   對話，造成延遲或不穩定工具選擇。
3. domain 0 同域時，Twin rehearsal 會作用在同一部 Isaac 車；G3 能阻擋後續正式執行，
   但不能把同域預演描述成無副作用隔離。

修正後 supervisor 只鏡像完整導航工具組；`route_execute_tool` 與 `explore_area_tool` 仍保留
framework approval 與 NavigationGateway。raw ROS drive/pub 工具沒有上提。failed／interrupted
turn 只在 session 末端確實未由 assistant 收尾時追加誠實 marker，空 session 不會孤立追加。

## 證據邊界

- 支持：follow-up 導航工具 fallback、批准／拒絕、Esc 中斷與 session 誠實收尾。
- 不支持：Hero demo ≥9/10、成功導航路線、Twin 通訊隔離、實體安全或跨載具泛化。
- SALES-1 與 ENG-2 維持 `PARTIAL`，直到 Reset 後在固定 commit／模型／場景保存完整 artifact。

## 本機 artifact

- trace：`~/.config/jenai/traces/traces.jsonl`
- 修正前 tool error：`trace_b485d1f9b46d408ebcf409a48f141218`
