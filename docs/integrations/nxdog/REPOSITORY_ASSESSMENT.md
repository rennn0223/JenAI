# NXDog Developer Kit Repository Assessment

> 狀態：Repository assessment／architecture proposal；不是 motion support 宣告
> Nexuni source：`nexuni/nxdog-developer-kit@9cc558172993b6ed9ee239f2e4e8f5e971740d24`
> JenAI baseline：`main@af0e7de71bb99ad5908027a07115356fb11e41c8`
> 評估日期：2026-07-31

## 結論

NXDog developer kit 足以讓 JenAI：

- 將 `interfaces/nxnav_msgs` 與 `interfaces/nxdog_interfaces` 視為 robot-side ROS 2
  API 的 **public compatibility definitions**；
- 以 reference clients 理解目前範例所用的 ROS graph 與資料流；
- 以 Flask example 做 discovery、唯讀 observation prototype 與風險分析。

但它不足以讓 JenAI：

- 把 Flask routes、Python client methods、topic/service/action names 或 error codes
  全部當成穩定 vendor contract；
- 把 `/stop` 的 HTTP success 當成 cancel acknowledgement 或實體停止 Evidence；
- 把 action `error_code == 0`、cached odom、`ready_flag`、map group name 或正電池電流
  當成 JenAI Completion Contract 已完成；
- 複製或重新散布 vendor example implementation；該固定 commit 沒有
  repository-level `LICENSE`、`COPYING` 或 `NOTICE`，且兩個 ROS package 的授權標記
  不一致。

Vendor 自己明示 Flask backend 是經裁切的 reference example，不是 drop-in production
SDK；相對地，ROS 2 definitions 才是 robot-side services 的 public compatibility
definitions。[README][vendor-readme-contract]
[integration guide][vendor-example-status] 目前最合理的決策因此是：

1. 保留 JenAI 現行 NXDog observation-only 狀態。
2. 不複製 Flask 或 reference-client implementation。
3. 先釐清授權與 vendor runtime contract。
4. 若要做 motion，將 Robot Runtime Authority 與 NXDog Adapter 一起部署在 authenticated
   robot-side companion；所有移動步驟仍通過共用 Navigation Gateway，非移動操作與觀察
   則通過 Runtime-owned typed internal ports，不得直呼 vendor。
5. 在註冊 NXDog motion Capability 前，完成不依賴 LLM 的實體 navigation、cancel、
   stop、endpoint 與 Evidence acceptance。

這延續 JenAI 已接受的 [ADR 0005][jenai-adr-0005] 與
[ADR 0006][jenai-adr-0006]，不改寫現行產品邊界。JenAI 的 Robot Runtime Seam 是
Workflow 導航、觀察與返航所依賴的小型 typed interface；ROS 2、Nav2、Isaac Sim 與
robot-specific SDK 必須留在 seam 的另一側。[Domain Context][jenai-context-runtime]

## Assessment deliverables

- [Interface Inventory](INTERFACE_INVENTORY.md)：ROS／HTTP shape、names與evidence限制。
- [Capability Mapping](CAPABILITY_MAPPING.md)：Capability maturity與Completion Contract缺口。
- [Deployment Options](DEPLOYMENT_OPTIONS.md)：direct HTTP、direct DDS與co-located Runtime
  deployment比較。
- [Vendor Gaps](VENDOR_GAPS.md)：需向Nexuni取得的授權、版本、安全與evidence契約。
- [Robot Runtime Protocol v0 Draft](ROBOT_RUNTIME_PROTOCOL_DRAFT.md)：
  `submit`／`observe`／`stop`三入口與hidden authority model。
- [Physical Acceptance Plan](PHYSICAL_ACCEPTANCE_PLAN.md)：read-only到charging的分階段gate。

## 評估方法與可信度標籤

本評估完整閱讀以下固定 source：

- Nexuni `README.md`、`docs/integration/**`、`docs/getting-started/**`、
  `docs/mapping/**`；
- `examples/backend/http-api-server/**` 的 Flask app、navigation types、
  nxnav reference client 與 platform reference client；
- `interfaces/nxnav_msgs/**`、`interfaces/nxdog_interfaces/**`；
- 固定 commit 的完整 Git tree 與所有可見授權標記；
- JenAI 的 domain context、architecture、ADR 0005、ADR 0006、NXDog observer、
  Workflow runtime protocol、Navigation Gateway、操作文件與 regression tests。

本文使用三種標籤：

- **Source fact**：source 直接陳述或可由完整 definition 精確讀出。
- **Code inference**：由 example implementation 的控制流推得；不是 vendor 承諾。
- **Unknown vendor contract**：公開 source 沒有定義，必須由 Nexuni 確認。

Vendor implementation code 沒有被複製到本文件；下列 interface proposal 是 JenAI
原創的 architecture sketch。

## Repository 性質與適用界線

### Source facts

Repository 自稱 public customer-facing documentation 與 reference integration
examples，目的是協助客戶連線、使用產品工具及整合 robot-side services。
[README][vendor-readme-purpose] 它包含 product guides、HTTP example，以及符合
robot-side nxnav／platform driver API 的 ROS 2 type definitions。
[README][vendor-readme-includes]

