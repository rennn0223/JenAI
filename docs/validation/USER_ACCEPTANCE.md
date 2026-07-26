# JenAI 使用者驗收目錄

> 適用版本：v2.5.0。這份文件給使用者親自操作，不需要執行 pytest，也不需要修改程式碼。
> 驗證以 Isaac Sim 為主；若接的是實體載具，請改用空曠場地並安排現場安全人員。

## 1. 開始前

1. 將 Isaac Sim Stop 後重新 Play，並把車輛放在已知起點。
2. 確認車輛、LiDAR 與 ROS 2 Action Graph 正常運作。
3. 保持畫面可見；導航時不要同時手動拖曳車輛。

```bash
cd /home/nvidia/JenAI
source /opt/ros/jazzy/setup.bash
source /home/nvidia/IsaacSim-ros_workspaces/jazzy_ws/install/setup.bash
uv run JenAI doctor
uv run JenAI
```

`doctor` 不要求每項都是 `pass`。沒有獨立 Twin domain 時，
`twin_isolation` 警告是預期結果；ROS 2、map、localization、laser 與 Nav2 應誠實反映現況。

## 2. 建議測試順序

請依序執行。會移動車輛的項目開始前，先確認 Isaac Sim 畫面與 `/stop` 都可操作。

| ID | 在 TUI 輸入／操作 | 通過條件 |
|---|---|---|
| U01 | `/status` | 顯示 provider、model、config 與 doctor 摘要；不移動 |
| U02 | `/doctor` | ROS 2／Nav2 各項逐條顯示狀態，且異常有具體原因 |
| U03 | 輸入 `/`，方向鍵選擇，再以 Tab 補完 `/status` | 選單可捲動；Tab 只補指令名稱，不插入假的 `<placeholder>` |
| U04 | `/loc list` | 看到 `dock` 與 Site Profile 使用的地點；沒有多餘的 `no aliases` 噪音 |
| U05 | `幫我檢查目前位置、雷射掃描與 Nav2 狀態，不要移動機器人。` | 顯示 Agent／Inspect／Result；明說未送移動指令，車輛不動 |
| U06 | Shift+Tab 切到 `plan`，輸入 `回到 dock` | 只產生計畫；沒有批准卡、Nav2 goal 或移動 |
| U07 | 切回 `approve`，輸入 `請導航到 map_left_up，到達後核對位置與朝向誤差。` | 先批准再導航；結果含 Nav2 狀態與終點誤差，不以「附近」冒充到達 |
| U08 | `/dock` | 先批准再執行；驗證 dock approach pose，但不宣稱已充電 |
| U09 | `巡檢 Site Profile 的所有必要區域，完成後回到 home；異常或證據不足時要求人工確認。` | 選擇 area patrol Workflow；最後列出 coverage、未完成區域、證據與回程結果 |
| U10 | `/report task list` | 看得到最近任務；成功、部分成功、失敗與取消沒有混為一談 |
| U11 | `/route 這個地點不存在` | 不送 Nav2 goal；誠實列出未知地點或可用地點，不猜座標 |
| U12 | 啟動任一導航後立即輸入 `/stop` | 不需批准，能搶佔任務、取消 goal、停止車輛並清除舊排隊意圖 |
| U13 | `/clear` | 清除畫面與對話記憶，之後仍可繼續操作 |
| U14 | `/quit` | 正常離開 TUI，終端機狀態恢復 |

U09 需要 active Site Profile 已定義 `patrol_areas`、必要區域、觀察點與 home。
若尚未設定，誠實拒絕算系統行為正確，但不算「巡檢 Workflow 通過」。

## 3. 發現問題時提供這些資料

1. 測試 ID，例如 `U07`。
2. 輸入的完整句子或 Slash 指令。
3. 最後一個 Agent、Tool、Result 或錯誤區塊截圖。
4. Isaac Sim 當時的車輛位置與 Play／Stop 狀態。
5. `/status` 與 `/doctor` 結果。
6. `/report task list` 中對應的任務 ID。

## 4. 建議記錄格式

每次驗收複製一列，填入實際結果：

| ID | 日期時間 | 結果 | 實際輸出摘要 | 截圖／任務 ID |
|---|---|---|---|---|
| U01 |  | PASS／FAIL／BLOCKED |  |  |

- `PASS`：行為與通過條件一致。
- `FAIL`：當機、卡住、執行錯誤行為或回報不實。
- `BLOCKED`：缺少 Nav2、地圖、相機或 Site Profile，且系統有誠實說明。

不要把 `BLOCKED` 改寫成 `PASS`，也不要把 Isaac Sim 結果描述成實體載具驗證。

完整工程測試與歷史證據請看 [TEST](TEST.md) 與
[ISAAC_HIL_ACCEPTANCE](ISAAC_HIL_ACCEPTANCE.md)。

請勿提供 `.env`、API key、原始憑證或其他私密設定。
