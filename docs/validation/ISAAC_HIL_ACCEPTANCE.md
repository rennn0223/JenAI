# Isaac HIL Acceptance

本流程把 Isaac Sim／Nav2 的 live route、取消回執與 software halt 驗收保存成可稽核 JSON。
它不取代一般 CI，也不會由 push、pull request 或排程自動啟動。

## 安全邊界

- 執行模式只接受 `--execute` 加完整確認字串
  `I-CONFIRM-ISAAC-SIM-MAY-MOVE`；少任一項就不送 goal。
- 一般 CI 只用 fake 測 runner，本 workflow 只支援人工 `workflow_dispatch`。
- preflight 以 production bridge 唯讀取得 AMCL/map 位姿與 latched `/map`；座標非有限值、不是 map frame、超出地圖、起點 cell 為 unknown／occupied（只有值 `0` 視為明確 free），或起點落入禁區時一律 fail。
- preflight 會抽樣 10 筆 `/scan`；同時檢查至少 180 bins、角寬 ≥3.0 rad、正且有限的 increment、幾何誤差 ≤0.02 rad、range bounds、`0 < scan_time ≤ 1.0 s`、固定且非空 frame、timestamp 嚴格前進、全空樣本比例 ≤20% 與 aggregate valid-finite coverage ≥25%。NaN、`-inf`、空 ranges、malformed 或 out-of-range bins 一律 fail；topic 有頻率本身不算通過。
- live execution 在第一個 goal 前重新讀取位姿並套用相同判定，避免 preflight 後車況改變。
- bridge client 對 pose、map cell、Nav2 plan、cancel 與 halt 回應採精確型別與一致性驗證；
  例如字串 `"false"`、非有限座標、`free=true` 卻 occupancy=100，或未明確確認
  `halted=true` 都視為失敗，不做 truthiness 強制轉型。
- live route 一律經 `NavigationGateway`、watchdog、Nav2 cancel 與 software halt；這不是硬體緊急停止。
- Nav2 回報 `SUCCEEDED` 後，JenAI 仍以 terminal feedback 的 `current_pose` 計算位置與
  wrap-around yaw 誤差；frame 不同、證據缺失／畸形或超過 `[vehicle]` 到點上限時，
  route 必須 fail closed 並 halt。action status 本身不再等於「精準到點」。
- 任一 live route／Twin verdict 失敗後，後續 route 與 cancel exercise 一律標示 `skip` 且不再送
  motion goal；runner 進入 fail-fast 收尾。
- runner 結束時不論成功或失敗都再次呼叫 halt，並把 `final_halt` 與
  `bridge_shutdown` 的成功或失敗寫入 artifact；停止未獲確認時 overall 必為 `fail`。
- 目前 target 固定為 `isaac-sim`，不可把 artifact 解讀成實體安全認證。

## 執行前提

1. Isaac Sim 已 Reset 並 Play，車位於合法、可導航的起點。
2. Nav2、map、AMCL、scan 與 cmd_vel controller 已啟動。
   RTX LiDAR 經 PointCloud2 轉換時，Helper 必須用 `Publish Full Scan=True`；目前 10 Hz full cloud 對應 `scan_time=0.1`，Nav2 使用裁出的前向 180° `/scan`。
3. shell 已 source Isaac ROS Jazzy workspace（本機預設 `/home/nvidia/IsaacSim-ros_workspaces/jazzy_ws/install/setup.bash`），JenAI config 與 locations 可讀。
4. 目標點必須是已儲存位置；建議至少兩個 route goals，加一個夠遠的 cancel goal。
5. 若要驗 Twin verdict，Twin 必須使用與 target 不同的 ROS domain。

2026-07-19 正式場次的 Dock 是 `map (-6.0, -1.0, 3.14159)`；舊驗收的
`(4.355, 3.236, -1.289)` 只屬歷史 artifact，重播時不得混用。

先做唯讀 preflight。除了 ROS、定位、雷射、起點地圖 cell 與禁區，它也會對每個 requested
goal 呼叫 Nav2 `ComputePathToPose`；任一起點占用、目標占用或無有效路徑都會在送出
移動 goal 前 fail closed：

> 此 preflight 是必要條件，不是到達保證。它證明當下靜態 map cell 與全域規劃可接受；
> 移動後才出現的局部 costmap 障礙、碰撞監控、定位漂移或控制器限制仍可讓 Nav2 abort，
> 此時 live runner 必須照實失敗、停止後續 motion 並執行 final halt。

