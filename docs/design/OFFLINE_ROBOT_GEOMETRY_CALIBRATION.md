# Offline Robot Geometry Calibration — Design Brief

- Status: Proposed; documentation only
- Date: 2026-08-04
- Scope: Isaac robot geometry calibration, product startup checks, and simulation acceptance
- Supersedes operationally: the pending GUI Script Editor Stage-export request
- Does not supersede yet: ADR 0008; that ADR requires an explicit amendment before implementation

## Decision summary

JenAI 將產品執行、載具幾何校準與驗收拆成三條獨立流程：

1. Product Runtime 繼續使用既有 `NavigationGateway → ROS 2/Nav2 → Isaac` 路徑；
2. Offline Calibration 在全新 Headless Isaac process 中低頻產生版本化
   `RobotGeometryAttestation`；
3. Acceptance 只透過 ROS 2 取得 path、costmap、live footprint、pose、result 與 STOP
   Evidence，並驗證現行 identity 已有匹配的 geometry attestation。

Product Runtime 不依賴 Script Editor、Isaac extension、Stage exporter、任意 Python
execution server 或 Headless ROS/Nav2 parity。正常任務前也不重新掃描 USD Stage。

這個方向會改變 ADR 0008 對「每次 motion readiness 都取得 active Stage geometry」的
要求。實作前必須修訂 ADR 0008，明確區分 Development、Acceptance 與 Certification
Research；本 Brief 本身不改變現行 safety policy。

## Problem statement

原始產品問題是：JenAI 能否透過載具既有導航堆疊可靠完成高階任務、停止並誠實回報結果。
USD collision geometry 是低頻的 robot-profile 校準輸入，不是每次導航的 runtime dependency。

目前流程把 active GUI Stage extraction 放入每場 Motion Readiness capture，因而引入 sealed
ZIP bundle、Script Editor bootstrap、長駐 import cache 與 GUI/Headless parity。這些機制改善了
嚴格實驗 Evidence，卻讓輔助驗證工具成為產品主線的必要條件。

## Target flows

### 1. Product Runtime

```text
Operator
  → JenAI interaction surface
  → Capability / Workflow
  → Robot Runtime Authority（完成後）
  → NavigationGateway
  → ROS 2 / Nav2
  → Isaac or physical robot
```

這條路徑保留現有 motion authority、approval、STOP、completion 與 Evidence 規則。
`NavigationGateway` 不讀 USD、不載入 calibration tool，也不因 Headless parity 未完成而改變。

### 2. Offline Robot Calibration

```text
Exact USD + dependency manifest + robot profile footprint
  → fresh Headless Isaac process
  → composed collision geometry extraction
  → projected base-frame collision hull
  → containment comparison
  → RobotGeometryAttestation
  → review / version control
```

此流程只在新 robot、新 USD、collision geometry／scale、footprint 或 calibration tool contract
改變時執行。它不啟動 ROS Bridge、Nav2 或 timeline playback，也不發 goal、`cmd_vel` 或
initial pose。

### 3. Acceptance

```text
ROS 2 plan / costmap / live footprint / pose / result / STOP Evidence
  + matching RobotGeometryAttestation
  → route readiness
  → separately approved motion
  → verified task outcome
```

Acceptance 不解析 USD。它只驗證 attestation 與目前 robot profile、scene launch manifest 及
live effective Nav2 footprint 相符。

## Module and seam design

### Offline calibration module

外部 interface 應維持單一操作：

```text
calibrate(CalibrationRequest) → RobotGeometryAttestation
```

`CalibrationRequest` 包含 exact USD、完整 dependency manifest、robot root prim、base frame、
canonical Nav2 footprint 與 tool identity。模組內部隱藏 Stage loading、instance traversal、
transform／scale handling、2D hull projection、containment 與 digest 計算。

第一版只有 Headless Isaac implementation，不先建立通用 extractor seam；只有第二個真正
不同的 implementation 出現時，才抽出 Adapter interface。

### Runtime attestation validator

Product startup 與 Acceptance 共用一個小 interface：

```text
verify_geometry_attestation(
  robot_profile,
  scene_launch_manifest,
  live_nav2_footprint,
  attestation,
) → GeometryReadiness
```

它只做 identity、digest、expiry/policy 與 containment-result 驗證，不啟動 Isaac 或修改
robot state。`GeometryReadiness` 至少區分 `pass`、`blocked` 與 `unavailable`，並列出精確
mismatch。

## Minimal Headless calibration flow