Flask example 的預期環境是 Ubuntu、ROS 2 Foxy、Python 3、Flask／Flask-Cors、
build 後的兩個 interface packages、可見的 robot ROS graph，以及相容的 map
directory。[Integration guide][vendor-example-environment] 文件把安裝命令稱為
starting point，而非 fully validated installer。[Integration guide][vendor-example-setup]

範例 server 使用 TCP `5088`，`NXNAV_MAPS_DIR` 預設指向
`/var/lib/nxdog/nxnav-maps`。[Integration guide][vendor-example-run]
README 另外列出 Pi 5 security frontend、Jetson mapping frontend 與 example backend
URL；官方 connection guide 的建議拓撲是 PC、Pi 5 與 Jetson Orin NX 位於同一 LAN。
[README][vendor-product-urls] [Connection guide][vendor-connect-lan]

Mapping guide 描述兩階段流程：先產生／整理 point cloud，再製作 2D occupancy map、
標註 zones、產生 PRM graph 並 export `.zip` map bundle；map name 不得含 `-`。
[Mapping guide][vendor-mapping-workflow]

### 評估界線

以上內容證明「可開始 integration discovery」，不證明特定 robot firmware、nxnav
build、ROS domain、frame、QoS、error enum、timeout、concurrency 或 safety behaviour
與此 commit 永久相容。Vendor 文件本身要求依 robot software version、ROS 2 domain、
deployment layout 與 customer application 調整 example。
[Integration guide][vendor-example-status]

## Public compatibility interfaces

Vendor 明示 `interfaces/` 定義 robot-side nxnav 與 platform driver 接受的 ROS 2
message、service、action types，且是 developer kit 的 public integration contract。
[ROS 2 interface guide][vendor-ros-contract] 這個 contract 精確保證的是 **type
shape**；topic／service／action instance name 與 operational semantics 仍須分開判讀。

`nxnav_msgs` `0.1.0`（metadata：`Proprietary`）定義 6 messages、3 services 與
5 actions；`nxdog_interfaces` `0.0.0`（metadata：`Apache-2.0`）定義
`SportCommand.srv`。[package metadata][vendor-nxnav-package]
[interface list][vendor-nxnav-interface-list]
[platform metadata][vendor-platform-package] 導航 action確實提供goal/result/feedback
shape，但definitions沒有JenAI所需的command lease、safety epoch、source freshness、
map digest或Task Outcome contract。[Navigate action][vendor-navigate-action]
[MapData][vendor-map-data] [SportCommand][vendor-sport-command]

完整逐項shape、reference name、freshness、timeout、cancel與evidence限制整理在
[Interface Inventory](INTERFACE_INVENTORY.md)。Interface defaults只是type shape，不得
直接取代Site Profile／vehicle profile經驗證的Completion Contract。

## Reference clients：可借鏡，不是 compatibility contract

### `NxNavClient`

`NxNavClient` 對 caller 使用 plain Python dataclasses，但內部建立 `rclpy` node 與
`MultiThreadedExecutor`；「public API surface 沒有 ROS dependency」指的是 caller
arguments/results，不是 implementation 不依賴 ROS。
[Reference client introduction][vendor-nxnav-client-intro]
[lifecycle][vendor-nxnav-client-lifecycle]

Reference client 使用以下 ROS graph names：

| Direction | Name／type | Source fact |
|---|---|---|
| Subscribe | `/nxnav/odom` (`nav_msgs/Odometry`) | pose 與 velocity cache。[client wiring][vendor-nxnav-client-wiring] |
| Subscribe | `/nxnav/current_map` (`std_msgs/String`) | transient-local map-name cache。[client wiring][vendor-nxnav-client-wiring] |
| Subscribe | `/nxnav/heartbeat` (`std_msgs/Bool`) | heartbeat arrival time；payload value 未被使用。[client wiring][vendor-nxnav-client-wiring] |
| Publish | `/cmd_vel_low`、`/cmd_vel_mid`、`/cmd_vel_high` (`geometry_msgs/Twist`) | priority-specific raw velocity publisher。[client wiring][vendor-nxnav-client-wiring] |
| Publish | `/nxnav/avoidance_enabled` (`std_msgs/Bool`) | latched avoidance switch。[client wiring][vendor-nxnav-client-wiring] |
| Publish | `/initialpose` (`PoseWithCovarianceStamped`) | initial localization pose。[client wiring][vendor-nxnav-client-wiring] |
| Action | `/nxnav/navigate_to_pose` | `nxnav_msgs/action/NavigateToPose` client。[client wiring][vendor-nxnav-client-wiring] |
| Action | `/nxnav/compute_prm_path` | `nxnav_msgs/action/ComputePrmPath` client。[client wiring][vendor-nxnav-client-wiring] |
| Service | `/nxnav/switch_map`、`/nxnav/set_map` | Both use `nxnav_msgs/srv/SwitchMap` in the example.[client wiring][vendor-nxnav-client-wiring] |

