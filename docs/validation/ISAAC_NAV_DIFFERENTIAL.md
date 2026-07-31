# Isaac Navigation Differential Harness

本工具只回答一個問題：

> 在相同場景、起點、Nav2 stack、量測契約與 canonical goal 下，
> `bridge → Nav2` 和 `JenAI NavigationGateway → Nav2` 的實際輸入、終點證據與 verdict
> 是否不同？

它是 observation-only 驗收工具，不修改 Nav2／AMCL 參數、endpoint retry policy、arrival
tolerance、Nav2 success 後的 0.5 秒驗證、production navigation flow、TUI 或 WebUI。

`navigate_live(..., direct=True)` 是 deprecated 的 `odom → cmd_vel` fallback，不是 Nav2
baseline；本工具不使用它。

## 模式與邊界

| 模式 | 路徑 | 用途 |
|---|---|---|
| `R1_bridge_nav2` | canonical goal → harness-owned `RosBridgeClient.nav_send` → Nav2 | 隔離 bridge／Nav2；仍有 watchdog、final halt 與 cleanup |
| `R2_jenai_no_retry` | 同一 goal → `NavigationGateway` → `nav_live` → Nav2 | 觀測 JenAI policy 與 completion verification |

R1 的 result timeout 使用目前 `VehicleProfile.nav_timeout_s`，與 R2 的有效 JenAI navigation
budget 相同，不另設一套 CLI timeout。

R2 只在 runner 的記憶內建立 `nav_endpoint_retry_limit=0` 的 config copy。若 Twin 與 target
使用同一有效 ROS domain，也只在該次 copy 暫時停用 Twin。有效 override 會保存於
`effective_experimental_config`；磁碟設定與 production defaults 不會被改寫。

`R0_rviz` 是人工／bag 證據基準，不由第一版 runner 自動發 goal。R0 必須擷取 RViz 實際送出
並由 action server 接受的 payload；不可用畫面游標或假設 `/goal_pose` 等於 accepted goal。

## Goal 與 UUID 證據

- `canonical_goal` 是 location 經 Site binding 後的預期目標。
- `t1_goal_dispatch.actual_goal` 是 `_ObservedNavBridge.nav_send()` 真正收到的
  `frame/x/y/yaw`；離線比較使用這份實際 dispatch goal。
- `map` 與 `/map` 正規化為 `map`。
- quaternion 先正規化；`q` 與 `-q` 視為同一旋轉。
- ROS timestamp 數字不要求跨 run 相等；只檢查 clock domain、simulation epoch 與 freshness。
- accepted goal UUID 不是 action client 直接回傳，而是在實際 `nav_send` 後，從
  `/navigate_to_pose/_action/status` 推論出的唯一、schema-valid、goal-stamp fresh 新 UUID。
- 沒有新 UUID、多個候選或 stale candidate 都會使 T1 timeline fail closed；該場為
  `insufficient_evidence`，不得猜測 UUID。

## Ground truth 契約

Isaac world pose 不可直接與 ROS map pose 相減。只有已驗證的 `T_map_world` calibration
通過下列全部 gate，runner 才會產生 `final_ground_truth_map_median`：

- calibration 與 `--ground-truth-topic` 成對提供；
- `residual_m <= 0.02 m`（預設）；
- configured scene SHA、操作員擷取的 active Stage SHA、calibration scene SHA 三者一致；
- Site map SHA、live map digest、calibration map SHA 三者一致；
- configured map frame、live map frame、calibration `map_frame_id` 一致；
- ground-truth message 有 fresh header timestamp，frame 等於 `world_frame_id`；
- 只使用 Nav2 terminal 後 final window 的樣本。

Calibration JSON 範例：

```json
{
  "status": "VERIFIED",
  "scene_sha256": "<64 lowercase hex>",
  "map_sha256": "<64 lowercase hex>",
  "source": "survey artifact URI or local record",
  "world_frame_id": "world",
  "map_frame_id": "map",
  "translation_x_m": 0.0,
  "translation_y_m": 0.0,
  "rotation_yaw_rad": 0.0,
  "calibration_method": "three-point rigid fit",
  "residual_m": 0.004
}
```

