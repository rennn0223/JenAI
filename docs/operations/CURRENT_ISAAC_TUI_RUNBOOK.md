# JenAI TUI＋Isaac Sim 現行操作 Runbook

> 適用版本：v2.5.1
> 執行主機：DGX Spark（Codex CLI、JenAI、ROS 2、Nav2、Isaac Sim 均在本機）
> 用途：重播一次受監督的真 TUI＋Nav2＋Isaac Sim 產品驗收。

本文件只記錄目前可由 repository、設定、腳本或既有實測確認的操作。高階設計見
[CURRENT_WORKFLOW](../workflow/CURRENT_WORKFLOW.md)；不經 TUI／LLM 的正式 HIL 見
[ISAAC_HIL_ACCEPTANCE](../validation/ISAAC_HIL_ACCEPTANCE.md)。

## 1. 固定工程事實

| 項目 | 現行值或取得方式 |
|---|---|
| Repository | `/home/nvidia/JenAI` |
| ROS 2 | Jazzy，`/opt/ros/jazzy/setup.bash` |
| Isaac workspace | `/home/nvidia/IsaacSim-ros_workspaces/jazzy_ws/install/setup.bash` |
| Nav2 管理入口 | `./scripts/isaac_nav2.sh restart`／`status`／`stop` |
| Nav2 tmux session | 預設 `nav2`；可用 `JENAI_NAV2_TMUX_SESSION` 覆寫 |
| Map／params | 由 `scripts/isaac_nav2.sh` 解析並在啟動前顯示；不要另抄一份路徑 |
| TUI 入口 | `uv run JenAI` |
| TUI 預設模式 | `approve`；移動批准卡預設選取 `No` |
| Task receipt | 預設位於 `/home/nvidia/.config/jenai/reports/tasks/`；實際 config 目錄以 `/status` 為準 |
| Isaac 範例 | `Window → Examples → Robotics Examples → ROS2 → Navigation → Nova Carter` |
| 場景完整 USD path | **UNKNOWN**；目前只由 Isaac Robotics Examples 載入，未在 repository 固定 |
| `ROS_DOMAIN_ID` | 不硬編碼；啟動前記錄目前 shell 值，未設定時 ROS 預設為 `0` |
| `RMW_IMPLEMENTATION` | **UNKNOWN／環境決定**；啟動前記錄，不得從過往場次猜測 |

手機 App／NoMachine 只負責遠端操作或觀看同一台 DGX Spark，不是額外 runtime。

## 2. Ownership

| 動作 | Owner | 自動判定 |
|---|---|---|
| 載入 Nova Carter 場景、Stop／Play、目視確認車體未穿模 | 操作員 | 否 |
| 記錄 Git／ROS 環境、restart Nav2、執行 preflight、啟動 TUI | Codex／工程師 | 是 |
| 檢查批准卡內容並批准移動 | 操作員 | 否 |
| Nav2 執行、取消、終點驗證、task receipt | JenAI runtime | 是 |
| 正式 HIL PASS／FAIL | HIL runner | 是 |
| Full TUI acceptance 判定 | 操作員依 receipt、畫面與 HIL 證據共同判定 | 部分 |

Codex 可以開啟 TUI 與讀取結果，但不得在看不清批准內容時替操作員猜測批准，也不得用
畫面「看起來到了」取代終點證據。

## 3. 是否需要 Stop／Play

Stop／Play 是世界重設，不是每次測試的固定儀式。

| 檢查 | 可繼續目前 Play session | 必須 Stop／Play |
|---|---|---|
| 車體 | 未卡牆、未穿模、沒有持續接觸障礙 | 卡牆、穿模或姿態異常 |
| 模擬時間 | `/clock` 連續兩次樣本有前進 | 時間停止、倒退或剛重設 |
| 導航 | 沒有未確認取消的舊 goal | 舊 goal 無法確認取消 |
| 定位 | TF／AMCL 與畫面位置一致且可規劃 | 明顯漂移、跳位或無法收斂 |
| 測試目的 | 一般功能回歸 | 固定基準要求相同 Dock 起點 |

健康 session 可先在 TUI 執行 `/dock`，再 restart Nav2。只要做過 Stop／Play、修改導航
profile，或即將執行正式固定基準，就必須重新執行：

```bash
cd /home/nvidia/JenAI
./scripts/isaac_nav2.sh restart
```

目前 HIL runner 會檢查 scan timestamp 嚴格前進，但沒有獨立的 `/clock` advancement
check；操作員需要在正式 TUI 驗收前讀取兩次 `/clock` 並確認第二次較新：

```bash
source /opt/ros/jazzy/setup.bash
source /home/nvidia/IsaacSim-ros_workspaces/jazzy_ws/install/setup.bash
ros2 topic echo /clock --once
ros2 topic echo /clock --once
```

任一 gate 失敗時先儲存診斷並停止。不可在同一次驗收中臨時調參、任意重啟或修改程式後
繼續把它算成同一個 PASS。

## 4. 啟動前紀錄

在 DGX Spark 的 repository shell 執行：

```bash
cd /home/nvidia/JenAI
git rev-parse HEAD
git status --short
printf 'ROS_DISTRO=%s\n' "${ROS_DISTRO:-UNSET}"
printf 'ROS_DOMAIN_ID=%s\n' "${ROS_DOMAIN_ID:-0}"
printf 'RMW_IMPLEMENTATION=%s\n' "${RMW_IMPLEMENTATION:-UNSET}"
```

若要形成正式 release 證據，工作樹必須 clean；dirty session 只能列為工程補充。

