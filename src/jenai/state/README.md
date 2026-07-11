# state — 執行期狀態與持久化

| 檔案 | 職責 |
|---|---|
| `audit.py` | 有界 SQLite 稽核事件(預設最多 10,000 筆、0600):run 狀態、批准、工具狀態與 Twin Gate 判決;不存 user prompt、raw action 或完整 tool payload |
| `runs.py` | `RunStore`:run 記錄 + agents SDK 原生 `RunState.to_json/from_json` 暫停狀態;TUI 原子寫入 `pending-runs/`、0600 權限、恢復時一次性 claim 防重播 |
| `session.py` | 對話 session 建立與跨重啟記憶檔 |
| `history.py` | 輸入歷史(↑↓ 鍵) |
| `reports.py` | 巡邏日報(V1_GATE A8):patrol 結束存 `<config 目錄>/reports/patrol-*.json`;`/report` 的確定性 markdown 渲染 + LLM 摘要(provider 離線誠實降級 —— LLM 是加分,永不是依賴) |
