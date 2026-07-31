# NXDog Deployment Options

> 推薦：Option C — authenticated Robot Runtime Authority + co-located NXDog Adapter

本比較將「Robot Runtime Authority」與「NXDog Adapter」視為不同 module，但 v0 將兩者
部署在同一 host／deployment unit，不在它們之間建立第二套 wire protocol：

- **Robot Runtime Authority**：JenAI 擁有的唯一 Task、Workflow、Approval、命令與
  Evidence authority。
- **NXDog Adapter**：Authority implementation 內、位於 robot-side ROS 2 環境的
  Adapter；把 platform-neutral execution 轉成 Nexuni interface，並把 vendor feedback
  轉回 typed Evidence。

## Option A：JenAI 直接呼叫 vendor HTTP example

```text
JenAI/TUI/WebUI
      │
      ▼
NXDog Flask :5088
      │
      ▼
nxnav/platform ROS 2
```

| 面向 | 評估 |
|---|---|
| 導入速度 | 最快；read-only observer 已存在 |
| ROS distro | DGX 不需安裝 Foxy interfaces |
| Authentication | source 未提供 |
| Command ownership | source 未提供；threaded server 可同時接受 request |
| Cancellation | `/stop` 只送 cancel request，沒有 acknowledgement |
| Observability | HTTP 丟失 action feedback、timestamps、covariance 與 goal correlation |
| Long command | `/navigate` 最長 blocking 600 秒；disconnect 不代表 command cancel |
| Evidence quality | 適合 compatibility observation，不足以支撐 physical Completion Contract |
| 適用範圍 | 隔離網路內的 read-only assessment |

**結論**：保留為 `Experimental Vendor Compatibility Interface`，不可成為 production
Robot Runtime，也不可由 Agent/TUI/WebUI/MCP 直接使用 motion endpoint。

## Option B：DGX Spark 直接加入 NXDog ROS 2 domain

```text
JenAI on DGX (ROS 2 Jazzy)
      │ DDS/custom interfaces
      ▼
NXDog robot domain (reference: ROS 2 Foxy)
```

| 面向 | 評估 |
|---|---|
| Evidence | 可保留 action feedback、header、covariance、cancel response |
| Latency | 少一層 application transport |
| ROS distro | Nexuni reference 只記錄 Foxy；Jazzy build/wire/QoS compatibility 未獲 vendor 保證 |
| Dependency | DGX 需建置 custom interface；`nxnav_msgs` 標示 proprietary |
| DDS exposure | DGX process 會看見 robot graph，command topics/actions 的暴露面增加 |
| Command ownership | 仍需額外 authority；DDS 本身不阻止第二個 client |
| Deployment | JenAI、ROS environment、RMW、Domain、vendor interface release 綁在一起 |
| Failure locality | vendor ROS 問題會直接進入 JenAI host/runtime |

ROS 2 不同 distro 可能在介面完全相同、RMW/QoS 相容時互通，但 Nexuni repository 沒有
提供 Foxy↔Jazzy support contract。不能把「可能互通」寫成 product guarantee。

**結論**：適合實驗診斷，不是預設產品部署。若 vendor 正式支持、license 允許且網路
隔離充分，可作為受控替代方案。

## Option C：Robot Runtime Authority + co-located NXDog Adapter

```text
DGX Spark callers
┌──────────────────────────────────────┐
│ Intent Layer / TUI / WebUI / MCP     │
└──────────────────┬───────────────────┘
                   │ authenticated public Runtime v0
                   ▼
Robot-side Orin NX／LAN sidecar
┌──────────────────────────────────────┐
│ Robot Runtime Authority              │
│ Workflow / Approval / lease / epoch  │
│ Completion / Outcome / event journal │
│ Capability Executor                  │
│ NXDog Adapter (co-located)           │
└──────────────────┬───────────────────┘
                   │ internal ports + local ROS 2 Foxy
                   ▼
             nxnav / platform driver
```

| 面向 | 評估 |
|---|---|
| ROS distro | Foxy/custom interfaces 留在 robot-side deployment |
| Authentication | caller→Authority 的 Runtime protocol 要求 token/TLS、principal、robot scope |
| Command ownership | Robot-side Authority 擁有唯一 Task lease、Workflow Instance與safety epoch |
| Cancellation | Co-located Adapter 保留 goal handle、等待 cancel response、回傳 typed Event |
| Evidence | Adapter 保留 ROS source time/frame/covariance/feedback，再包成 `EvidenceEnvelope` |
| Network exposure | 不把 DDS graph 或 vendor raw endpoint暴露給 interaction surfaces |
| Failure locality | vendor/RMW complexity集中在 robot-side Runtime deployment；caller只理解 public Runtime Interface |
| Testability | in-memory ports、Isaac/Nav2 Adapter、NXDog Adapter 共用同一 Authority contract |
| 成本 | 需部署、升級、auth、reconnect、watchdog與Runtime protocol compatibility |

