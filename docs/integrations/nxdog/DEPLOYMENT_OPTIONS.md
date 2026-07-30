# NXDog Deployment Options

> 推薦：Option C — authenticated Robot Runtime + NXDog Edge Adapter

本比較將「Robot Runtime Authority」與「NXDog Edge Adapter」視為不同 module：

- **Robot Runtime Authority**：JenAI 擁有的唯一命令與 evidence authority。
- **NXDog Edge Adapter**：在 robot-side ROS 2 環境中，把 platform-neutral command
  轉成 Nexuni interface，並把 vendor feedback 轉回 typed evidence。

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

## Option C：Robot Runtime Authority + NXDog Edge Adapter

```text
DGX Spark
┌──────────────────────────────────────┐
│ JenAI Application / Workflow         │
│ Robot Runtime Authority              │
│ command lease / safety epoch / event │
└──────────────────┬───────────────────┘
                   │ authenticated, versioned Runtime protocol
                   ▼
Robot-side Orin NX／LAN sidecar
┌──────────────────────────────────────┐
│ NXDog Edge Adapter                   │
│ local fencing / watchdog / evidence  │
└──────────────────┬───────────────────┘
                   │ local ROS 2 Foxy
                   ▼
             nxnav / platform driver
```

| 面向 | 評估 |
|---|---|
| ROS distro | Foxy/custom interfaces 留在 robot-side deployment |
| Authentication | Runtime protocol 可要求 token/TLS、robot identity、scope |
| Command ownership | Authority 擁有全域 lease；Edge 以 fencing token 拒絕 stale command |
| Cancellation | Edge 保留 goal handle、等待 cancel response、回傳 typed event |
| Evidence | Edge 保留 ROS source time/frame/covariance/feedback，再包成 `EvidenceEnvelope` |
| Network exposure | 不把 DDS graph 或 vendor raw endpoint暴露給 interaction surfaces |
| Failure locality | vendor/RMW complexity集中在 Adapter；JenAI caller只理解 Runtime interface |
| Testability | in-memory backend、Isaac/Nav2 Adapter、NXDog Adapter 共用同一 interface |
| 成本 | 需部署、升級、auth、reconnect、watchdog、protocol compatibility |

**結論**：推薦產品方向。這是 ports-and-adapters seam：Runtime 是 remote-but-owned
module，NXDog ROS 是 true-external dependency；Edge Adapter 將 vendor complexity
局部化。

## Authority and Edge responsibilities

### Robot Runtime Authority owns

- authenticated robot identity 與 protocol negotiation；
- one active command lease per robot/domain；
- monotonic safety epoch 與 stale approval/command invalidation；
- idempotency、deadline、command lifecycle 與 ordered event sequence；
- operator-readable approval preview 與 server-held request digest；
- task outcome、audit、receipt 與 evidence retention；
- interaction surfaces 的 single source of truth。

### NXDog Edge Adapter owns

- 唯一 robot-side `rclpy` node/executor 與 vendor goal handle；
- Foxy/custom interface dependency；
- runtime lease/fencing token validation 的 local enforcement；
- action acceptance、feedback、cancel response、terminal result correlation；
- source timestamp/frame/covariance preservation；
- robot-side bounded watchdog 與 network-loss safe behaviour；
- vendor error → Runtime stable taxonomy translation。

Edge Adapter 不能自行發明 Task Outcome；它提供 evidence，Completion Contract 由
Runtime/Application 依 Capability 評估。

## Network and authentication baseline

第一版只可：

1. Authority loopback bind + generated access token；或
2. robot LAN 上的 explicit secure deployment，使用 TLS、雙向 identity 或等價的
   short-lived credential。

此外必須：

- 每個 request 綁定 `robot_id`、`command_id`、`idempotency_key`、
  `expected_safety_epoch`、deadline 與 request digest。
- Edge 不接受 ambient proxy、redirect 或任意 vendor path。
- HTTP disconnect 不取消 command；cancel/stop 必須是明確 command。
- network partition 時 Edge 依 vendor-approved watchdog policy fail safe；未取得正式
  policy 前不得開啟 motion。

## Recommended deployment sequence

1. Runtime Protocol v0 + in-memory backend。
2. 現有 Isaac/Nav2 vertical slice，證明 parity 與 single authority。
3. NXDog Edge Adapter read-only state/evidence。
4. LED command pipeline，套用
   [canonical Evidence-to-outcome policy](CAPABILITY_MAPPING.md#evidence-to-outcome-policy)。
5. stationary software-stop evidence。
6. compute-route。
7. short physical navigation。

這個順序讓第二個 Adapter 證明 Robot Runtime Seam 是真實 seam，而不是為 NXDog 臨時
建立第二條 execution path。
