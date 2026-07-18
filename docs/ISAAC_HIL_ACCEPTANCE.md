# Isaac HIL Acceptance

本流程把 Isaac Sim／Nav2 的 live route、取消與 hard stop 驗收保存成可稽核 JSON。
它不取代一般 CI，也不會由 push、pull request 或排程自動啟動。

## 安全邊界

- 執行模式只接受 `--execute` 加完整確認字串
  `I-CONFIRM-ISAAC-SIM-MAY-MOVE`；少任一項就不送 goal。
- 一般 CI 只用 fake 測 runner，本 workflow 只支援人工 `workflow_dispatch`。
- preflight 以 production bridge 唯讀取得 AMCL/map 位姿；座標非有限值、禁區設定存在但不是 map frame，或起點落入禁區時一律 fail。
- live execution 在第一個 goal 前重新讀取位姿並套用相同判定，避免 preflight 後車況改變。
- live route 一律經 `NavigationGateway`、watchdog、Nav2 與 hard stop。
- runner 結束時不論成功或失敗都再次呼叫 halt。
- 目前 target 固定為 `isaac-sim`，不可把 artifact 解讀成實體安全認證。

## 執行前提

1. Isaac Sim 已 Reset 並 Play，車位於合法、可導航的起點。
2. Nav2、map、AMCL、scan 與 cmd_vel controller 已啟動。
3. shell 已 source ROS2 Jazzy，JenAI config 與 locations 可讀。
4. 目標點必須是已儲存位置；建議至少兩個 route goals，加一個夠遠的 cancel goal。
5. 若要驗 Twin verdict，Twin 必須使用與 target 不同的 ROS domain。

先做唯讀 preflight：

```bash
source /opt/ros/jazzy/setup.bash
uv run python scripts/isaac_hil_acceptance.py \
  --goal map_left_up \
  --goal map_right_up \
  --cancel-goal map_left_down \
  --output artifacts/isaac-hil-preflight.json
```

確認畫面與路徑安全後，才執行 live run：

```bash
source /opt/ros/jazzy/setup.bash
uv run python scripts/isaac_hil_acceptance.py \
  --execute \
  --confirm I-CONFIRM-ISAAC-SIM-MAY-MOVE \
  --goal map_left_up \
  --goal map_right_up \
  --cancel-goal map_left_down \
  --output artifacts/isaac-hil-live.json
```

需要正式 Twin 證據時加 `--require-twin`。若 target 與 Twin 同 domain，runner
會 fail；不加時則明確記為 `skip`，不會偽裝成 isolation 或 verdict 通過。

## Self-hosted GitHub runner

workflow 位於 `.github/workflows/isaac-hil.yml`，runner 必須有
`self-hosted` 與 `jenai-isaac` labels，並已安裝 ROS2 Jazzy、uv，且能連到正在
Play 的 Isaac graph。從 Actions 手動輸入 goals、cancel goal 與完整確認字串後執行。
正式使用前，請在 GitHub 的 `isaac-hil` Environment 設定 required reviewer；如此即使
有 repository write 權限的人啟動 workflow，仍需經指定操作員核准才會接觸 runner。
workflow 永遠上傳 `isaac-hil-<run>-<attempt>` artifact；無檔時也會在 summary 誠實
標示 setup／confirmation 在 runner 開始前失敗。

## Artifact 判讀

`overall` 只有四種：

- `preflight_pass`：ROS/Nav2 必要檢查與合法起點位姿皆通過，沒有送 goal。
- `pass`：route、cancel/stop，以及要求的 Twin checks 全通過。
- `pass_with_skips`：live checks 通過，但 Twin 等選配證據明確跳過。
- `fail`：任一必要項失敗。

每個 check 保存狀態、原因與證據，包括 progress samples、GateReport、執行耗時、
停止後位姿漂移、設定 SHA-256、Git revision/dirty 狀態與 ROS domain。artifact 不保存 API key、prompt 或
raw ROS payload，也不保存 self-hosted 主機名或設定絕對路徑。預設拒絕覆寫既有
檔案；重跑請換檔名，避免抹除失敗證據。

## 目前證據狀態

建立 runner/workflow 只代表「可重複執行」。在 self-hosted run 尚未產生並保存
artifact 前，`PRODUCT_READINESS` 的 ENG-2 仍維持 `PARTIAL`。
