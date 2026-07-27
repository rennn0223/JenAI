# TUI 真實互動驗收（2026-07-26）

## 證據定位

本頁記錄維護者在 DGX Spark 上，以 Isaac Sim 5.1.0、ROS 2 Jazzy、Nav2 與本機
Ollama `qwen3.6:35b` 進行的受監督工程接受測試。執行來源為
`81f935da9560b5326032df51c8a588bae80e1b05` 加尚未提交的候選修正，工作樹為 dirty。
它能證明本次互動鏈實際運作，但沒有獨立保存原始 TUI transcript，不可當作 clean revision
成功率、實體載具安全、Twin 隔離或可重播正式 HIL 的替代品。正式 HIL artifact 另見
[EVIDENCE_LEDGER.md](EVIDENCE_LEDGER.md)。

## 前置條件

- 完整基準驗收由操作員先在 Isaac Sim 執行 Stop／Play，使車輛回到 Dock 起點；這是可重現性要求，不是每次操作的必要條件。
- 車輛未卡牆、定位正常且舊 goal 已清除時，可先 `/dock` 再重啟 Nav2，不必重新 Play。
- 每次正式驗收前以 `./scripts/isaac_nav2.sh restart` 重啟 Nav2；重新 Play 後也必須 restart。
- TUI 刻意從未 source ROS 的乾淨程序環境啟動，以驗證 JenAI 的 ROS 自動載入。
- 導航 profile：位置 0.05 m、朝向 0.15 rad、`min_vel_x=0.0`、`vtheta_samples=15`、AMCL `alpha1..5=0.01`、`update_min_a/update_min_d=0.02`。

## 驗收結果

| 項目 | 實際結果 | 判定 |
|---|---|---|
| 乾淨環境啟動 | `uv run JenAI` 自動載入 ROS 2 Jazzy 與 `jazzy_ws` | 通過 |
| `/doctor` | ROS2、map、localization、LaserScan、Nav2、cmd_vel、Site Profile、Twin graph、provider 全部 pass | 通過 |
| 自然語言唯讀檢查 | 「幫我檢查目前位置、雷射掃描與 Nav2 狀態，不要移動機器人。」選擇唯讀狀態工具，以繁體中文回報且未送移動命令 | 通過 |
| `/route map_left_down` 批准卡 | 顯示 `goto map_left_down (x=-8.50, y=-7.50, yaw=0.785 rad)`，未顯示 Python dict | 通過 |
| 移動中 `/stop` | 先送立即零速 pulse，再取消 Nav2 goal；最終回報 navigation canceled、zero velocity sent | 通過 |
| `/dock` | 導航完成後以新鮮 `map → base_link` TF 驗證；最終位置誤差 0.022 m、朝向誤差 0.123 rad | 通過 |
| 最終 `/status` | JenAI 2.5.1、Doctor pass、route adapter `nav2` | 通過 |
| `/quit` | TUI 正常離開，沒有殘留互動任務 | 通過 |

同日較早一輪亦完成自然語言唯讀檢查、`/route` 後 `/stop`、`/dock` 與 `/status`；該輪
Dock 誤差為 0.023 m／0.144 rad。上表採用修正導航批准卡後的最後一輪結果，不以較佳數字
覆蓋正式 HIL 的 0.045 m／0.144 rad。

後續 AMCL profile 診斷中，車輛在定位健康且未卡牆時直接以 `/dock` 返回，不重新 Play；
停止後 TF 為 0.039 m／0.143 rad。這項觀察只證明健康工作階段可用 Dock＋Nav2 restart
恢復，不代表定位漂移、卡牆或模擬時間重置後也能省略重播。

PR 前末輪從重新 Play 後的已知起點重啟 Nav2，再依序執行 `/doctor`、`/loc list`、自然語言
唯讀狀態檢查、`/dock`、`/status` 與 `/quit`。五個註冊地點均可列出且不再出現
`no aliases`；自然語言檢查沒有送出移動命令；`/dock` 經人工批准後只送一個模擬目標，
停止後 TF 為 0.046 m／0.090 rad；`/status` 顯示 2.5.1、Doctor pass、route adapter
`nav2`，最後正常離開。稽核庫同步保存該 `/dock` run 的 `tool_updated=succeeded` 與
`run_finished=completed`。此末輪用來確認候選工作樹的實際入口，不取代上表涵蓋的移動中
急停測試，也不改寫正式 HIL artifact。

## 仍未關閉的邊界

- `/dock` 只證明抵達註冊姿態；Isaac Sim 尚未提供充電接點或充電狀態回授。
- target 與 Twin 都在 ROS domain 0；本次不宣稱 separated-domain Twin verdict 或實體隔離。
- 工作樹 dirty，且 TUI transcript 未封存為不可變 artifact；發布後若要升格為版本證據，
  必須在 clean tag／commit 重跑並保存完整環境、輸入、輸出與 SHA-256。
- 本頁只有單次受監督互動，不可外推長時間可靠度、事故率或新使用者學習效率。
