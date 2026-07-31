# NXDog Capability Mapping

> 狀態：Proposed mapping；NXDog motion 仍禁止

本文件將 Nexuni interface 映射到 JenAI 的 `Capability`、`Completion Contract` 與
`Evidence`。它不註冊新 Capability，也不表示 NXDog 已支援 motion。

## Evidence-to-outcome policy

本節是這組 NXDog assessment 文件的 canonical policy：

- `vendor_command_accepted` 是 Evidence verdict，不是 `TaskOutcome`。
- 實體效果無 authoritative feedback 時，terminal outcome 使用 JenAI 既有封閉集合中的
  `partial` 或 `unavailable`，並附 `physical_effect_unverified` limitation。
- 只有 Capability-specific Completion Contract 所需的 fresh Evidence 完整時，才可使用
  `succeeded`。

其他 NXDog 文件只引用本節；不得各自建立第二套 outcome vocabulary。

## 判定等級

| 等級 | 意義 |
|---|---|
| `observation_available` | 目前 read-only Adapter 可取得 typed observation |
| `candidate` | vendor interface shape 足以開始設計，但仍缺 Runtime／vendor contract／acceptance |
| `unavailable` | 缺少可執行或可驗證的 contract |
| `prohibited` | 不得成為 Agent Capability 或繞過 Robot Runtime Authority |

這個等級只描述 integration maturity，不取代上述 Evidence-to-outcome policy。

## Runtime execution ownership

Agent／Fast Path只選Capability與typed input。Runtime接受Task後，只有Robot Runtime
Authority可以建立Approval、Task lifecycle，以及Workflow Capability對應的mutable
Workflow Instance；Completion Contract evaluation、Task Outcome與Receipt也只有Authority
能改變。Capability Executor及下列ports只執行Authority已准入的step並回傳typed
Events／Evidence：

| Internal execution path | Scope |
|---|---|
| `NavigationGateway` | `compute_route`、`navigate`及Workflow中的movement；不得承接indicator／posture／charging |
| `PlatformCommandPort` | `set_indicator`、`set_posture`、`auto_charge`等allowlisted non-navigation write |
| `ObservationPort` | `inspect_state`與Completion Contract所需的health／pose／velocity／map／battery Evidence |
| Runtime provider-free STOP | robot-wide epoch invalidation、task cancellation與各port bounded cleanup |

NXDog v0中的Authority、Capability Executor與Adapter在robot-side companion co-locate；
public Runtime protocol只存在caller→Authority。未來remote Authority→Edge需要獨立ADR，
不得讓Adapter成為第二個Task authority。

## Mapping

| JenAI Capability | Nexuni source | 目前等級 | Completion Contract 所需 evidence | 目前缺口 |
|---|---|---|---|---|
| `inspect_state` | heartbeat、odom、current map、battery | `observation_available` | source/freshness、pose、velocity、map、health limitations | HTTP evidence 無 vendor timestamp、frame/covariance、map digest |
| `compute_route` | `ComputePrmPath`／`ComputeRoute` | `candidate` | accepted goal、typed result、path、map identity、planning error | reference HTTP blocking；route/map semantics未固定 |
| `navigate` | `NavigateToPose` | `unavailable` | command lease、goal accepted/UUID、progress、terminal result、final pose window、stationary evidence | Runtime v0、cancel ack、map identity、freshness、physical acceptance皆未完成 |
| `emergency_stop` | action cancel；可能搭配 internal zero command | `unavailable` for NXDog | cancel requested、cancel acknowledged、terminal result、zero command、fresh velocity、physical limitation | `/stop` 只有 request；沒有 ack 或 physical-stop evidence |
| `set_indicator` | `/nxdog/cmd_vui` | `candidate` | command accepted；若有真實 feedback，再加入 observed state | `/color` 與 `/brightness` 只讀 process shadow，不是硬體 read-back |
| `set_posture` | `SportCommand` | `candidate` | service response、posture/stance observation、operator safety acknowledgement | 沒有 posture feedback；HTTP 丟棄 service result |
| `auto_charge` | charging command/result、`BatteryState` | `unavailable` | command correlation、dock/charge state、fresh current/voltage、sustained charging | result 沒有 command ID；HTTP charging bool 過度簡化 |
| `switch_map` | `SwitchMap` | `candidate` internal operation | requested/observed map identity、localization reset/convergence | map name不是 content identity；不應由 Agent任意選 |
| `set_initial_pose` | `/initialpose` | `prohibited` as Agent tool | operator-controlled localization procedure | publish 不等於收斂；屬操作／部署能力 |
| `raw_velocity` | `/cmd_vel_*`／`POST /set_cmd_vel` | `prohibited` | 不適用 | LLM/interaction surface 不得直接控制速度 |
| `toggle_avoidance` | `/nxnav/avoidance_enabled` | `prohibited` | 不適用 | 低階安全策略不可成為 Agent tool |
| `resume_navigation` | reference `resume()` | `unavailable` | 保存原 command、重新驗證、重新取得 lease、重新送 goal | reference implementation 是空方法 |