**結論**：推薦 v0 產品方向。從 caller 觀點，Robot Runtime 是 remote-but-owned module；
在 Runtime implementation 內，Capability Executor 與 NXDog Adapter 是 co-located
internal seams，NXDog ROS 才是 true-external dependency。Public protocol只跨
caller→Authority，不跨Authority→Adapter。

## Authority and co-located Adapter responsibilities

### Robot Runtime Authority owns

- authenticated robot identity 與 protocol negotiation；
- one active command lease per robot/domain；
- monotonic safety epoch 與 stale approval/command invalidation；
- mutable Workflow Instance、deterministic sequencing與bounded retry；
- idempotency、server-owned timing budgets、command lifecycle 與 ordered event sequence；
- operator-readable approval preview 與 server-held request digest；
- Completion Contract evaluation、Task Outcome、audit、Receipt 與 Evidence retention；
- startup reconciliation、authority generation、orphan cleanup與availability admission；
- Capability Executor composition；
- interaction surfaces 的 single source of truth。

### Capability Executor and co-located NXDog Adapter own

- `NavigationGateway` 只處理 navigation／route／movement；
- `PlatformCommandPort` 處理 indicator／posture／charging non-navigation write；
- `ObservationPort` 取得 health／pose／velocity／map／battery Evidence；
- NXDog Adapter持有唯一robot-side `rclpy` node/executor與vendor goal handle；
- Foxy/custom interface dependency；
- authority generation／epoch／fencing token 的 local enforcement；
- action acceptance、feedback、cancel response、terminal result correlation；
- source timestamp/frame/covariance preservation；
- robot-side bounded watchdog 與 public Runtime disconnect behaviour；
- vendor error → Runtime stable taxonomy translation。

Capability Executor與Adapter不能自行發明Task Outcome；它們只提供typed Events與
Evidence，Completion Contract只由Robot Runtime Authority依Capability評估。

## Future remote Authority／Edge split

若未來因 fleet topology 必須讓 Authority 留在 DGX、Adapter 遠端部署，需另立 Edge
Control Protocol／ADR，明確處理 mutual authentication、authority generation、boot ID、
fencing continuity、prepare/execute/cancel/robot-wide STOP、remaining budget、Evidence
stream、network partition、takeover與restart reconciliation。它不是 Runtime v0，也不能
重用 public `submit`／`observe`／`stop` 讓 Edge 變成第二個 Task authority。

## Network and authentication baseline

Public caller→Authority transport 第一版只可：

1. Authority loopback bind + generated access token；或
2. robot LAN 上的 explicit secure deployment，使用 TLS、雙向 identity 或等價的
   short-lived credential。

此外必須：

- `StartCapability` request綁定`robot_id`、`idempotency_key`、
  `expected_safety_epoch`、`requested_timeout_ms`與request digest；`command_id`只由Runtime
  產生。`ResolveApproval`／`CancelCommand`只引用既有command，不得重置timing budget。
  Server依Capability policy clamp，並在execution開始時發布accepted execution budget與
  authoritative server deadline。
- HTTP/TLS Adapter建立`AuthenticatedPrincipal`；transport-bound client identity只能由
  credential／TLS導出。Payload的client ID／source surface永遠只是caller claims，不可
  用於authorization、audit actor、idempotency namespace或trusted classification。
- Runtime/Adapter 不接受 ambient proxy、redirect 或任意 vendor path。
- HTTP disconnect 不取消 command；cancel/stop 必須是明確 command。
- Runtime startup reconciliation完成前effectful command保持blocked，STOP仍可用。
- caller↔Runtime network partition時，robot-side Authority依vendor-approved watchdog
  policy處理；未取得正式policy前不得開啟motion。

## Recommended deployment sequence

1. Runtime Protocol v0 + in-memory backend。
2. 現有 Isaac/Nav2 vertical slice，證明 parity 與 single authority。
3. NXDog co-located Runtime deployment的read-only state/evidence。
4. LED command pipeline，套用
   [canonical Evidence-to-outcome policy](CAPABILITY_MAPPING.md#evidence-to-outcome-policy)。
5. stationary software-stop evidence。
6. compute-route。
7. short physical navigation。

這個順序讓第二個 Adapter 證明 Robot Runtime Seam 是真實 seam，而不是為 NXDog 臨時
建立第二條 execution path。
