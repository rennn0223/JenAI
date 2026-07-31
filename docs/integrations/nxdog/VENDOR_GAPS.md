# Questions and Contract Gaps for Nexuni

> 本清單供 vendor discussion 使用；不是對 Nexuni 產品品質的判定。

公開 developer kit 足以進行 read-only integration assessment，但不足以支持 JenAI
physical motion Completion Contract。下列問題取得書面答案、versioned interface 或可
重現 evidence 前，NXDog motion 維持 unavailable。

## 1. Version and compatibility

1. 如何查詢 robot firmware、nxnav、platform driver、HTTP example 與 interface version？
2. `nxnav_msgs` `0.1.0`、`nxdog_interfaces` `0.0.0` 分別相容哪些 firmware release？
3. interface 的 breaking-change 與 deprecation policy 是什麼？
4. Nexuni 是否支持第三方 Edge Adapter 在 Orin NX/Pi 5 執行？建議 deployment user、
   process supervisor、resource limit 與 update procedure 為何？
5. Foxy client、Jazzy client或跨 distro DDS 是否有正式 support matrix？

**需要的產物**：machine-readable version endpoint/topic、compatibility matrix、
upgrade/rollback contract。

## 2. License and reuse permission

1. repository root 沒有 `LICENSE`；reference Python code 可否修改、部署、再散布或商用？
2. `nxnav_msgs/package.xml` 標示 `Proprietary`。客戶可否：
   - 在 robot-side workspace build？
   - 將 generated artifacts 放入 container/image？
   - 將 interface definitions vendoring 到私有 integration repo？
3. `nxdog_interfaces` 標示 `Apache-2.0`，但 root license 缺失時適用範圍為何？
4. 是否允許依 public interface shape 自行實作 clean-room Adapter？
5. 對外展示、論文、測試 artifact 可揭露哪些 interface、error code、robot state？

**需要的產物**：書面 license/permission；在此之前 JenAI 不複製 vendor implementation。

## 3. ROS names, QoS and source time

1. 每個 topic/service/action 的正式 name、namespace、type、QoS 與 lifecycle availability？
2. `/nxnav/heartbeat` 的 Bool 值是否有語意，或任一 message 都代表 alive？
3. heartbeat period、允許 miss count 與 stale threshold？
4. `/nxnav/odom` 的 `header.stamp`、`frame_id`、`child_frame_id` 與 covariance 是否可信？
5. pose 是 localization estimate、fused odom 或其他 frame？
6. `/nxnav/current_map` 的 publish cadence、transient-local semantics 與 tile/group格式？
7. `NavStatus` 的正式 topic name、publisher 與 freshness contract？

**需要的產物**：versioned ROS graph contract 與 sample bag。

## 4. Navigation goal lifecycle

1. `NavigateToPose` goal rejection、acceptance、feedback、result 的完整 state machine？
2. `error_code` 每個值的名稱、retryability 與 operator action？
3. goal UUID 是否可安全用於外部 correlation？
4. `goal_tolerance_xy/yaw` 與 `nav_speed` 的有效範圍、default、clamp與單位？
5. action result success 是否已保證 final pose 在 tolerance 內？使用哪個 frame/body point？
6. feedback frequency、stale threshold與 recovery count semantics？
7. map group/tile transition 期間 goal 是否維持同一 UUID？
8. HTTP `/navigate` timeout 後，goal 是否繼續？正式 cleanup procedure為何？

**需要的產物**：action lifecycle spec、error taxonomy、safe parameter envelope。

## 5. Cancel and stop semantics

1. `cancel_goal_async()` response 中哪些 return code 表示 goal accepted cancellation？
2. cancel response 後，何時能保證 action terminal？
3. `/stop` 只取消目前 action，還是也清除 path/controller command？
4. 是否有平台級 software stop，可取消所有 owner/process 的 command？
5. zero velocity 要送哪個 priority topic、頻率與持續時間？
6. 如何觀察 controller 已套用 zero command？
7. velocity 接近零的官方 threshold、window與 source topic？
8. network/process crash 時 robot-side watchdog 行為？
9. software stop、E-stop與硬體安全 controller 的責任關係？