缺少可信 calibration 時會明確記錄 `GROUND_TRUTH_UNAVAILABLE`。這種 run 仍可比較 ROS
map pose，但不得宣稱 Isaac 中的實際車體停偏。

## Live runtime、T0、T1 與 final gate

Live execution 只允許 `deployment_mode=simulation`，並在送 goal 前 fail closed 檢查：

- clean Git revision、正確 `jenai.__file__`；
- config、Site map、locations、rendered Nav2 params、scene SHA 完整；
- `--scene` 檔案 SHA 等於操作員提供的 active Stage root-layer SHA；
- Site map digest／frame 等於 bridge 觀察到的 live map；
- 有效 ROS domain；
- `/amcl`、`/controller_server`、`/planner_server`、`/bt_navigator` 各一個；
- `NavigateToPose` action 唯一；
- controller／planner／BT navigator lifecycle 為 active；
- 必要 runtime parameter dumps 完整。

T0 scenario start 與真正 `nav_send` 前的 T1 dispatch state 都要求：

- `/clock` 前進且未倒退；
- fresh `map → base_link`；
- fresh AMCL／odom（預設 source age `<= 1 s`）；
- finite AMCL covariance `<= 0.1`；
- robot stationary；
- fresh、schema-valid action status；
- 沒有 active goal。

T1 在實際 `nav_send` 邊界重新取樣；T1 失敗時 goal 不會 forward。

Nav2 terminal 後的 final window 要求：

- 2.0 秒實際 ROS time；wall time 上限 15 秒；
- `/clock` 不可暫停或倒退；
- 至少 10 筆 fresh map pose；
- fresh AMCL／odom，AMCL covariance 仍在門檻內；
- final odom window 顯示 robot stationary。

Wall time 經過不代表 paused Isaac 已完成 final window。

Cleanup 逐步記錄 final halt、heartbeat、topic unwatch 與 bridge shutdown。Final halt 只證明零速度
命令發布，以及有 cancel request 時 cancel 已 acknowledged；它明確保存
`motion_stop_observed=false`。實際 stationary evidence 來自 final odom window。任何 cleanup
失敗會把 overall 降級為 `cleanup_failed`，不能保留先前的 success claim。

## Measurement contract 與 artifact lifecycle

每個 schema-v1 artifact 都保存不可變 `measurement_contract`，包括取樣期間、freshness、
final ROS-time window、wall timeout、最低樣本數、速度、covariance 與 calibration residual
門檻。R1／R2 的 measurement contract 不完全相同時，pairing gate 必須失敗。

`overall` 可能為：

```text
preflight_only
blocked
captured
insufficient_evidence
failed
cleanup_failed
```

只有 `captured` 且 T0、T1、terminal-bound final window、cleanup 全部 PASS，並有可解析的
actual dispatch goal，才能進入 paired comparison。Preparation、dispatch 或 cleanup 例外仍會
原子保存可重新載入的 schema-v1 artifact；成功與失敗場次都不得刪除。

## Preflight：不移動

```bash
uv run python scripts/isaac_nav_differential.py capture \
  --mode R1_bridge_nav2 \
  --location map_left_down \
  --pair-id pilot-00 \
  --simulation-epoch warehouse-play-20260731-01 \
  --reset-policy nav2_restart \
  --scene /absolute/path/to/warehouse.usd \
  --output artifacts/nav-diff/pilot-00-r1-preflight.json
```

`preflight_only` 只證明靜態 artifact 建立成功：不啟動 bridge、不取得 live map、不執行完整
runtime gate，也不代表 pilot ready 或 navigation PASS。

## Code Review 後的一組 Pilot Pair

第一個 live pair 是 `pilot-00`，不納入精度統計。開始前：

1. 在 Isaac 目前 Stage 取得 root-layer identifier，確認解析到與 `--scene` 同一檔案。
2. 對該檔案計算 SHA-256，保存 identifier 與 digest；不可猜場景名稱。
3. 完成一次明確 pair initialization：載入固定場景、Play、restart Nav2、定位健康、無 active
   goal，然後指定本次 simulation epoch。
4. `reset_policy` 只是描述 pair 開始前的操作，不會讓 runner 自動 restart、Replay 或
   reposition。