這些 names 出現在 reference client，卻沒有出現在 ROS type definitions 或
`ros2-interfaces.md` 的 formal list，所以本評估不把它們稱為保證穩定的 public
contract。[ROS 2 interface guide][vendor-ros-contract]

其他重要 source facts：

- `is_ready()` 只檢查 client 是否 started、node/executor/thread 與 action/service client
  objects 是否存在；它不檢查 action server、service、heartbeat、localization 或 map。
  [client code][vendor-nxnav-client-ready]
- `is_alive()` 表示最近 `6 s` 內收過 heartbeat；`current_map` cache 超過 `3 s` 會清成
  `None`。[timeout constants][vendor-nxnav-client-timeouts]
  [client code][vendor-nxnav-client-cache]
- pose 與 velocity 是 subscription callback 更新的 in-memory cache；public getters
  沒有 source timestamp 或 covariance。[client code][vendor-nxnav-client-observations]
- `goto()` 先等 action server `5 s`，然後送 `map` frame goal 與 map/tolerance/speed；
  feedback callback 丟棄所有 feedback。[client code][vendor-nxnav-client-goto]
- action result 的 example success 判斷只有 `error_code == 0`，沒有在 client 內再驗證
  final map、pose、tolerance 或 stopped state。[client code][vendor-nxnav-client-result]
- `pause()` 與 `cancel()` 都呼叫 `cancel_goal_async()`；`cancel()` 沒有等待 result/ack，
  就清除 local goal handle 與 current goal。`resume()` 沒有重新送 goal。
  [client code][vendor-nxnav-client-control]
- `set_cmd_vel()` 將 caller floats 發到選定 topic；example 裡看不到 clamp、rate
  enforcement 或 command watchdog。[client code][vendor-nxnav-client-cmd-vel]
- `compute_route()` 並未呼叫 public `ComputeRoute.srv`；它將相鄰 goals 逐段送入
  `ComputePrmPath` 再拼接 visualization path。[client code][vendor-nxnav-client-route]

`nav_types.NavResultCode` 列出 success、localization fail、cancelled、stuck、planning
fail，但這組 enum 位於 example Python wrapper，並未宣告在 ROS action definition
內。[Reference datatypes][vendor-nav-result-code]
[Navigate action][vendor-navigate-action] 未獲 vendor 確認前，不應將其視為 exhaustive
或 stable wire enum。

### `NxDogPlatformClient`

Platform reference client 使用：

- `/nxdog/sport` service；
- `/nxdog/cmd_vui` 與 `/nxdog/speaker` publishers；
- `/nxdog/battery` subscription；
- `/nxdog/auto_charging_cmd` publisher 與
  `/nxdog/auto_charging_result` subscription。

[Platform wiring][vendor-platform-wiring]

重要限制如下：

- sport allowlist 是 reference-client code；invalid value 會直接 return，service response
  只被寫入 log。[Platform sport client][vendor-platform-sport]
  [response handler][vendor-platform-sport-result]
- VUI color／brightness getters 回傳 client 本地 cache，而非 robot readback；
  setters 也會改寫其他 cached field。[Platform VUI client][vendor-platform-vui]
- `is_charging` 僅以 `BatteryState.current > 0` 更新；它沒有 connector、voltage、power、
  persistence 或 battery-increase contract。[Platform battery callback][vendor-platform-charging]
- start／stop charging 共用單一 mutable callback；收到字串 `"true"` 時才回報 result。
  [Platform charging client][vendor-platform-auto-charge]

## Flask example

### 定位與啟動方式

Flask app 在 module import 時就啟動一個 navigation client，稍後再啟動一個 platform
client；它啟用全域 CORS，以 threaded Flask server bind `0.0.0.0:5088`。
[Flask initialization][vendor-flask-init]
[Flask startup][vendor-flask-startup]

完整 app 定義 21 個 distinct paths、23 個 method/path operations：

| 類別 | Operations | 輸入／回傳重點 |
|---|---|---|
| Map／pose | `GET /maps`、`POST /map`、`GET /current_map`、`POST /set_initialpose`、`GET /odom` | scan map directory；switch map；回傳 group/tile；設定 map＋initial pose；回傳 cached pose。[Flask code][vendor-flask-map] |
| Navigation／planning | `POST /navigate`、`GET /nav_plan`、`POST /compute_route`、`GET /pause`、`GET /resume`、`GET /stop` | navigation 等 callback 最多 `600 s`；plan/route 是 JSON projection；pause/stop/resume 立即回 status string。[Flask code][vendor-flask-navigation] |
| Direct motion | `POST /set_cmd_vel` | JSON `vx`／`vy`／`wz`，經 mid-priority publisher 後立即回 success。[Flask code][vendor-flask-cmd-vel] |
| Health／state | `GET /get_ready_flag`、`GET /nav_health`、`GET /velocity` | expose local readiness、heartbeat freshness 與 cached velocity；small `vx`/`vy` 會被顯示成 zero。[Flask code][vendor-flask-observation] |
| Charging | `POST /charging`、`POST /auto_charging_stop`、`GET /is_charging` | start 最多等 `360 s`、stop 最多等 `30 s`；state 來自 platform client 的 current-sign heuristic。[Flask code][vendor-flask-charging] |
| VUI／posture | `GET/POST /color`、`GET/POST /brightness`、`POST /set_sport_action` | getters 是 client cache；POST handlers 在 forwarding 後立即回 success。[Flask code][vendor-flask-platform] |