1. 建立全新的 Isaac Lab／Kit Headless process。
2. 驗證 root USD path、SHA-256 與 dependency manifest。
3. 載入 Stage，等待 composition 完成，但不 Play timeline。
4. 在指定 robot root 下列舉 collision-enabled prim。
5. 取得 collision shape、local vertices、composed transform、scale 與 base-frame projection。
6. 對不支援的 animated transform、runtime-generated collider、unresolved asset、non-finite
   geometry 或無法保守投影的 shape 回報 `BLOCK`，不得猜測。
7. 建立 canonical 2D collision hull。
8. 讀取 `CalibrationRequest` 中 robot profile 的 canonical Nav2 footprint；校準工具不需連接
   live Nav2。
9. 計算 containment、minimum containment margin、inward/outward deviation 與 digests。
10. create-once 寫出 attestation，關閉 Kit，確認沒有 orphan process。

這個流程不要求 ROS 2、`/clock`、TF、sensor topic 或 Nav2 configure parity。

## `RobotGeometryAttestation` schema

Conceptual v1 schema：

```json
{
  "schema_version": 1,
  "attestation_id": "sha256:<canonical-content>",
  "created_at": "RFC3339 timestamp",
  "robot_profile": {
    "profile_id": "nova_carter",
    "profile_version": "versioned value"
  },
  "calibration_tool": {
    "git_sha": "exact reviewed commit",
    "contract_version": 1
  },
  "provenance": {
    "reviewed_source": "repository + commit",
    "signature_bundle": null
  },
  "source_geometry": {
    "root_usd_path": "repository/profile-relative identity",
    "root_usd_sha256": "...",
    "dependency_manifest_sha256": "...",
    "dependencies": [
      {"asset_id": "...", "sha256": "..."}
    ],
    "robot_root_prim": "/World/...",
    "base_frame": "base_link",
    "meters_per_unit": 1.0
  },
  "collision_geometry": {
    "collision_prim_inventory_sha256": "...",
    "projected_hull_vertices_m": [
      [-0.45, -0.30],
      [0.45, -0.30],
      [0.45, 0.30],
      [-0.45, 0.30]
    ],
    "projected_hull_sha256": "..."
  },
  "nav2_footprint": {
    "frame": "base_link",
    "configured_vertices_m": [
      [-0.50, -0.35],
      [0.50, -0.35],
      [0.50, 0.35],
      [-0.50, 0.35]
    ],
    "footprint_padding_m": 0.0,
    "effective_footprint_sha256": "..."
  },
  "comparison": {
    "result": "PASS",
    "minimum_containment_margin_m": 0.04,
    "maximum_inward_deviation_m": 0.0,
    "maximum_outward_deviation_m": 0.04
  },
  "limitations": [],
  "content_sha256": "..."
}
```

`root_usd_sha256` 單獨不足以識別 composed geometry；被引用的 robot asset 改變時，root
layer 可能不變。因此 v1 必須同時綁定 dependency manifest。無法完整列舉或固定 remote
dependency 時，attestation 為 `BLOCK`。

`content_sha256` 提供內容完整性，不等於來源簽章。v1 可由 Git review provenance 建立信任；
若未來發布 detached signature／Sigstore bundle，必須放在獨立 provenance 欄位，不得改變
幾何判定語意。

`PASS` 只表示該 footprint 在此校準 contract 下包覆該 collision hull；它不證明導航安全、
active GUI Stage identity、控制器 tracking、零碰撞或實體載具安全。

## Binding and startup verification

Robot profile 保存 approved `attestation_id`，並固定：

- robot profile identity/version；
- root USD 與 dependency manifest digest；
- effective Nav2 footprint digest；
- calibration contract/tool version。

Product startup 的 `doctor` 流程：

1. 載入 robot profile 指定的 attestation；
2. 驗證 attestation canonical digest 與 review/publish provenance；
3. 驗證 configured scene launch manifest 與 USD/dependency identities；
4. 從 live Nav2 parameter interface 取得 effective footprint 並重算 digest；
5. 要求 comparison result 為 `PASS`；
6. 任一 mismatch 回報 `GeometryReadiness.blocked`，列出重新校準指引。

純 ROS 2 無法獨立證明 GUI 實際載入的 active Stage。Development 可明確回報
`configured_scene_attested`；正式 Acceptance 則必須由受控 launcher 在啟動前固定 exact
scene launch manifest，並保存 launch evidence。沒有這項 evidence 時不得宣稱
`active_scene_attested`，但也不重新引入 Script Editor。

## Test levels

