# tui/widgets — 互動元件

| 檔案 | 職責 |
|---|---|
| `approval_card.py` | 批准卡:編號選項(1 Yes / 2 Yes 本 session 不再問 / 3、Esc No),↑↓+Enter 或數字鍵直選;動作內容由 server 端持有,卡片只展示 |
| `blocks.py` | /run 代理的視覺塊:PlanBlock(計畫)、ToolBlock(工具呼叫)、ErrorBlock |

純視覺 + 鍵盤事件;批准的**決策邏輯**不在這裡(在 app.py 的批准流程與
`_request_direct_approval` 管線)。