文件聲稱 example 包含 VUI volume，但 Flask app 沒有 volume route；只有 platform
reference client 有 `vui_set_volume()` method。[Integration guide][vendor-example-endpoints]
[Platform client][vendor-platform-volume] 這個不一致再次說明「guide／reference code」
不應被當成正式、完整且版本化的 HTTP contract。

### 安全與 Completion Contract 缺口

以下先分清 source fact 與 code inference。

| 標籤 | 觀察 | JenAI 判讀 |
|---|---|---|
| Source fact | `/stop` 呼叫 `api.cancel()` 後立即回 `success stop`；client 的 `cancel()` 只送 asynchronous cancellation，未等待 acknowledgement 就清 local handle。[Flask][vendor-flask-stop] [client][vendor-nxnav-client-control] | HTTP response 只證明 handler 執行到 return。 |
| Code inference | `/stop` 不能證明 action server 接受 cancellation、controller 停止、robot velocity 歸零或實體底盤停止。 | 不得用它支援 `TaskOutcome.CANCELLED` 或 safety halt；需要 cancel ack 與 fresh stopped Evidence。 |
| Source fact | `/navigate` 等 callback 最多 `600 s`；timeout path 沒有呼叫 cancel，失敗 response 使用 local `status`。[Flask][vendor-flask-navigate] | Handler timeout 與 robot goal lifecycle 是兩件事。 |
| Code inference | HTTP disconnect 或 `600 s` timeout 後，goal 可能仍在 robot-side action server 執行。 | Runtime 必須以 command ID 追蹤 goal，deadline 到期後執行 bounded cancellation/cleanup。 |
| Source fact | navigation callback success 只看 action result `error_code == 0`。[client][vendor-nxnav-client-result] | Example 沒有 final pose、map identity 或 stopped verification。 |
| Code inference | Example 的 success 不足以滿足 JenAI Completion Contract。 | JenAI 仍須以 final pose／map／tolerance／freshness Evidence 評估 Task Outcome。 |
| Source fact | Flask app 是 threaded；navigation client 只有一份 `_result_callback`、goal handle 與 current goal，charging client 也只有一份 mutable callback。[Flask startup][vendor-flask-startup] [navigation state][vendor-nxnav-client-state] [charging state][vendor-platform-auto-charge] | Example source 沒有 per-request command correlation。 |
| Code inference | Overlapping `/navigate` 或 charging calls 可能覆寫 callback／ownership；公開 source 沒有 single-active-command lease。 | 不應讓 TUI、WebUI、MCP 或多個 Workflow caller 直接競爭這些 routes。 |
| Source fact | `GET /get_ready_flag` 投影 local client object readiness；heartbeat 是另一個 freshness check。[client][vendor-nxnav-client-ready] [timeout constants][vendor-nxnav-client-timeouts] [heartbeat][vendor-nxnav-client-cache] | `ready_flag == true` 不等於 localization、map、action server 或 navigation ready。 |
| Source fact | `/odom`、`/velocity` payload 沒有 source timestamp；client cache 也沒有 per-sample timestamp。[Flask observation][vendor-flask-observation] [client observation][vendor-nxnav-client-observations] | Response collection time 不能假裝成 sensor source time。 |
| Code inference | 單次 zero velocity response，尤其經 display threshold 後，不能證明持續停止。 | Stop Completion Contract 需要 vendor timestamp、bounded observation window 與明確 tolerance。 |
| Source fact | Flask app 啟用 CORS、bind all interfaces；完整檔案看不到 authentication、authorization、request signing、API version、idempotency key、safety epoch 或 event replay。[initialization][vendor-flask-init] [startup][vendor-flask-startup] | Vendor 也沒有把 example 稱為 production SDK。 |
| Code inference | 直接把 `5088` 暴露給一般 LAN callers 會讓任何可達 client 觸發 motion/control routes。 | Production Robot Runtime 必須 authenticated；plain example 僅可在隔離 lab network 做 discovery。 |
| Source fact | Map observations只有 group/tile name；`MapData` 也只有 names與內容，沒有 digest。[Flask map][vendor-flask-map] [MapData][vendor-map-data] | Vendor map name 不是 JenAI Map Identity。 |
| Source fact | VUI／sport POST routes 回 success，不等待 authoritative hardware result；invalid sport value 在 platform client 可被靜默忽略。[Flask platform][vendor-flask-platform] [platform sport][vendor-platform-sport] | HTTP success 不得升級成 posture／device-effect Evidence。 |

## 授權評估

### Source facts

固定 commit 的完整 tree 沒有 repository-level `LICENSE`、`COPYING` 或 `NOTICE`。
可見的授權 metadata 只有：

- `interfaces/nxnav_msgs/package.xml`：`Proprietary`；
- `interfaces/nxdog_interfaces/package.xml`：`Apache-2.0`。

[nxnav package metadata][vendor-nxnav-package]
[platform package metadata][vendor-platform-package]

