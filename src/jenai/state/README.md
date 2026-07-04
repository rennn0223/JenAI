# state — 執行期狀態與持久化

| 檔案 | 職責 |
|---|---|
| `runs.py` | `RunStore`:session 內的 run 記錄 + agents SDK 暫停狀態側表(批准中斷/續跑的配對) |
| `session.py` | 對話 session 建立與跨重啟記憶檔 |
| `history.py` | 輸入歷史(↑↓ 鍵) |
| `reports.py` | 巡邏日報(V1_GATE A8):patrol 結束存 `<config 目錄>/reports/patrol-*.json`;`/report` 的確定性 markdown 渲染 + LLM 摘要(provider 離線誠實降級 —— LLM 是加分,永不是依賴) |