目前 `--live-scene-sha256` 是操作員另行擷取的 active Stage identity claim。Runner 會把它與
`--scene` 檔案 SHA 比對，但不會自行查詢 Isaac GUI。無法可靠取得 root-layer identifier 時，
不得執行 live capture。

R1：

```bash
uv run python scripts/isaac_nav_differential.py capture \
  --mode R1_bridge_nav2 \
  --location map_left_down \
  --pair-id pilot-00 \
  --simulation-epoch warehouse-play-20260731-01 \
  --reset-policy nav2_restart \
  --scene /absolute/path/to/warehouse.usd \
  --live-scene-sha256 "<active-stage-root-layer-sha256>" \
  --output artifacts/nav-diff/pilot-00-r1.json \
  --execute \
  --confirm "I UNDERSTAND THIS WILL MOVE THE ISAAC SIM ROBOT"
```

完成 R1 後，以明確記錄的 non-Replay reposition 回到同一起點（建議 `Dock`），再次確認
定位、stationary、clock 與無 active goal，再執行 R2：

```bash
uv run python scripts/isaac_nav_differential.py capture \
  --mode R2_jenai_no_retry \
  --location map_left_down \
  --pair-id pilot-00 \
  --simulation-epoch warehouse-play-20260731-01 \
  --reset-policy nav2_restart \
  --scene /absolute/path/to/warehouse.usd \
  --live-scene-sha256 "<active-stage-root-layer-sha256>" \
  --output artifacts/nav-diff/pilot-00-r2.json \
  --execute \
  --confirm "I UNDERSTAND THIS WILL MOVE THE ISAAC SIM ROBOT"
```

`--location` 是目的地，不是起點。Pair 內不得隱藏 Replay；若中途 Stop／Play，必須更換
simulation epoch，保存現有 artifact，並把該 pair 判為無效。

離線比較：

```bash
uv run python scripts/isaac_nav_differential.py compare \
  --r1 artifacts/nav-diff/pilot-00-r1.json \
  --r2 artifacts/nav-diff/pilot-00-r2.json \
  --output artifacts/nav-diff/pilot-00-comparison.json
```

Pilot 只驗證：goal 等價、runtime fingerprint、T0/T1、唯一 UUID、clock、final window、
cleanup、artifact reload 與 comparison 全部可靠。任何一項失敗都先修 Harness，不修導航。

## 正式五組順序

Pilot 通過後才執行，且每組中間使用同一種、明確記錄的 non-Replay reposition：

```text
pair-01: R1 → reposition → R2
pair-02: R2 → reposition → R1
pair-03: R1 → reposition → R2
pair-04: R2 → reposition → R1
pair-05: 以預先保存的固定 seed 決定順序
```

每組 R1／R2 必須使用同一 pair ID、simulation epoch、reset policy、scene／map／Site、clean
Git SHA、JenAI import path、Nav2 runtime fingerprint、ROS domain／RMW／DDS、actual goal 與
measurement contract。

## 離線分類

```text
GOAL_PAYLOAD_DIFFERENCE
MAP_POSE_DIFFERENCE
ACTUAL_ENDPOINT_DIFFERENCE
LOCALIZATION_GROUND_TRUTH_DIVERGENCE
JENAI_VERDICT_ONLY_DIFFERENCE
PAIRING_GATE_FAILED
RUNTIME_STACK_IDENTITY_DIFFERENCE
INSUFFICIENT_EVIDENCE
```

- `MAP_POSE_DIFFERENCE`：R1／R2 的 ROS map-frame final pose超過 0.05 m 或 0.15 rad；無
  ground truth 時仍可使用，但不能宣稱實際車體停偏。
- `ACTUAL_ENDPOINT_DIFFERENCE`：兩邊都有可信 map-frame ground truth 才可使用。
- `LOCALIZATION_GROUND_TRUTH_DIVERGENCE`：任一 run 的 map pose 與 ground truth 超過門檻。
- `JENAI_VERDICT_ONLY_DIFFERENCE`：只有在沒有 map／actual／localization difference 時才可用。
- 空 classifications 且 `included=true`：完整證據沒有支持量測差異；不是缺少證據。

第一批 artifact 出來前，不調整 0.5 秒、AMCL、Nav2 goal checker、endpoint retry 或 arrival
tolerance。只有 evidence 穩定支持單一假設時，才另開 production behavior 修復 PR。