## 5. 啟動 Isaac Sim 與 Nav2

1. 操作員載入 Nova Carter Navigation 範例。
2. 操作員按 Play，確認車體、感測器與場景正常。
3. Codex／工程師重啟 Nav2：

   ```bash
   cd /home/nvidia/JenAI
   ./scripts/isaac_nav2.sh restart
   ./scripts/isaac_nav2.sh status
   ```

4. `restart` 必須成功驗證 lifecycle 與現行 controller profile。若失敗，保留 tmux／終端
   輸出並停止，不啟動移動驗收。

## 6. 先跑不移動的正式 preflight

```bash
cd /home/nvidia/JenAI
source /opt/ros/jazzy/setup.bash
source /home/nvidia/IsaacSim-ros_workspaces/jazzy_ws/install/setup.bash
uv run python scripts/isaac_hil_acceptance.py \
  --goal map_left_down \
  --goal dock \
  --cancel-goal map_left_down \
  --output artifacts/isaac-hil-preflight-<run-id>.json
```

只有 artifact 的 `overall` 為 `preflight_pass` 才能繼續。這一步驗證 ROS／Nav2、
scan quality、起點、Site／map、儲存地點與唯讀路徑規劃，不會送出移動 goal。

## 7. 啟動真 TUI

另開一個 shell：

```bash
cd /home/nvidia/JenAI
source /opt/ros/jazzy/setup.bash
source /home/nvidia/IsaacSim-ros_workspaces/jazzy_ws/install/setup.bash
uv run JenAI
```

`JenAI` 本身也會嘗試載入上述 ROS 環境；Runbook 仍明確 source，避免驗收結果依賴呼叫
shell 的歷史狀態。

進入 TUI 後依序執行：

```text
/doctor
/loc list
```

必要條件：

- `/doctor` 的 ROS2、map、localization、LaserScan、Nav2、cmd_vel 與 Site Profile 必須
  可用。
- `/loc list` 必須包含本次要使用的安全位置。
- TUI status bar 必須顯示 `approve`，若不是則用 `/mode approve` 明確切回。
- `/queue` 必須顯示空 queue；若不是，先 `/queue clear`，不得沿用舊指令。

## 8. Scenario TUI-SLASH-NAV-001

此情境先驗證 deterministic Slash 路徑，不混入 LLM routing。

1. 輸入：

   ```text
   /route map_left_down
   ```

2. 確認批准卡顯示的是 `goto map_left_down` 與預期座標，而不是其他位置或未解析的 dict。
3. 批准卡預設為 `No`。確認內容正確後，按數字 `1` 選擇一次性 `Yes`；`Esc` 一律拒絕。
4. 等待同一 run 結束。期間不得重送同一指令。
5. 完成後執行：

   ```text
   /report task
   /status
   ```

通過條件：

- 只出現一次對應批准。
- 只執行一次對應導航請求。
- task receipt 的 product outcome 是 `succeeded`；不能只看 run lifecycle 的
  `completed`。
- Navigation Gateway 收到 Nav2 terminal success，且停止後新鮮 TF 通過目前 vehicle
  profile 的位置／朝向容差。
- queue 回到空值，沒有 pending approval 或殘留 active task。

若要驗急停，另開獨立情境 `TUI-STOP-001`：車輛開始移動後輸入 `/stop`，確認 queue 與
pending approvals 被清除、Nav2 cancel acknowledgement 已記錄、zero velocity 已送達。
不得把它接在失敗的 route 後面算成同一次 route PASS。

## 9. 自然語言與 Dock smoke

Slash 情境通過後，才分開驗證：

```text
幫我檢查目前位置、雷射掃描與 Nav2 狀態，不要移動機器人。
```

它必須走唯讀 Capability，且不得送移動命令。再依需要執行：

```text
/dock
```

`/dock` 必須有獨立批准；其通過只代表抵達 Dock Approach pose。沒有 charging signal 時，
不得宣稱已充電。

## 10. 收尾與證據

TUI 內執行：

```text
/report task
/status
/quit
```

然後檢查：

```bash
cd /home/nvidia/JenAI
./scripts/isaac_nav2.sh status
git status --short
```

正式 HIL 的唯一機器判定 artifact 是 runner 的 JSON；TUI acceptance 另儲存：

- Git revision 與 dirty 狀態。
- 精確輸入與操作時間線。
- task receipt 路徑與 `run_id`。
- Nav2／JenAI 終端輸出。
- 最終 outcome、終點誤差、取消與停止回執。
- 未執行或跳過的項目。

現有 2026-07-26 實測見
[TUI_LIVE_ACCEPTANCE_2026-07-26](../validation/TUI_LIVE_ACCEPTANCE_2026-07-26.md)；對應
HIL artifact、SHA-256 與不可延伸主張見
[EVIDENCE_LEDGER](../validation/EVIDENCE_LEDGER.md)。

## 11. 已知無法自動化的部分

- Isaac Robotics Examples 尚未固定完整 USD path，場景載入與 Play 仍由操作員完成。
- 現行 Full TUI acceptance 沒有一條命令能封存完整按鍵 transcript、task receipt、
  Nav2 goal 與 final pose 的統一 bundle。
- Replay 後 restart Nav2 是明確操作政策，但 runtime 無法自行得知操作員剛按過
  Stop／Play。
- `/clock` 前進目前由人工雙樣本與 HIL scan timestamp 間接確認，尚無獨立 runner check。

這些限制不得被文件中的理想流程描述成已自動 enforcement。