**需要的產物**：cancel acknowledgement contract、stop evidence contract、現場安全程序。

## 6. Raw velocity and avoidance

1. `/cmd_vel_low|mid|high` 的 arbitration、priority、timeout與 owner規則？
2. `vx/vy/wz` hard limits、acceleration limits、minimum publish rate？
3. 單次 publish 會保持多久？publisher消失後何時歸零？
4. vendor 是否提供 watchdog/lease token，而不是讓 client自行循環 publish？
5. `avoidance_enabled=false` 的合法使用者、允許場景與安全 interlock？

**需要的產物**：低階 controller contract。即使取得，這些介面仍不暴露給 JenAI Agent。

## 7. Map and localization identity

1. map group、tile、bundle、PRM graph、2D map之間的 version relation？
2. 是否提供 bundle digest、UUID、creation time或immutable revision？
3. `SwitchMap` success 後，何時 localization ready？
4. initial pose publish後，如何取得 convergence/covariance evidence？
5. map transform、tile boundary與跨圖 portal coordinate contract？
6. map update後，saved Site Profile coordinates如何失效？

**需要的產物**：content-bound map identity 與 localization readiness contract。

## 8. Platform commands and feedback

### LED/VUI

1. 是否有 device-originated color/brightness state topic或ack？
2. reference `vui_current_color/brightness` 是否刻意只是 command shadow？
3. invalid command、hardware offline與message drop如何回報？

在真實 feedback 出現前，套用
[canonical Evidence-to-outcome policy](CAPABILITY_MAPPING.md#evidence-to-outcome-policy)，
不得由 command shadow 推論硬體效果。

### Posture/sport

1. valid `SportCommand.identifier` 的 versioned allowlist？
2. service `success` 表示 accepted、started還是completed？
3. posture/action state、failure、timeout與safe interruption如何觀察？
4. 吊帶／空間／地面／電量等 prerequisite？

### Charging

1. `auto_charging_cmd` 與 `auto_charging_result` 如何做 command correlation？
2. result `"true"` 表示接近、接觸、開始充電或完成？
3. `BatteryState.current > 0` 的正負號、noise threshold與source timestamp？
4. 如何證明持續充電、功率與SOC增加？
5. stop charging 的 terminal acknowledgement？

## 9. Security and command ownership

1. Nexuni 是否有 production authentication/authorization transport？
2. 是否支持 mTLS、per-robot credential與credential rotation？
3. robot端是否已有 single command owner/lease/fencing token？
4. 同時有 security frontend、HTTP example與第三方 client時誰有優先權？
5. 如何撤銷 stale client、approval或goal？
6. vendor建議的 firewall/VLAN、port exposure與incident response？
7. 公開 connection guide 中的預設帳密是否要求首次啟動強制變更？

**需要的產物**：threat model、credential lifecycle與command arbitration contract。

## 10. Error and evidence taxonomy

1. topic/service/action/HTTP error是否有統一 taxonomy？
2. 哪些 failure retryable、operator-recoverable或必須 hard stop？
3. vendor evidence是否包含 source version、timestamp、sequence與robot identity？
4. log/bag/artifact中哪些欄位為敏感資料？
5. vendor建議的 acceptance scenario與成功門檻？

## Gate-to-phase mapping

| Phase | 必須先關閉的 gap |
|---|---|
| Read-only Edge Adapter | versions、ROS names/QoS、source time、license |
| LED command pipeline | auth/ownership、LED ack semantics；否則保持 unverified |
| Stationary stop | cancel/stop、velocity freshness、watchdog、現場安全 |
| Posture | SportCommand semantics、posture feedback、restrained procedure |
| Compute route | map identity、route error taxonomy |
| Short navigate | 全部 navigation lifecycle、map/localization、stop、parameter envelope |
| Charging | charging correlation、fresh battery evidence、stop acknowledgement |

## Source-observed gaps

具體 source evidence 與 pinned links 見
[`INTERFACE_INVENTORY`](INTERFACE_INVENTORY.md)。本文件提出的問題是 JenAI 的
integration requirement；未收到 vendor答覆前一律標為 unknown，而不是推測答案。