README 稱 repository 為 public customer-facing developer kit，但「公開可讀」不是
repository-wide reproduction、modification 或 redistribution grant。
[README][vendor-readme-purpose]

### 決策

在 Nexuni 提供明確授權前：

- 不 copy Flask app、reference clients 或 `nxnav_msgs` implementation into JenAI；
- 不假設 `nxdog_interfaces` 的 package tag 可擴張到 sibling package 或 examples；
- 不把 repository 稱為 open source；
- co-located NXDog Adapter 優先依賴 vendor 已安裝／vendor 提供的 generated interfaces；
- 若 JenAI distribution 必須 build 或 redistribute definitions，先取得書面授權與
  attribution／notice requirements。

這是 licensing risk containment，不是法律意見。

## Unknown vendor contract

下列問題在已讀 public source 中沒有完整答案。它們都是 motion integration gate：

| 主題 | 必須由 vendor 確認的 contract |
|---|---|
| Versioning | Developer-kit commit、robot firmware、nxnav、platform driver、ROS distro 與 API compatibility matrix；breaking-change／deprecation policy。 |
| Licensing | Repository examples、`Proprietary` `nxnav_msgs` 的 build/use/redistribution grant，以及 generated artifacts 的條款。 |
| ROS graph | Topic/service/action instance names、namespaces、types、QoS、lifecycle、required domain/network setup 是否屬於 stable public contract。 |
| Goal ownership | 同時多 goal 的 acceptance／preemption semantics、single owner、idempotency、command correlation 與 stale caller handling。 |
| Cancel／stop | Cancel acknowledgement、controller halt、software stop vs hardware emergency stop、最大停止時間、失聯 behaviour 與可驗證 stopped Evidence。 |
| Direct velocity | Valid ranges、units、rate、priority arbitration、timeout/watchdog、zero-command semantics 與 hardware limit。 |
| Result model | Action error-code enum、terminal status mapping、recovery behaviour、retryability 與 error-code stability。 |
| Frames／time | `map`／odom／base frames、clock source、source timestamps、covariance、TF consistency 與 freshness limits。 |
| Map identity | Map group/tile naming、bundle version/content hash、tile transitions、active-map acknowledgement 與 rollback。 |
| Navigation contract | Tolerances是否只是 defaults、endpoint verification、pause/resume semantics、cross-map portal ownership、avoidance-disable policy。 |
| Charging／posture | Authoritative completion signal、failure codes、physical charging confirmation、posture state feedback 與 safe interruption。 |
| Transport security | Supported authenticated channel、TLS/mTLS、credential rotation、network exposure、audit 與 incident response。 |

README 與 integration guide 已明示 example 需依 deployment 調整，因此不得用「source
沒有寫禁止」補成 vendor promise。[README][vendor-readme-contract]
[Integration guide][vendor-example-status]

## JenAI 現況

JenAI 現行 `NXDogObserver` 只並行讀取六個 allowlisted GET endpoints：
`/nav_health`、`/get_ready_flag`、`/current_map`、`/odom`、`/velocity`、
`/is_charging`。Snapshot 明示 transport 未驗證、source timestamps 不可用、
cryptographic map identity 不可用，且多 endpoint collection 不是 atomic robot
state。[NXDog adapter][jenai-nxdog-observer]

Architecture test 強制 NXDog 只能由 Doctor import，且 allowlist 不得包含 navigation、
stop、pause、resume、direct velocity、map switch、charging control 或 sport action。
[Architecture test][jenai-nxdog-architecture-test] 現行 limitation 與 lab-network
操作方式已記錄在 [NXDog read-only runbook][jenai-nxdog-runbook]。

JenAI 的 application-level motion seam 仍是 `NavigationGateway`。它先驗證 registered
Capability、active Site Profile、map identity 與 site assets，才將 goal dispatch 到
robot-side implementation。[Navigation Gateway][jenai-navigation-gateway]
Workflow domain 目前透過小型 `AreaPatrolRuntime` protocol 取得 navigate、inspect 與
return-home results，不 import ROS、adapter、LLM 或 UI。
[Workflow runtime protocol][jenai-workflow-runtime]

ADR 0006 已接受「single authenticated high-level HTTP Robot Runtime authority」方向，
但 production migration 尚未開始；現行 tree 沒有 production runtime HTTP server。
[ADR 0006][jenai-adr-0006] [Architecture][jenai-architecture-runtime]
因此以下 proposal 是下一階段 design input，不是現況描述。

## Recommended product architecture

本評估建議把下一階段定義為 **Robot Runtime v0 + NXDog first physical
backend**，而不是在現有 surface 增加一個 vendor HTTP tool。產品邊界如下：

```text
TUI／WebUI／MCP／Agent／Fast Path
              │ intent interpretation + registered Capability selection
              │ typed high-level Task
              ▼
Single Robot Runtime Authority
  authenticated principal · Approval resource · Workflow Instance
  command lease · safety epoch · event journal
  Completion Contract · Task Outcome · Receipt
              │
              ▼
Capability Executor
  ├─ Navigation Gateway → Isaac/Nav2 or NXDog navigation Adapter
  ├─ PlatformCommandPort → indicator／posture／charging Adapter
  └─ ObservationPort → health／pose／velocity／map／battery Evidence
```