## `inspect_state`

ADR 0005 已允許 `JenAI doctor` 取得 NXDog read-only observations。這些 observation
只能支持「目前可看到什麼」，不能支持「可以安全導航」：

- heartbeat recently received 不等於 localization ready。
- client objects initialized 不等於 action server available。
- map group name 不等於 content-bound `Map Identity`。
- cached odom 不等於 fresh pose 或 ground truth。
- charging current flag 不等於已完成 docking/charging。

在 Robot Runtime Protocol v0 中，read-only Adapter 應投影為 `RuntimeState` 與
`EvidenceEnvelope`，經`ObservationPort`保留`observed_at`、`received_at`、source、
freshness verdict、content digest、transport security、source assurance／attestation與
limitations；無source timestamp時必須明確標記，這些evidence dimensions不得互相替代。

## `compute_route`

這是 motion 前最適合的 navigation integration：

```text
typed route request
→ Robot Runtime Authority
→ Capability Executor
→ Navigation Gateway
→ co-located NXDog navigation Adapter
→ ComputePrmPath／ComputeRoute
→ typed route evidence
```

它仍須使用command ID、server-clamped execution budget、map identity與result taxonomy，
但不取得motion lease。路徑只可作為preview/evidence；不能因path存在就宣稱navigation
ready。

## `set_indicator`

LED 是第一個低物理風險 write candidate，但需修正「read-back」說法。
`NxDogPlatformClient.vui_set_color()` 在 publish 後立即改寫 process-local
`vui_current_color`；`GET /color` 只回傳此變數。因此：

```text
Robot Runtime Authority
→ Capability Executor
→ PlatformCommandPort
→ co-located NXDog Adapter
→ vendor VUI command accepted
AND
GET shadow == requested
```

最多支持：

```text
Evidence: vendor_command_accepted
TaskOutcome: partial
Limitation: physical_effect_unverified
```

它可以驗證 authentication、approval、idempotency、lease、event、receipt 與 retry
行為，但不能證明 LED 硬體實際改色。若 Nexuni 提供 device-originated feedback，才可
新增 `indicator_state_observed` evidence，並把 Completion Contract 升級。

## `emergency_stop`

Runtime 必須把以下 evidence 分開，禁止以單一 `success` 合併：

```text
cancel_requested
cancel_acknowledged
goal_terminal
zero_velocity_command_published
zero_velocity_observed
physical_stop_unverified
```

NXDog reference client 的 `cancel()` 只提供第一項。即使未來 Edge Adapter 能從 ROS
action cancel response取得第二項，也不能由此推論車體已停止。停止路徑必須
provider-free、不需批准、提高 safety epoch、撤銷所有舊批准/lease，且不等待一般
command queue。

## `navigate`

NXDog navigation 要進入 `implemented_unvalidated` 前，必須同時滿足：

1. Robot Runtime Authority 已在 Isaac/Nav2 fake/live vertical slice 驗證。
2. Robot Runtime Authority與NXDog Adapter已co-locate在唯一robot-side deployment，且只有
   該Adapter持有vendor ROS action client。
3. typed request 綁定 active Site Profile 與可驗證 map identity。
4. action goal acceptance、UUID、feedback、cancel response 與 terminal result 可關聯。
5. final pose/velocity evidence 具有 source time、frame 與 freshness。
6. software stop acceptance 與人工 E-stop procedure 已通過。
7. 至少一條短距離 physical acceptance 可重播並保存失敗場次。

在這些 gate 前，不得只靠 vehicle config 把 `navigate` 加入 quadruped Capability Card。

## Agent exposure policy

Agent 只可看見 platform-neutral Capability：

```text
inspect_state
compute_route
navigate
emergency_stop
set_indicator
set_posture
auto_charge
```

以下名稱與 transport 永遠不得進入 Agent、TUI、WebUI 或 MCP tool graph：

```text
/nxnav/*
/nxdog/*
/cmd_vel_*
/set_cmd_vel
/set_initialpose
/nxnav/avoidance_enabled
SportCommand identifier
vendor map filesystem path
```

Interaction surfaces 只提交 typed high-level command，並投影 Runtime event/evidence；
它們不建立 `NxNavClient`、不加入 NXDog DDS domain，也不直接呼叫 port `5088` motion
endpoint。

## Source basis

- Nexuni interface 與 reference behaviour：
  [`INTERFACE_INVENTORY`](INTERFACE_INVENTORY.md)
- JenAI observation-only decision：
  [`ADR 0005`](../../adr/0005-nxdog-http-is-an-observation-only-runtime-adapter.md)
- JenAI single authority direction：
  [`ADR 0006`](../../adr/0006-single-high-level-http-robot-runtime.md)
- 現行產品 Capability contract：
  [`src/jenai/capabilities.py`](../../../src/jenai/capabilities.py)
