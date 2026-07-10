# state — 執行期狀態與持久化

| 檔案 | 職責 |
|---|---|
| `runs.py` | `RunStore`:run 記錄 + agents SDK 原生 `RunState.to_json/from_json` 暫停狀態;TUI 原子寫入 `pending-runs/`、0600 權限、恢復時一次性 claim 防重播 |
| `session.py` | 對話 session 建立與跨重啟記憶檔 |
| `history.py` | 輸入歷史(↑↓ 鍵) |
| `reports.py` | 巡邏日報(V1_GATE A8):patrol 結束存 `<config 目錄>/reports/patrol-*.json`;`/report` 的確定性 markdown 渲染 + LLM 摘要(provider 離線誠實降級 —— LLM 是加分,永不是依賴) |