這個安排保留三項既有產品規則：

1. Agent／Fast Path 只選 registered Capability 與 typed input；Runtime 建立並唯一持有
   Task lifecycle，且只為 Workflow Capability 建立 mutable Workflow Instance。純 Workflow
   definition仍負責正常順序、bounded retry、cancel、Evidence requirements與返航，但不在
   Application建立第二份active state；atomic Capability只使用Runtime-owned單步Task lifecycle。
   [Architecture][jenai-architecture-product]
2. Capability Executor將navigation送入共用Navigation Gateway，非導航write送入
   PlatformCommandPort，read-only Evidence送入ObservationPort；TUI、WebUI、MCP 與Agent
   不得直接建立NXDog client或呼叫vendor endpoint。[ADR 0005][jenai-adr-0005]
3. 單一 Robot Runtime authority 擁有 per-robot/domain command lease、safety epoch、
   Approval、Workflow Instance、active command、Completion Contract、event order、Task
   Outcome與immutable Receipt；transport disconnect不等於command cancellation。
   [ADR 0006][jenai-adr-0006]

Protocol v0採co-located topology。Isaac Authority與Isaac/Nav2 Adapter都位於DGX；NXDog
Authority、Capability Executor與NXDog Adapter一起部署在能直接存取robot-side ROS 2
Foxy domain的companion／LAN sidecar。中央JenAI／DGX caller只使用authenticated
high-level Runtime protocol；這條public protocol終止於Authority，不繼續連到Adapter。
未來若拆分Authority與Adapter，必須另立Edge Control Protocol／ADR。這是推薦方案，不是
已完成部署；完整比較見[Deployment Options](DEPLOYMENT_OPTIONS.md)。

## Robot Runtime v0 decision summary

詳細 wire schema、state machine、ordering、error taxonomy 與 replay contract 集中在
[Robot Runtime Protocol v0 Draft](ROBOT_RUNTIME_PROTOCOL_DRAFT.md)。Repository
assessment 只固定下列 architecture decisions：

| Decision | Required contract |
|---|---|
| Execution owner | Intent Layer只選typed Capability；Runtime唯一持有Approval、Task lifecycle、Completion Contract、Task Outcome與Receipt，並只為Workflow Capability建立Workflow Instance。 |
| Public semantic surface | `submit(CommandEnvelope)`、`observe(ObservationRequest)`、`stop(StopEnvelope)` 三個入口。 |
| Wire mapping | `POST /runtime/v0/commands`、`GET /runtime/v0/observations`、`POST /runtime/v0/stop`。 |
| Deployment topology | v0 Authority與Adapter co-located；public Runtime protocol只存在caller→Authority。 |
| Command authority | Authority從Task accepted／awaiting-approval到terminal cleanup管理唯一per-robot/domain lease與local execution fence。 |
| Identity | AuthenticatedPrincipal由transport建立；transport-bound client identity不得抄用payload。Client ID／source surface永遠只是caller claim。 |
| Approval | Server 保存 exact canonical action 與 digest；browser 只看 redacted、operator-readable preview，執行前再比對。 |
| Safety epoch | Global stop 先 durable bump epoch，再 invalidates pending approvals／commands／lease；stop 不因 busy 或 stale caller epoch 被拒絕。 |
| Idempotency | 同 key＋同 canonical request 回既有 command；同 key＋不同 request fail closed。 |
| Timing | Caller只要求timeout；Runtime clamp並分開request freshness、approval expiry、execution、postcondition Evidence、cleanup與STOP budgets；TaskStarted必須發布server-accepted execution deadline。 |
| Observation | Atomic snapshot 加 monotonic `sequence` event stream；sequence 是 replay cursor 與 dedupe key。 |
| Evidence | Vendor-neutral typed envelope分開source time／freshness、content digest、transport security、source assurance／attestation、frame／Map Identity與limitation；缺資料不得補造。 |
| Completion | HTTP 2xx、process exit 或 vendor result code 都不是 Task Outcome；只由 Capability-specific Completion Contract 與 Evidence 決定。 |
| Startup | Durable generation／epoch先前進，再reconcile active vendor work與non-terminal Tasks；完成前effectful admission blocked，STOP仍可用。 |
| Adapter boundary | Adapter 處理 ROS／vendor transport、goal correlation、bounded cancel／cleanup 與 Evidence normalization，不處理意圖、approval 或 product outcome。 |

Runtime 後方只有一個 Capability Executor role：navigation通過既有
`NavigationGateway`，indicator／posture／charging通過`PlatformCommandPort`，state
Evidence通過read-only `ObservationPort`。這些是internal Interfaces，不是public HTTP
API。NXDog Adapter在vendor contract與physical acceptance尚未完成前只能advertise
read-only observation；任何motion operation必須回`capability_unavailable`，不得
fallback到未強化的Flask routes。

## Adoption sequence and gates

詳細場域條件、Evidence 與 abort rules 見
[Physical Acceptance Plan](PHYSICAL_ACCEPTANCE_PLAN.md)。建議依序推進：

