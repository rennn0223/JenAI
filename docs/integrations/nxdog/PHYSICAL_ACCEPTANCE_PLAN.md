# NXDog Physical Acceptance Plan

> 狀態：Proposed／本 PR 不執行真機動作
>
> 本計畫是分階段的 engineering acceptance，不是實體安全認證，也不表示 JenAI
> 已正式支援 NXDog motion。

## Purpose

本計畫驗證的不是「HTTP 回 200」或「機器狗看起來有動」，而是：

```text
operator intent
→ Intent Layer selects typed Capability
→ Robot Runtime Authority
→ Runtime-owned Approval／Task lifecycle／Completion Contract
→ Workflow Instance（僅 Workflow Capability）
→ Capability Executor
→ NavigationGateway／PlatformCommandPort／ObservationPort
→ co-located NXDog Adapter
→ vendor interface
→ fresh evidence
→ Authority-owned honest Task Outcome／Receipt
```

每一階段只在上一階段的 prerequisite、evidence 與 cleanup 均通過後開放。失敗場次與
限制也必須保存，不得只保留成功展示。

## Global safety and authority gates

所有可能改變實體狀態的場次開始前，必須同時滿足：

1. 經 Nexuni 確認該 robot／firmware／interface 組合與允許的測試程序。
2. 測試區域清空，地面與淨空符合廠商要求；姿態測試另有吊帶或防倒措施。
3. 現場操作員持有可立即觸發的實體 E-stop，並知道其復歸程序。
4. 只啟動一個co-located NXDog Runtime deployment；Authority、Capability Executor與
   NXDog Adapter都位於同一robot-side companion／LAN sidecar。
5. TUI、WebUI、MCP 與其他 client 只能呼叫同一 Runtime；不得另連 port `5088` 或
   NXDog ROS 2 motion interface。
6. runtime、Adapter、robot、map、site、AuthenticatedPrincipal與command owner identity
   可確認；caller claim不得取代principal。
7. authority generation、boot ID、durable safety epoch、command lease與local execution
   fence均為current，startup reconciliation已完成。
8. network、heartbeat、pose、velocity 與 required interface freshness 通過。
9. Reconciliation已證明沒有unknown active vendor goal、未完成Approval或上一場殘留
   command；effectful admission狀態為available。
10. 現場操作員已依vendor程序演練實體E-stop與安全復歸。Runtime software STOP的正式
    acceptance屬Phase 2；Phase 1前的外部演練不表示Phase 2已通過。

任一 gate 失敗即停止該場，保存 evidence；不得在同一場臨時調參、重啟或繞過 Runtime
把結果變成 PASS。

## Startup and restart reconciliation gate