| Level | Purpose | Required | Optional / limitation-preserving |
|---|---|---|---|
| Development | 日常功能與短路徑開發 | ROS readiness、matching geometry attestation、plan/clearance、localization/TF/costmap health、STOP/cancel | collision topic、tracking history、ground truth |
| Acceptance | 合併／發版與固定任務 | Development requirements、controlled scene launch manifest、repeated task results、artifacts、cleanup | collision timeline 若 available；缺少時不得宣稱 collision-free |
| Certification Research | 論文或高保證研究 | ADR 0008 類型的 raw geometry、full collision timeline、source/clock attestation、完整 uncertainty budget、防篡改與 ground truth contracts | 無；缺 Evidence 即 BLOCK |

Simulation Evidence 在所有 level 都不得外推為實體安全。

## Motion Safety Gate simplification

每次 Development／Acceptance motion admission 保留四項必要條件：

1. matching `RobotGeometryAttestation` 有效；
2. exact plan 的 oriented-footprint conservative clearance 高於該測試 level 的明確 policy；
3. localization、TF、costmap 與 runtime health 有效；
4. STOP／cancel path 可用且符合既有 acceptance contract。

以下降為 optional confidence Evidence，缺少時保留 limitation，不自動宣稱成功：

- collision topic／contact details；
- controller tracking history；
- simulation world ground truth；
- 每場 active Stage raw geometry；
- Certification Research 等級的完整七項 uncertainty reconstruction。

Optional 不代表可捏造為 PASS。若某個 route policy 明確要求該 Evidence，缺少時仍應
`BLOCK`；一般受控模擬測試則可在保守 footprint、clearance、速度與 STOP contract 下執行，
並誠實標記 collision Evidence unavailable。

## GUI Stage exporter disposition

目前尚未執行的 GUI Stage export 標記為：

```text
superseded_by_offline_calibration_workflow
```

不再要求操作員執行桌面 bootstrap。PR #150／#151 修正的 lazy import 與 sealed bundle cache
問題保留為真實工程修正，但 Script Editor 流程不再是主線。

完成並驗收 Headless calibration tool 後，另開 migration PR：

- 從主要操作文件移除 Script Editor 步驟；
- deprecate `prepare-stage-export` 與 GUI bootstrap；
- 保留一個 release 的只讀 fallback（若仍有使用者）；
- fallback 期結束後刪除 exporter implementation、相關 CLI 與只服務該流程的 bundle code；
- Git history 保留原始研究與修正紀錄。

## Migration sequence

1. 接受本 Brief，修訂 ADR 0008 的 level 與 attestation reuse policy。
2. 實作最小 Headless geometry calibration module 與 create-once CLI。
3. 以 exact Nova Carter USD/profile 產生、review 並版本化第一份 attestation。
4. 將 attestation ID 綁入 Nova Carter robot profile。
5. 在 `doctor`／startup readiness 加入 digest 與 live footprint 驗證；不改
   `NavigationGateway`。
6. 將 Motion Safety admission 收斂為四項必要條件，研究級 Evidence 移至
   Certification Research profile。
7. 使用既有 GUI Isaac＋ROS 2＋Nav2 執行 plan-only route selection，再以獨立批准執行一條
   短距離、高 clearance smoke route。
8. 完成一個 Inspection Mission vertical slice。
9. 待替代流程穩定後移除 GUI Stage exporter 主流程。
10. Headless ROS Bridge／Nav2 parity 保留獨立 backlog，不阻擋 GUI product development。

## Non-goals

- 不建立 Isaac extension；
- 不建立任意 Python execution server；
- 不修改 NavigationGateway 或 product motion path；
- 不在每個 task 前掃描 USD；
- 不要求 operator 使用 Script Editor；
- 不以 Headless ROS/Nav2 parity 作為恢復 GUI product development 的前置條件；
- 不在本 Brief 執行 motion 或宣稱 Nova Carter attestation 已存在。

## Acceptance criteria for the future implementation

- Headless calibration 在 fresh process 中只讀 exact USD，無 ROS/Nav2/timeline/motion dependency；
- 相同 inputs 產生 canonical-equivalent attestation；
- USD dependency、collision geometry、scale、robot profile 或 footprint任一改變會使舊
  attestation mismatch；
- unsupported geometry 或 incomplete dependency closure 產生可信 `BLOCK`；
- startup validator 能從 live Nav2 footprint 偵測 undersizing／drift；
- Product Runtime 與 NavigationGateway 的 dependency graph 不新增 Isaac calibration import；
- Acceptance 在沒有 Script Editor 或 extension 的情況下可使用既有 attestation；
- 文件不把 attestation PASS宣稱為 route safety、collision-free 或 physical safety。