| Gate | Scope | Exit criteria |
|---|---|---|
| 0. Published release baseline（completed） | `v2.6.0` 已由 release-only PR 建立，immutable tag 指向 `a648576efa35b7f0ed8a376d34ef88ab8c1a5b18`；Runtime 與 NXDog motion 均未納入。 | CI、Supply Chain、build、wheel lifecycle、七項 Release assets、checksum 與 attestations 已通過；後續工作以該 stable release 為比較基線。 |
| 1. Vendor／legal contract | 取得授權、firmware／ROS compatibility、names/QoS、map/frame/time、cancel/stop、watchdog、charging/posture semantics。 | [Vendor Gaps](VENDOR_GAPS.md) 的 motion blockers 有書面答案。 |
| 2. Runtime parity | 以 in-memory ports 與 co-located Isaac Navigation Gateway 驗證 auth、idempotency、lease、epoch、approval binding、event replay、disconnect、startup reconciliation 與 missing-Evidence outcomes。 | 現有 Isaac approval、cancel、Evidence 與 Task Outcome 無倒退。 |
| 3. NXDog read-only deployment | 在 robot-side companion co-locate Authority與Adapter，將現行 observation snapshot 投影到 common Runtime並保留 stale／timestamp／map limitations。 | 不新增 motion Capability；WebUI 只顯示可證明狀態。 |
| 4. Non-motion write | 先做 indicator command，套用 [canonical Evidence-to-outcome policy](CAPABILITY_MAPPING.md#evidence-to-outcome-policy)。 | Auth、lease、approval、idempotency、receipt 與可用 read-back contract 通過。 |
| 5. Stop contract | 先在靜止狀態演練，再驗 exact cancel correlation、ack、zero-command publication 與 fresh velocity window。 | 明確區分 requested／acknowledged／observed；未證實 physical halt。 |
| 6. Physical navigation | 不透過 TUI 或 LLM，走同一 Runtime／Navigation Gateway 做 compute-route、短距離 navigate、cancel 與 endpoint Evidence。 | 固定場景與起點下通過 independent physical acceptance、security review 與 blocking code review。 |
| 7. Capability registration | 只註冊已通過 Completion Contract 的 high-level Capability。 | Agent surface 沒有 vendor route、raw Twist、sport string 或 arbitrary ROS payload。 |

任何 Gate 第一個 required check 失敗時都保存診斷並停止；不得在同一次 run 任意調參、
重啟或改碼把結果變成 PASS。Simulation、mock、HTTP 2xx 或 operator visual impression
都不能替代 physical Evidence。

## Proposed follow-up PR sequence

0. `v2.6.0` stable release baseline 與 published truth 已完成；下一個 release 版本在功能
   scope、相容性與驗證完成前不預先指定。
1. Robot Runtime v0 schema、in-memory executor ports、durable state/event/reconciliation tests；
   不接NXDog motion。
2. DGX上的co-located Isaac Navigation Gateway parity slice。
3. robot-side companion上的co-located NXDog read-only projection。
4. Indicator write／read-back contract（若無 authoritative read-back，維持 `partial`）。
5. Software stop contract。
6. Short physical navigation。
7. Charging／posture只在 vendor Completion Contract 完整後另開 PR。

每一個 effectful slice 都需要自己的 ADR amendment、security review、targeted live
acceptance 與 rollback plan。這份 assessment 不授權其中任何一項實作。

## Findings summary

1. **可用的正式資產是 type definitions，不是 Flask contract。**
   `interfaces/` 是 vendor 明示的 public compatibility layer；HTTP/Python code 是
   reference template。[ROS interface guide][vendor-ros-contract]
   [README][vendor-readme-contract]

2. **Reference implementation 缺少 JenAI 所需的 command authority。**
   公開 code 沒有 authenticated transport、workflow-wide lease、safety epoch、
   durable event replay 或 per-command Evidence correlation；threaded handlers 共用
   mutable client state。[Flask startup][vendor-flask-startup]
   [client state][vendor-nxnav-client-state]

3. **Stop、timeout 與 success semantics 不足。**
   `/stop` 不等 cancel ack；`/navigate` timeout 不 cancel；action success 不驗 final
   pose。把這些提升為 Task Outcome 會違反 JenAI Completion Contract。
   [Flask stop][vendor-flask-stop] [Flask navigate][vendor-flask-navigate]
   [client result][vendor-nxnav-client-result]

4. **Observation 有價值，但必須保留 limitations。**
   Heartbeat、map name、cached odom／velocity、charging-current heuristic 可作 Evidence
   input，不能單獨證明 navigation ready、fresh pose、stopped 或 physical charging。
   [client cache][vendor-nxnav-client-cache]
   [platform charging][vendor-platform-charging]

5. **授權是 blocking gate。**
   Repo 無總體授權檔；`nxnav_msgs` 標記 Proprietary，
   `nxdog_interfaces` 標記 Apache-2.0。公開可讀不等於可複製／散布。
   [nxnav metadata][vendor-nxnav-package]
   [platform metadata][vendor-platform-package]

6. **建議採 co-located Runtime＋Adapter，不採 vendor HTTP passthrough。**
   Common Robot Runtime只expose high-levelTask、Evidence與Task Outcome；NXDog-specific
   ROS／map／cancel／charging details全部留在co-located Adapter。Navigation只通過
   NavigationGateway，其他平台功能使用各自internal port。[ADR 0006][jenai-adr-0006]
   [Architecture][jenai-architecture-runtime]

## Pinned vendor sources

[vendor-readme-purpose]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/README.md#L1-L8
[vendor-readme-includes]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/README.md#L10-L22
[vendor-readme-contract]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/README.md#L19-L22
[vendor-product-urls]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/README.md#L59-L65
[vendor-connect-lan]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/docs/getting-started/connect-to-dog.md#L11-L42
[vendor-example-status]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/docs/integration/example-backend.md#L1-L10
[vendor-example-environment]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/docs/integration/example-backend.md#L20-L29
[vendor-example-setup]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/docs/integration/example-backend.md#L31-L79
[vendor-example-run]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/docs/integration/example-backend.md#L88-L109
[vendor-example-endpoints]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/docs/integration/example-backend.md#L111-L133
[vendor-ros-contract]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/docs/integration/ros2-interfaces.md#L1-L14
[vendor-mapping-workflow]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/docs/mapping/mapping-quick-start-guide.md#L3-L89

[vendor-nxnav-package]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/package.xml#L1-L20
[vendor-nxnav-interface-list]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/CMakeLists.txt#L9-L27
[vendor-platform-package]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxdog_interfaces/package.xml#L1-L17
[vendor-map-data]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/msg/MapData.msg#L1-L6
[vendor-navigate-action]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxnav_msgs/action/NavigateToPose.action#L1-L17
[vendor-sport-command]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/interfaces/nxdog_interfaces/srv/SportCommand.srv#L1-L4

[vendor-nav-result-code]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/nav_types.py#L16-L26
[vendor-nxnav-client-intro]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/nxnav_client.py#L1-L29
[vendor-nxnav-client-lifecycle]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/nxnav_client.py#L104-L168
[vendor-nxnav-client-state]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/nxnav_client.py#L76-L103
[vendor-nxnav-client-ready]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/nxnav_client.py#L156-L168
[vendor-nxnav-client-timeouts]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/nxnav_client.py#L47-L48
[vendor-nxnav-client-cache]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/nxnav_client.py#L201-L240
[vendor-nxnav-client-observations]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/nxnav_client.py#L174-L213
[vendor-nxnav-client-wiring]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/nxnav_client.py#L273-L345
[vendor-nxnav-client-goto]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/nxnav_client.py#L349-L415
[vendor-nxnav-client-result]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/nxnav_client.py#L397-L415
[vendor-nxnav-client-route]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/nxnav_client.py#L416-L516
[vendor-nxnav-client-control]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/nxnav_client.py#L518-L537
[vendor-nxnav-client-cmd-vel]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/nxnav_client.py#L634-L645

[vendor-platform-wiring]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/platform_driver_client.py#L25-L73
[vendor-platform-sport]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/platform_driver_client.py#L83-L106
[vendor-platform-sport-result]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/platform_driver_client.py#L188-L195
[vendor-platform-auto-charge]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/platform_driver_client.py#L114-L182
[vendor-platform-volume]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/platform_driver_client.py#L133-L139
[vendor-platform-vui]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/platform_driver_client.py#L140-L164
[vendor-platform-charging]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/platform_driver_client.py#L173-L182

[vendor-flask-init]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/app.py#L1-L24
[vendor-flask-map]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/app.py#L26-L328
[vendor-flask-cmd-vel]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/app.py#L91-L104
[vendor-flask-stop]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/app.py#L116-L147
[vendor-flask-navigation]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/app.py#L116-L260
[vendor-flask-navigate]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/app.py#L149-L194
[vendor-flask-observation]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/app.py#L106-L285
[vendor-flask-charging]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/app.py#L299-L391
[vendor-flask-platform]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/app.py#L382-L453
[vendor-flask-startup]: https://github.com/nexuni/nxdog-developer-kit/blob/9cc558172993b6ed9ee239f2e4e8f5e971740d24/examples/backend/http-api-server/app.py#L510-L523

## JenAI local sources

[jenai-context-runtime]: ../../../CONTEXT.md
[jenai-architecture-product]: ../../ARCHITECTURE.md
[jenai-architecture-runtime]: ../../ARCHITECTURE.md
[jenai-adr-0005]: ../../adr/0005-nxdog-http-is-an-observation-only-runtime-adapter.md
[jenai-adr-0006]: ../../adr/0006-single-high-level-http-robot-runtime.md
[jenai-nxdog-runbook]: ../../operations/NXDOG_READ_ONLY.md
[jenai-navigation-gateway]: ../../../src/jenai/tools/navigation_gateway.py
[jenai-nxdog-observer]: ../../../src/jenai/adapters/nxdog.py
[jenai-workflow-runtime]: ../../../src/jenai/workflows/area_patrol.py
[jenai-nxdog-architecture-test]: ../../../tests/unit/test_architecture.py