```bash
source /opt/ros/jazzy/setup.bash
source /home/nvidia/IsaacSim-ros_workspaces/jazzy_ws/install/setup.bash
uv run python scripts/isaac_hil_acceptance.py \
  --goal map_left_down \
  --goal dock \
  --cancel-goal map_left_down \
  --output artifacts/isaac-hil-preflight.json
```

確認畫面與路徑安全後，才執行 live run：

```bash
source /opt/ros/jazzy/setup.bash
source /home/nvidia/IsaacSim-ros_workspaces/jazzy_ws/install/setup.bash
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
workflow 預設 source `/home/nvidia/IsaacSim-ros_workspaces/jazzy_ws/install/setup.bash`；
其他 runner 以 repository variable `ROS_SETUP_PATH` 指定完整 setup 路徑。
正式使用前，請在 GitHub 的 `isaac-hil` Environment 設定 required reviewer；如此即使
有 repository write 權限的人啟動 workflow，仍需經指定操作員核准才會接觸 runner。
workflow 永遠上傳 `isaac-hil-<run>-<attempt>` artifact；無檔時也會在 summary 誠實
標示 setup／confirmation 在 runner 開始前失敗。

## Artifact 判讀

`overall` 只有四種：

- `preflight_pass`：ROS/Nav2、合法起點位姿與唯讀路徑規劃皆通過，沒有送 goal。
- `pass`：route、cancel acknowledgement／software halt，以及要求的 Twin checks 全通過。
- `pass_with_skips`：live checks 通過，但 Twin 等選配證據明確跳過。
- `fail`：任一必要項失敗。

執行模式的 `final_halt` 與 `bridge_shutdown` 也是必要項；缺少、畸形或否定的 halt 回執
不得用「沒有例外」推論機器人已停止。

每個 check 保存狀態、原因與證據，包括起點 map cell／地圖幾何、progress samples、GateReport、執行耗時、
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

2026-07-24 另保存一筆**工程補充、非正式主證據**的精度調校 run：同步把
`general_goal_checker.xy_goal_tolerance` 與 `FollowPath.xy_goal_tolerance` 設為 0.05 m，
並把 yaw tolerance 調為 0.15 rad 後，Dock route 最後 Nav2 feedback 為 0.04 m、action
succeeded；run 為 dirty source，且 yaw 門檻在場次中調整，耗時 164.219 s、5 recoveries，
因此只證明參數耦合與 5 cm 位置門檻可達，不取代 clean HIL-FS2，也不是效能基準。原始檔
`artifacts/isaac-hil-live-precision-loaded-20260724.json`，SHA-256
`ba71af2a25b80f6e8db46f06c931129b8f42d1fc81fa94627c7ecda828cc4925`。該場次早於
terminal-pose 二次核對程式碼；完成後須另跑 clean run 才能把新成功語意升格為正式證據。

同日完成現行 Site Profile 與 terminal-pose 二次核對的整合工程驗收。唯讀 preflight
先確認 active site `Isaac Warehouse — Nova Carter` 的 `/map` identity
`0bbe99c7be3c7eae05b7872e0945c95f8f71bf88c763e4ad12d8aefed82d22e3`
一致，再執行兩條 route 與 cancel-stop。live artifact 為 `pass_with_skips`：
`map_left_down` 終點誤差 0.043 m／0.149 rad、174.073 s、4 recoveries；Dock 終點
誤差 0.038 m／0.149 rad、92.248 s、3 recoveries；Nav2 cancel acknowledgement
為 `true`，停止後漂移 0.0000 m。雷射 10/10 筆、每筆 362 bins，aggregate
valid-finite coverage 46.989%。原始檔
`artifacts/isaac-hil-live-product-v4-20260724.json`，SHA-256
`47b2dc45a4b77fba3a6efce96b3fc4d12f946617d5ef603d94b378408b85c1e3`；
對應 preflight SHA-256
`4839840c5927f281a006d1406b74043cbed753f8a7cb3c180992b7178e0af3de`。
此場次仍是 dirty source 工程證據，不取代 clean HIL-FS2。多次 recovery 與 0.149 rad
Dock yaw 誤差必須保留為待改善事項；`pass` 不代表充電對位或充電狀態已驗證。

所有本機 artifact 均不進 Git 且不取代失敗樣本；上述結果也不支持實體安全、sim-to-real、
跨載具泛化或 Twin 隔離。正式 separated-domain Twin verdict 仍須另跑。
