# Isaac Navigation Differential Harness

本工具回答一個很窄的問題：

> 相同場景、起點、Nav2 stack 與 canonical goal 下，直接 bridge→Nav2 與
> JenAI NavigationGateway→Nav2 的輸入、實際終點、定位證據及 verdict 是否不同？

第一版只增加觀測、artifact 與離線分類，不修改：

- Nav2／AMCL 參數
- endpoint retry policy
- arrival tolerance
- Nav2 success 後的 0.5 秒等待
- production navigation flow
- TUI

`navigate_live(..., direct=True)` 是 deprecated 的 odom→cmd_vel fallback，不是 direct
Nav2 baseline；本工具不使用它。

## 模式

| 模式 | 路徑 | 用途 |
|---|---|---|
| `R1_bridge_nav2` | canonical goal → `RosBridgeClient.nav_send` → Nav2 | 隔離 bridge／Nav2 |
| `R2_jenai_no_retry` | 同一 goal → `NavigationGateway` → `nav_live` → Nav2 | 觀測 JenAI policy 與 completion verification |

R2 只在 runner 的記憶內建立
`nav_endpoint_retry_limit=0` 的 config copy；不改設定檔與 production default。

`R0_rviz` 是人工觀測基準，不在第一版 runner 中自動發 goal。若要保存 R0，必須由 ROS
action／bag 證據取得 RViz 實際送出的 PoseStamped 和 goal UUID；不可把畫面游標或
`/goal_pose` 的假設值當作 action server 已接受的 payload。

## Canonical goal

- `map` 與 `/map` 正規化為 `map`。
- quaternion 先正規化；`q` 與 `-q` 視為同一旋轉。
- 比較 quaternion angular distance，不逐欄比較字串。
- ROS timestamp 不要求數字相等；只檢查 clock domain、simulation epoch 與 freshness。
- R1／R2 的 accepted goal UUID 由
  `/navigate_to_pose/_action/status` 的新 UUID 觀測取得。

## Ground truth

Isaac world pose 不可直接與 ROS map pose 相減。只有提供已驗證的 `T_map_world`
calibration，且其 scene／map SHA-256 與本次 runtime identity 一致時，runner 才會產生
`final_ground_truth_map_median`。

沒有 calibration 或 identity 不一致時：

```text
GROUND_TRUTH_UNAVAILABLE
```

不得改用肉眼或未轉換 world 座標宣稱實際終點差異。

Calibration JSON：

```json
{
  "status": "VERIFIED",
  "scene_sha256": "<64 hex>",
  "map_sha256": "<64 hex>",
  "source": "survey artifact URI or local record",
  "translation_x_m": 0.0,
  "translation_y_m": 0.0,
  "rotation_yaw_rad": 0.0,
  "calibration_method": "three-point rigid fit",
  "residual_m": 0.004
}
```

## 執行前

1. 載入固定 Isaac scene 並 Play。
2. 依本組實驗選定單一 reset policy；第一輪不可混用。
3. 若剛 Replay，先依操作文件 restart Nav2。
4. 為這個 Play epoch 指定一個不變的 ID，例如 `warehouse-play-20260728-01`；再次
   Stop／Play 後必須更換。
5. 每場 capture 會 fail closed 檢查：
   - `/clock` 持續前進且未倒退
   - fresh `map → base_link` 可用
   - AMCL covariance 在門檻內
   - odom 顯示車輛靜止
   - action status 沒有活動 goal

Start gate 失敗的 artifact 仍保存，但不得納入 paired comparison。

## 先做不移動的 preflight artifact

```bash
uv run python scripts/isaac_nav_differential.py capture \
  --mode R1_bridge_nav2 \
  --location Dock \
  --pair-id pair-01 \
  --simulation-epoch warehouse-play-20260728-01 \
  --reset-policy nav2_restart \
  --scene /absolute/path/to/warehouse.usd \
  --output artifacts/nav-diff/pair-01-r1-preflight.json
```

未加 `--execute` 不會啟動 bridge goal。

## Live capture

Live capture 會移動 Isaac Sim 車輛，必須提供精確確認字串：

```bash
uv run python scripts/isaac_nav_differential.py capture \
  --mode R1_bridge_nav2 \
  --location Dock \
  --pair-id pair-01 \
  --simulation-epoch warehouse-play-20260728-01 \
  --reset-policy nav2_restart \
  --scene /absolute/path/to/warehouse.usd \
  --output artifacts/nav-diff/pair-01-r1.json \
  --execute \
  --confirm "I UNDERSTAND THIS WILL MOVE THE ISAAC SIM ROBOT"
```

操作員依固定 reset policy 將車輛恢復到相同起點，確認 Play／定位健康後，再執行 R2：

```bash
uv run python scripts/isaac_nav_differential.py capture \
  --mode R2_jenai_no_retry \
  --location Dock \
  --pair-id pair-01 \
  --simulation-epoch warehouse-play-20260728-01 \
  --reset-policy nav2_restart \
  --scene /absolute/path/to/warehouse.usd \
  --output artifacts/nav-diff/pair-01-r2.json \
  --execute \
  --confirm "I UNDERSTAND THIS WILL MOVE THE ISAAC SIM ROBOT"
```

離線比較：

```bash
uv run python scripts/isaac_nav_differential.py compare \
  --r1 artifacts/nav-diff/pair-01-r1.json \
  --r2 artifacts/nav-diff/pair-01-r2.json \
  --output artifacts/nav-diff/pair-01-comparison.json
```

## 第一輪五組順序

避免 warm-up、時間漂移與固定順序偏差：

```text
pair-01: R1 → reset → R2
pair-02: R2 → reset → R1
pair-03: R1 → reset → R2
pair-04: R2 → reset → R1
pair-05: 以硬幣／固定 seed 決定順序並記錄
```

每組 R1／R2 必須使用同一個：

- pair ID
- simulation epoch
- reset policy
- scene／map／Site Profile
- Git SHA／dirty state
- JenAI import path
- Nav2 params fingerprint
- ROS domain／RMW／DDS profile
- canonical goal

## Artifact 與分類

每場保存：

- runtime identity 與 fingerprint
- T0 start gate
- canonical goal
- action status 觀測到的 accepted goal UUID
- request→accept、accept→terminal、terminal→verification latency
- Nav2 terminal event
- JenAI result 與 navigation attempt tags
- 最後兩秒 fresh map pose samples 與 median
- `/clock`、AMCL、covariance、odom、velocity
- 經驗證的 ground truth，或明確 unavailable
- final halt evidence

離線分類只會輸出：

```text
GOAL_PAYLOAD_DIFFERENCE
ACTUAL_ENDPOINT_DIFFERENCE
LOCALIZATION_GROUND_TRUTH_DIVERGENCE
JENAI_VERDICT_ONLY_DIFFERENCE
PAIRING_GATE_FAILED
RUNTIME_STACK_IDENTITY_DIFFERENCE
INSUFFICIENT_EVIDENCE
```

`ACTUAL_ENDPOINT_DIFFERENCE` 必須有已轉換至 map frame 的 ground truth；只有 AMCL／TF
差異時不得使用這個分類。

## 下一階段的條件

第一批 artifact 出來前，不調整 0.5 秒、AMCL、Nav2 goal checker、endpoint retry 或
arrival tolerance。只有 evidence 能穩定支持單一假設時，才另開行為修復 PR。