每次Runtime／Adapter restart、credential rotation、robot reconnect或不確定上一場cleanup
狀態後，必須先執行
[canonical startup reconciliation](ROBOT_RUNTIME_PROTOCOL_DRAFT.md#startup-reconciliation-and-authority-continuity)：

```text
acquire single deployment ownership
→ durable authority generation + safety epoch advance
→ invalidate old Approval／lease
→ observe active vendor goal／velocity／robot state
→ bounded cancel／STOP unknown prior work
→ reconcile old Task／Event／Receipt
→ Runtime available
```

完成前Runtime Health只能是`read_only`、`degraded`或`unavailable`，effectful Task保持
`blocked`；read-only observation與STOP仍可使用。Unknown prior work不得auto-resume，
reconciliation失敗不得靠重新送同一command來取得PASS。

## Required artifact bundle

每場產生 immutable run directory，至少包含：

```text
manifest.json
runtime-descriptor.json
robot-state-before.json
command-request.redacted.json
approval.json
events.jsonl
vendor-evidence.jsonl
robot-state-after.json
stop-evidence.json
task-receipt.json
cleanup.json
operator-observation.json
```

`manifest.json` 至少記錄：

- JenAI、Runtime、co-located Adapter 與 vendor interface revision；
- robot ID、firmware／API version；unknown 時不得開始 motion phase；
- test case、operator、site、map identity、時間與網路拓撲；
- capability/input schema version、command ID、idempotency key；
- runtime ID、boot ID、authority generation、safety epoch、lease／fencing token的不可逆
  識別與reconciliation result，不保存credential；
- authenticated principal ID／credential reference與separate caller claims；
- requested timeout、accepted execution／postcondition Evidence／cleanup budgets、approval
  expiry，以及execution／postcondition／cleanup server deadlines；
- source／receive timestamps、event sequence、content digest、transport security、source
  assurance／attestation與evidence limitations；
- reset／reposition／cleanup policy；
- 所有 config、map、Site Profile與 test definition digest；
- 實體 E-stop holder 與 observer；不保存個人秘密資料。

原始 secret、bearer token、Wi-Fi 密碼與 server-held action digest不得出現在 artifact。

## Outcome vocabulary

各階段只能使用符合 evidence 強度的 outcome：

| Outcome | 意義 |
|---|---|
| `succeeded` | 該階段的 Completion Contract 已由指定 evidence 驗證 |
| `arrived_unverified` | approach pose有證據，但最終物理效果不可驗證 |
| `endpoint_mismatch` | execution 已終止，但 fresh endpoint Evidence 超出該 Capability 的必要 tolerance |
| `partial` | 部分必要步驟有證據，其餘未完成 |
| `blocked` | prerequisite、policy、approval或vendor contract 阻止執行 |
| `unavailable` | required interface／feedback／identity 不可用 |
| `failed` | command、verification或cleanup失敗 |
| `cancelled` | current command 被 operator／Runtime 取消且終態已記錄 |

所有 phase 都套用
[canonical Evidence-to-outcome policy](CAPABILITY_MAPPING.md#evidence-to-outcome-policy)；
沒有 required fresh Evidence 時不得使用 `succeeded`。

## Phase 0 — Read-only baseline

### Purpose

將現有 NXDog read-only observer 投影成 `RuntimeState`／`EvidenceEnvelope`，不送出任何
write、service或action。

### Observe

- heartbeat receive freshness；
- ready flag，但不得等同 navigation ready；
- current map group/tile；
- odom pose／velocity；
- battery／charging indication；
- Runtime與Edge health。

### Pass contract

- robot、Runtime與co-located Adapter identity一致；
- values能被 typed schema解析，非有限數值與 malformed payload fail closed；
- source timestamp存在時保留；不存在時明確標示`freshness=unknown`；content digest、
  transport security與source assurance各自保存，不能互相替代；
- disconnect、stale heartbeat與missing field均投影為 degraded/unavailable；
- artifact可重播產生相同 presentation，不包含 secret。

### Stop condition

identity、version、frame、QoS或freshness contract不明確時，motion phases保持
`blocked`；read-only assessment仍可繼續。

## Phase 1 — Indicator command pipeline

### Purpose

以低物理風險 command 驗證 authentication、approval、idempotency、lease、event、
receipt與retry。不把它當成硬體 read-back驗證。

### Procedure

```text
typed set_indicator request
→ operator-readable approval
→ exact server-side action binding
→ Runtime command lease
→ Capability Executor
→ PlatformCommandPort
→ co-located NXDog Adapter
→ vendor VUI command
→ command shadow observation
→ Task Receipt
```

### Pass contract

- duplicate idempotency key不重複送出 vendor command；
- stale authority generation／safety epoch／fencing token被拒絕；
- authorization、audit actor與idempotency namespace來自AuthenticatedPrincipal，不採信
  caller自稱的client ID／source surface；
- accepted execution budget由Runtime clamp；Approval expiry不消耗execution budget；
- approval內容顯示顏色／亮度，且不洩漏 transport；
- vendor publish/request有 evidence；
- process-local shadow與requested value一致；
- 依 canonical policy 產生 Evidence、Task Outcome 與 limitation。

`GET /color` 與 `GET /brightness` 是 reference process 的 local shadow，不是
device-originated feedback。只有 Nexuni提供可信的硬體狀態或 acknowledgement，才可新增
另一條能得到 `succeeded` 的 Completion Contract。

## Phase 2 — Stop while stationary

### Purpose

在沒有 active navigation且機器狗原本靜止時，驗證 STOP是 provider-free、免批准、
優先且會使舊 command失效。

### Required evidence

```text
stop request accepted
safety epoch advanced
pending approvals invalidated
leases/queued effectful commands revoked
cancel requested (若有 handle)
cancel acknowledged = true | false | unknown
zero command published = true | false | unknown
fresh velocity window
physical stop = operator-observed | unverified
```

### Pass contract

- STOP不等待一般 command queue或模型；
- STOP前建立的delayed work／callback被co-located execution fence拒絕；
- fresh velocity持續低於 vendor確認的門檻與時間窗；
- software evidence與人工 physical observation分開保存；
- `/stop` HTTP success本身不構成 PASS。

若 vendor尚未提供 cancel acknowledgement、zero-command與velocity freshness contract，
本階段只能回報各證據欄位，整體physical stop仍是`unavailable`或`failed`，不得以
command acceptance取代停止驗證。

## Phase 3 — Posture under restraint

### Prerequisites

- Nexuni提供 versioned `SportCommand` allowlist、prerequisite與safe interruption程序；
- 機器狗依廠商要求架高、吊帶固定或位於合格安全區；
- 現場操作員持有實體 E-stop；
- Phase 2通過。

### Scope

只選廠商核准的低風險姿態，例如 stand-up／stand-down；不測跳躍、快速動作或任意
identifier。

### Evidence and outcome

- service request／response與command correlation；
- vendor posture state若可用；
- operator observation、影片或安全檢查表（依隱私政策）；
- timeout、cancel與cleanup。

只有 service-level acceptance 時，依 canonical policy 產生 Evidence、Task Outcome 與
limitation。人工觀察可作 acceptance evidence，但不能冒充 vendor telemetry 或安全認證。

## Phase 4 — Compute route

### Purpose

先驗證 non-motion route contract，再開 navigation。

### Prerequisites

- content-bound map identity或明確標記的受控替代證據；
- route/action error taxonomy與timeout contract；
- start/goal frame、map/tile與tolerance語意已由vendor確認。

### Pass contract

- typed Site Profile location被轉成唯一 canonical goal；
- Runtime command ID與vendor action goal/result可關聯；
- path、map identity、planning time與error保存；
- timeout不留下未知狀態的active action；
- path存在只證明route可計算，不宣稱motion ready。

Execution path必須是`Robot Runtime Authority → Capability Executor → NavigationGateway →
co-located NXDog navigation Adapter`；不得由PlatformCommandPort或caller直接送action。

## Phase 5 — Short navigation

### Prerequisites

- Phases 0–4通過；
- vendor navigation lifecycle、goal UUID、feedback、cancel、parameter envelope已確認；
- software STOP與實體 E-stop程序已演練；
- localization/map identity、fresh pose與velocity evidence可用；
- first target位於同一張map、短距離、開放且無階梯／人員／障礙的受控區域。

### Procedure

```text
start gate
→ typed navigate
→ approval
→ lease/fencing
→ Capability Executor
→ NavigationGateway
→ co-located NXDog navigation Adapter
→ vendor goal accepted
→ correlated progress events
→ terminal result
→ final pose + velocity window
→ Completion Contract
→ cleanup
```

不使用LLM產生座標，不啟用巡邏、充電、多地圖或自動retry。

### Pass contract

- canonical request、approval與vendor goal等價；
- goal acceptance、UUID、feedback與terminal result可關聯；
- final pose使用vendor確認的frame/body point/tolerance；terminal result成功但fresh endpoint
  Evidence超出該tolerance時，Task Outcome必須是`endpoint_mismatch`，不得宣稱
  `succeeded`；
- fresh velocity window確認robot stationary；
- Task Outcome由evidence判定，不由action success單獨決定；
- cleanup後沒有active goal、lease或舊approval。

第一輪只做pilot，不計入產品成功率。Harness與artifact經review後，再用預先固定的起點、
目標與順序執行多次candidate/baseline run，成功與失敗場次全部保留。

## Phase 6 — Navigation STOP

### Purpose

在受控短距離navigation中驗證robot-wide STOP與late-result linearization。

### Procedure

```text
goal active
→ operator STOP
→ safety epoch advances
→ approval/lease invalidated
→ cancel requested
→ cancel acknowledgement
→ goal terminal
→ zero command/evidence
→ fresh stationary window
→ old success arrives late (如發生)
```

### Pass contract

- STOP具有獨立stop ID與receipt；
- Runtime internal watchdog／shutdown reason不得由external caller claim冒充；
-原navigation只有一個terminal outcome；
-晚到success只能成為audit evidence，不能覆寫`cancelled`；
-所有interaction surfaces看到相同command、stop與outcome；
-軟體證據沒有被描述成physical E-stop或formal safety guarantee。

cancel acknowledgement或fresh stationary evidence缺失即不得宣稱stop完成。

## Phase 7 — Charging

### Prerequisites

- short navigation與navigation STOP已穩定；
- Nexuni提供charging command/result correlation與state semantics；
- charger、接近姿態、對接程序與現場安全條件已確認；
- fresh battery voltage/current/SOC與source timestamp可用。

### Pass contract

`auto_charge`的`succeeded`至少需要：

```text
correlated charging command terminal
AND fresh charging-state evidence
AND sustained current/power window
AND no active navigation
AND cleanup/stop capability remains available
```

只到達充電站時最多是`arrived_unverified`；只收到未關聯字串`"true"`時最多是
`partial`並附上`charging_effect_unverified` limitation。不得用Isaac Dock Approach
evidence支持實體充電成功。

## Abort and cleanup contract

下列任一情況立即中止，不嘗試在同一場修復後續跑：

- Runtime／Adapter／robot identity或authority generation改變；
- startup reconciliation未完成、失敗或重新進入running；
- safety epoch、lease或local execution fence不一致；
- heartbeat、clock、pose、velocity或map evidence stale/unknown；
-出現第二個motion owner、goal或未授權client；
-approval/action digest mismatch；
-vendor error taxonomy未知且收到非成功結果；
-cancel、STOP或cleanup無法證明終態；
-robot行為與預期不一致、跌倒風險、異音、打滑或進入禁區；
-實體 E-stop holder要求停止。

cleanup必須嘗試provider-free STOP、撤銷lease/Approval，並在server-owned cleanup window
內等待相符的terminal／stationary Evidence、保存artifact並關閉Adapter handle。這些
Evidence只能支撐cleanup或cancel outcome，不能把已timeout Task翻成`succeeded`。若cleanup
未確認，場次標為`failed`，後續motion phase保持blocked，直到操作員與vendor完成安全復歸。

## Release claim boundary

各階段通過後只能宣稱該階段、該robot、該firmware、該site與該revision的engineering
evidence。不得外推為：

-所有NXDog型號或firmware相容；
-實體安全認證；
-跨載具navigation精度；
-network partition下必然安全；
-hardware E-stop等價；
-正式production SDK支援。

正式支援需另有accepted motion ADR、vendor contract、repeatable physical evidence、
rollback/runbook與release-specific support matrix。
