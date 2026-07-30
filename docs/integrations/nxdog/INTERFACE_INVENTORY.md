# NXDog Interface Inventory

> 狀態：Architecture assessment，非 production support contract
>
> Nexuni source：[`nxdog-developer-kit@9cc5581`](https://github.com/nexuni/nxdog-developer-kit/tree/9cc558172993b6ed9ee239f2e4e8f5e971740d24)
>
> JenAI baseline：`21b1579f7cabf99f6b8c9cf95f16c48e2973ed38`

本文件分開記錄三種不同強度的事實：

- **Public interface definition**：`interfaces/` 中的 ROS 2 message、service、action shape。
- **Reference behaviour**：範例 client／Flask server 在該 commit 的實作方式。
- **Inference**：JenAI 根據上述 source 作出的產品設計推論；不是 Nexuni 保證。

`interfaces/` 是公開相容性定義，但 repository 並未把所有 type 綁到具名 topic、
service 或 action。只有 reference client 實際建立的名稱才列為「reference name」；
其餘名稱必須向 Nexuni 確認。

## ROS 2 public interface definitions

### Navigation actions

| Type | Goal | Result | Feedback | JenAI evidence potential | 限制 |
|---|---|---|---|---|---|
| `NavigateToPose` | `PoseStamped`、map、BT、XY/yaw tolerance、speed | `error_code`、`error_msg` | current pose、remaining distance、navigation time、recovery count | command lifecycle、progress、terminal result | error taxonomy、cancel terminal semantics 未定義 |
| `ComputePrmPath` | start/goal pose 與 map、smooth | path、planning time、cost、error | text status | route evidence | feedback status taxonomy 未定義 |
| `ComputePath` | start/goal、planner ID | path、planning time、error | 無 | single-map plan | reference client 未使用 |
| `FollowPath` | path、controller、speed、tolerances | error | distance、speed | path execution | reference client 未使用 |
| `Recovery` | recovery ID | error | 無 | recovery terminal result | recovery identifiers 未定義 |

來源：
[`NavigateToPose.action`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/action/NavigateToPose.action)、
[`ComputePrmPath.action`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/action/ComputePrmPath.action)、
[`ComputePath.action`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/action/ComputePath.action)、
[`FollowPath.action`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/action/FollowPath.action)、
[`Recovery.action`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/action/Recovery.action)。

### Navigation services

| Type | Request | Response | JenAI use | 限制 |
|---|---|---|---|---|
| `ComputeRoute` | start/goal map 與 position | success、error、segments、cost | cross-map route evidence | reference client 改用 repeated `ComputePrmPath` |
| `SwitchMap` | map name、load-3D flag | success、message | map transition | map content identity 不在 contract |
| `LoadMap` | YAML path | success、message | 不建議進產品 Runtime | 暴露 robot-local filesystem path |

來源：
[`ComputeRoute.srv`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/srv/ComputeRoute.srv)、
[`SwitchMap.srv`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/srv/SwitchMap.srv)、
[`LoadMap.srv`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/srv/LoadMap.srv)。

### Navigation messages

| Type | Observable content | Evidence limitation |
|---|---|---|
| `NavStatus` | idle/navigating/paused/following/recovering、map、pose、distance | interface 沒有 timestamp；publisher name/QoS 未記錄 |
| `MapData` | map name、zones、paths、named poses | 沒有 content digest 或 version |
| `MapPath` | named door／stair／elevator path 與 ordered waypoints | 沒有 frame、map identity、timestamp 或 path type enum |
| `MapPose` | named pose 與 tolerance | 沒有 frame、map identity 或 timestamp |
| `MapZone` | polygon、type、JSON overrides | arbitrary JSON 需在 Adapter 驗證 |
| `RouteSegment` | portal、maps、exit pose、paths、avoidance flag | 包含關閉 avoidance 的低階策略，不得直接暴露給 Agent |

來源：
[`NavStatus.msg`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/msg/NavStatus.msg)、
[`MapData.msg`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/msg/MapData.msg)、
[`MapPath.msg`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/msg/MapPath.msg)、
[`MapPose.msg`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/msg/MapPose.msg)、
[`MapZone.msg`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/msg/MapZone.msg)、
[`RouteSegment.msg`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/msg/RouteSegment.msg)。

### Platform service

`nxdog_interfaces/srv/SportCommand` 接受一個 `identifier`，回傳 `success` 與
`message`。這可以支持 service-level acceptance，但沒有姿態、關節或動作完成的
observable state，因此不能單獨支持實體姿態成功。

來源：[`SportCommand.srv`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxdog_interfaces/srv/SportCommand.srv)。

## Reference ROS names and behaviour

下表只描述 `NxNavClient`／`NxDogPlatformClient` 在 pinned commit 建立的 ROS entities，
不把它們提升為 vendor 長期穩定保證。

| Reference name | Type | Direction | Reference behaviour | Evidence assessment |
|---|---|---|---|---|
| `/nxnav/heartbeat` | `std_msgs/Bool` | subscribe | 收到任何 message 後以 local monotonic time 記錄；6 秒後視為 not alive | 有 receive freshness，沒有 vendor source timestamp |
| `/nxnav/current_map` | `std_msgs/String` | subscribe | transient-local；3 秒無更新會清空 local state | map name/tile，不是 content identity |
| `/nxnav/odom` | `nav_msgs/Odometry` | subscribe | 只保留 pose 與 twist | message header、frame、covariance 被 reference client 丟棄 |
| `/nxnav/navigate_to_pose` | `NavigateToPose` | action client | 送 goal、保留 goal handle、讀 result | feedback callback 被丟棄；cancel future 未等待 |
| `/nxnav/compute_prm_path` | `ComputePrmPath` | action client | blocking poll，accept 最多 10 秒、result 最多 60 秒 | path/result 可用；timeouts 是 example policy |
| `/nxnav/switch_map` | `SwitchMap` | service client | async callback | success 只代表 service response |
| `/nxnav/set_map` | `SwitchMap` | service client | async callback | type/name pairing需 vendor 確認 |
| `/initialpose` | `PoseWithCovarianceStamped` | publish | 固定 covariance 後 publish | publish completion 不是 localization convergence |
| `/cmd_vel_low|mid|high` | `Twist` | publish | 任意數值、單次 publish | 無 clamp、duration、watchdog contract |
| `/nxnav/avoidance_enabled` | `Bool` | publish | transient-local | 屬低階安全設定，禁止 Agent 暴露 |
| `/nxdog/sport` | `SportCommand` | service client | allowlist 後 async request | HTTP caller看不到 service result |
| `/nxdog/battery` | `BatteryState` | subscribe | `current > 0` 即 local charging flag | 未保存 header、電壓、SOC 或持續時間 |
| `/nxdog/auto_charging_cmd` | `String` | publish | `start`／`stop` | 沒有 command ID |
| `/nxdog/auto_charging_result` | `String` | subscribe | 下一筆 `"true"` 解決目前 callback | 沒有 command correlation 或 timestamp |
| `/nxdog/cmd_vui` | `String` | publish | 文字 command；local shadow state 同步更新 | GET read-back 是 process cache，不是硬體 feedback |
| `/nxdog/speaker` | `String` | publish | robot-local WAV path | 不應暴露任意 filesystem path |

來源：
[`nxnav_client.py`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/nxnav_client.py)、
[`platform_driver_client.py`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/platform_driver_client.py)。

## Reference HTTP interface

Flask server 使用 unrestricted CORS、`0.0.0.0:5088` 與 threaded request handling；
source 中沒有 authentication、authorization、command lease、idempotency key 或
request version。它是 compatibility reference，不是 JenAI Robot Runtime。

| Method/path | Behaviour | Wait | 可保留的 evidence | 產品判定 |
|---|---|---:|---|---|
| `GET /maps` | 掃描 robot-local map directory | bounded by filesystem only | map group names | observation only |
| `GET /nav_health` | `NxNavClient.is_alive()` | immediate | heartbeat recently received | 不等於 ready |
| `GET /get_ready_flag` | local client objects initialized | immediate | client initialized | 不等於 action server available |
| `GET /current_map` | cached map group | immediate | vendor map name | 無 timestamp/digest |
| `GET /odom` | cached pose/map/tile | immediate | pose observation | 無 header/frame/covariance |
| `GET /velocity` | cached twist；小於 0.05 的 vx/vy 被歸零 | immediate | filtered velocity | 不足以確認實體停止 |
| `GET /nav_plan` | 依 current goal 計算 PRM path | 最長約 70 秒 | path/error 只保留部分 | read 可能觸發昂貴 action |
| `GET /is_charging` | cached `BatteryState.current > 0` | immediate | charging-current indication | 非 charging completion contract |
| `GET/POST /color` | local shadow read／publish VUI command | immediate | command shadow | 非硬體 read-back |
| `GET/POST /brightness` | local shadow read／publish VUI command | immediate | command shadow | 非硬體 read-back |
| `POST /compute_route` | repeated PRM planning | per segment 最長約 70 秒 | route geometry | blocking request |
| `POST /navigate` | send action and block for callback | 600 秒 | result code/message | timeout 不取消 goal |
| `GET /pause` | request action cancellation | immediate | request issued | 沒有 acknowledgement |
| `GET /stop` | request action cancellation、立即清 local handle | immediate | request issued | 沒有 acknowledgement/stop observation |
| `GET /resume` | 呼叫空實作 | immediate | 無 | 不可用 |
| `POST /set_initialpose` | set map、publish initial pose | 30 秒 map wait | service response/publish | 不證明 localization converged |
| `POST /map` | set map | 30 秒 | service response | map identity 不可驗證 |
| `POST /set_cmd_vel` | raw mid-priority Twist publish | immediate | publish call | 禁止暴露；無 clamp/duration/watchdog |
| `POST /set_sport_action` | allowlist method 後一律 HTTP success | immediate | HTTP handler returned | service result 未傳回 |
| `POST /charging` | publish start、等待下一個 result | 360 秒 | uncorrelated bool callback | command/result correlation不足 |
| `POST /auto_charging_stop` | publish stop、等待下一個 result | 30 秒 | uncorrelated bool callback | command/result correlation不足 |

來源：
[`app.py`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/app.py)。

文件稱範例包含 VUI volume，但 pinned `app.py` 沒有 volume route；這是 source/document
差異，需向 vendor 確認，不能自行補成產品 contract。

## Cancellation and stop evidence

`NxNavClient.cancel()` 呼叫 `cancel_goal_async()` 後立即清除 local handle；沒有等待
cancel response，也沒有等待 action terminal result。`/stop` 又立即回 HTTP success。
因此這條 reference path 只能支持：

```text
cancel_requested
```

不能支持：

```text
cancel_acknowledged
zero_velocity_command_published
zero_velocity_observed
physical_stop_confirmed
```

正式 NXDog Edge Adapter 應直接保留 ROS action goal UUID/handle、等待 cancel response、
觀察 terminal status，並另以 fresh velocity／平台安全 evidence 評估停止。即使上述
軟體 evidence 完整，仍不得取代實體 E-stop 或宣稱 hardware-safe stop。

## Freshness and identity gaps

- ROS `Odometry` 原始型別具有 header、frame 與 covariance，但 reference client/HTTP
  response 丟棄它們。Edge Adapter 有機會保留，語意仍需 vendor 確認。
- `NavigateToPose` feedback 已定義 current pose、distance、time 與 recovery count，
  reference client 卻丟棄 feedback。Edge Adapter 可轉成 `CommandEvent`。
- map group/tile 不是 JenAI `Map Identity`。需 vendor 提供 map bundle version/digest，
  或由受控部署計算 content-bound identity。
- HTTP collection 不是原子 snapshot；不同 endpoint 的 cached values 可能來自不同時刻。
- reference timeout 使用 local wall/monotonic policy，沒有 vendor-declared deadline contract。

## License boundary

`nxnav_msgs/package.xml` 宣告 `Proprietary`，`nxdog_interfaces/package.xml` 宣告
`Apache-2.0`，repository root 沒有提供統一 `LICENSE`。因此：

- 可以依公開 interface shape 進行互通性 assessment。
- 不得假設可複製 `NxNavClient`、Flask server 或 `nxnav_msgs` definitions 到 JenAI。
- build、redistribution、generated code、修改及商業部署權限都要由 Nexuni 書面確認。

來源：
[`nxnav_msgs/package.xml`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/package.xml)、
[`nxdog_interfaces/package.xml`](https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxdog_interfaces/package.xml)。
