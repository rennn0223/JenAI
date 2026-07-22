# Isaac HIL Acceptance

本流程把 Isaac Sim／Nav2 的 live route、取消回執與 software halt 驗收保存成可稽核 JSON。
它不取代一般 CI，也不會由 push、pull request 或排程自動啟動。

## 安全邊界

- 執行模式只接受 `--execute` 加完整確認字串
  `I-CONFIRM-ISAAC-SIM-MAY-MOVE`；少任一項就不送 goal。
- 一般 CI 只用 fake 測 runner，本 workflow 只支援人工 `workflow_dispatch`。
- preflight 以 production bridge 唯讀取得 AMCL/map 位姿；座標非有限值、禁區設定存在但不是 map frame，或起點落入禁區時一律 fail。
- preflight 會抽樣 10 筆 `/scan`；同時檢查至少 180 bins、角寬 ≥3.0 rad、正且有限的 increment、幾何誤差 ≤0.02 rad、range bounds、`0 < scan_time ≤ 1.0 s`、固定且非空 frame、timestamp 嚴格前進、全空樣本比例 ≤20% 與 aggregate valid-finite coverage ≥25%。NaN、`-inf`、空 ranges、malformed 或 out-of-range bins 一律 fail；topic 有頻率本身不算通過。
- live execution 在第一個 goal 前重新讀取位姿並套用相同判定，避免 preflight 後車況改變。
- live route 一律經 `NavigationGateway`、watchdog、Nav2 cancel 與 software halt；這不是硬體緊急停止。
- runner 結束時不論成功或失敗都再次呼叫 halt。
- 目前 target 固定為 `isaac-sim`，不可把 artifact 解讀成實體安全認證。

## 執行前提

1. Isaac Sim 已 Reset 並 Play，車位於合法、可導航的起點。
2. Nav2、map、AMCL、scan 與 cmd_vel controller 已啟動。
   RTX LiDAR 經 PointCloud2 轉換時，Helper 必須用 `Publish Full Scan=True`；目前 10 Hz full cloud 對應 `scan_time=0.1`，Nav2 使用裁出的前向 180° `/scan`。
3. shell 已 source ROS2 Jazzy，JenAI config 與 locations 可讀。
4. 目標點必須是已儲存位置；建議至少兩個 route goals，加一個夠遠的 cancel goal。
5. 若要驗 Twin verdict，Twin 必須使用與 target 不同的 ROS domain。

2026-07-19 正式場次的 Dock 是 `map (-6.0, -1.0, 3.14159)`；舊驗收的
`(4.355, 3.236, -1.289)` 只屬歷史 artifact，重播時不得混用。

先做唯讀 preflight：

```bash
source /opt/ros/jazzy/setup.bash
uv run python scripts/isaac_hil_acceptance.py \
  --goal map_left_down \
  --goal dock \
  --cancel-goal map_left_down \
  --output artifacts/isaac-hil-preflight.json
```

確認畫面與路徑安全後，才執行 live run：

```bash
source /opt/ros/jazzy/setup.bash
uv run python scripts/isaac_hil_acceptance.py \
  --execute \
  --confirm I-CONFIRM-ISAAC-SIM-MAY-MOVE \
  --goal map_left_down \
  --goal dock \
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
- `pass`：route、cancel acknowledgement／software halt，以及要求的 Twin checks 全通過。
- `pass_with_skips`：live checks 通過，但 Twin 等選配證據明確跳過。
- `fail`：任一必要項失敗。

每個 check 保存狀態、原因與證據，包括 progress samples、GateReport、執行耗時、
停止後位姿漂移、設定 SHA-256、Git revision/dirty 狀態與 ROS domain。artifact 不保存 API key、prompt 或
raw ROS payload，也不保存 self-hosted 主機名或設定絕對路徑。預設拒絕覆寫既有
檔案；重跑請換檔名，避免抹除失敗證據。

## 目前證據狀態

正式主證據是在 clean revision `d942130a7b3a789ddfa5585b8554dea32588d855`
執行的 JenAI 2.0.2 live run。本機 artifact
`artifacts/isaac-hil-live-final-d942130-20260719.json`（SHA-256
`b5e0f4f9bd14474a128865f26f748a2de9feea4c3e2bda1a395c14ed099bd18b`）記錄：scan
10/10、362 bins/筆、57.0442% valid-finite coverage，全部幾何／frame／timestamp／range／
scan-time gate 通過；`map_left_down` 82.881 s、Dock 45.804 s，皆 0 recoveries；取消時
local task stopped、Nav2 cancellation acknowledged=`true`、停止後漂移 0.0000 m。整體為
`pass_with_skips`，因 target 與 Twin 都在 domain 0，`twin_isolation` 明確為 `skip`。

另有 clean `cc6d217…f6e` 的 Hero 固定序列：第一次因 pose feed 暫失而 fail closed、0 goal
sent；恢復後 `map_left_down`／Dock 交替 10 legs 全數 succeeded。它是 Nav2 路線壓力樣本，
不是 10 次自然語言或完整 demo。較早 clean `fb56456…b1e` artifact 早於現行 scan metadata
與 Nav2 cancel-ack gate，只保留為歷史證據。詳見
[TUI_LIVE_ACCEPTANCE_2026-07-19.md](TUI_LIVE_ACCEPTANCE_2026-07-19.md) 與
[EVIDENCE_LEDGER.md](EVIDENCE_LEDGER.md)。

所有本機 artifact 均不進 Git 且不取代失敗樣本；上述結果也不支持實體安全、sim-to-real、
跨載具泛化或 Twin 隔離。正式 separated-domain Twin verdict 仍須另跑。
