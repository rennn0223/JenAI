# Isaac FullScan HIL 驗收（2026-07-19）

本紀錄保存 Isaac Sim／ROS2 Jazzy／Nav2 在乾淨 Git revision 上的正式 route、取消與
software halt 結果，也記錄 RTX LiDAR 從局部掃掠改為完整掃描的診斷依據。它是模擬
HIL 證據，不是實體載具、跨載具泛化、Twin 通訊隔離或功能安全認證。

## 固定環境與正式主證據

- JenAI `2.0.2`，revision
  `d942130a7b3a789ddfa5585b8554dea32588d855`，`source_dirty=false`
- Linux aarch64、Python 3.13.2、ROS domain 0
- 起點／Dock：`map (-6.0, -1.0, 3.14159)`；歷史場次使用的舊 Dock
  `(4.355, 3.236, -1.289)` 保留為歷史資料，兩者不可混用
- 正式 artifact：本機
  `artifacts/isaac-hil-live-final-d942130-20260719.json`
- SHA-256：
  `b5e0f4f9bd14474a128865f26f748a2de9feea4c3e2bda1a395c14ed099bd18b`

artifact 依專案規則留在本機且不進 Git；失敗或前導樣本亦不覆寫。

## RTX LiDAR 問題與修正

`/scan` 有穩定頻率不代表每筆訊息包含完整視野。修正前，每筆 PointCloud2 只有
6,724–7,604 點，約為旋轉中的 60° 局部掃掠。Helper 的 Full Scan 會讓每筆 PointCloud2
成為時間上完整的 360° 點雲；目前 `pointcloud_to_laserscan` 再裁成導航使用的前向 180°
`/scan`（`angle_min=-π/2`、`angle_max=π/2`、362 bins）。轉換器逐筆處理，不會替多筆
局部 wedge 累積視野；因此修正前 10 筆 `/scan` 中 3 筆全為 `+inf`，有限距離 bin 覆蓋率
只有 18.48%。這會讓 AMCL／Nav2 在「topic 還活著」的情況下仍收到稀疏或空白掃描。

在 Isaac Sim 的 ROS2 RTX LiDAR Helper 設定 `Publish Full Scan=True` 後，每筆點雲增加至
約 42,777–43,968 點；互動診斷窗為 12 筆 `/scan`、0 筆全空、有限 bin 覆蓋率 90.35%。
該互動數字是操作診斷，raw output 未另封存，不能取代正式 artifact。最終正式 HIL 的
觀測窗為 10/10 筆、每筆 362 bins、0 筆全空、2,065/3,620 個有效有限值（57.0442%）。
百分比會隨車輛朝向與場景幾何改變；判定使用預先固定門檻，不以單一高覆蓋率取代協議。

目前 scan gate 同時要求：10 筆樣本、每筆至少 180 bins、角寬至少 3.0 rad、正且有限的
angle increment、幾何誤差不超過 0.02 rad、合法 range bounds、有限值均在 bounds 內、
`0 < scan_time ≤ 1.0 s`、frame 非空且固定、timestamp 嚴格前進、全 `+inf` 樣本比例不高於
20%，以及 aggregate valid-finite coverage 至少 25%。本場次角寬 3.1416 rad、increment
0.0087 rad、最大幾何誤差 0.000900 rad、`scan_time=0.1 s`，NaN、`-inf`、malformed、
out-of-range 與不前進 timestamp 均為 0。

## 最終正式結果（clean `d942130`）

| 檢查 | 結果 | 可稽核觀測 |
|---|---|---|
| 必要 preflight | PASS | 第 1 次抽樣短暫缺 AMCL／cmd_vel subscriber，第 2 次重試後 ROS2 CLI、map、AMCL、laser、Nav2、cmd_vel 必要項全通過；起點 `(-5.945,-1.264)` 不在 `SW-narrow-aisle` |
| `/scan` 品質門檻 | PASS | 10/10 筆、362 bins/筆、0 筆全空、2,065/3,620 valid finite（57.0442%）；完整 metadata gate 全通過 |
| `map_left_down` route | PASS | `Arrived at the goal`；82.881 s；0 recoveries |
| `dock` route | PASS | `Arrived at the goal`；45.804 s；0 recoveries |
| cancel + software halt | PASS | local task 已取消；Nav2 cancellation acknowledged=`true`；halt 已送零速度；4.486 s；停止後位姿漂移 0.0000 m（門檻 0.05 m） |
| Twin isolation | SKIP | target 與設定的 Twin 都是 ROS domain 0，本場次不主張隔離或 Twin verdict |

整體結果為 `pass_with_skips`。這表示本場次必要的模擬 route／cancel／software halt 已通過，
而選配的 Twin 隔離證據明確缺席；不能把 `skip` 改寫成 `pass`。runner 結束後已停止，車
回到 Dock 後才進行 cancel goal，取消完成時留在 Dock 附近。

## Hero 10-leg 補充樣本（clean `cc6d217`）

- 初次 artifact `isaac-hil-hero10-current-gate-cc6d217-20260719.json` 在 scan gate 通過後，
  因 `/amcl_pose` 與 `/odom` 暫時皆無資料而 fail closed；live execution withheld，送出
  goal 數為 0。此失敗保留，SHA-256 `e4efad…bbb`。
- 恢復 preflight 為 `preflight_pass`，SHA-256 `ba6810…43e`。
- 第二次固定序列交替執行 `map_left_down` 與 `dock` 各 5 次，共 10 個 route legs；結果
  10/10 `succeeded`，單段 45.155–111.668 s，Nav2 recoveries 介於 0–4。完整 artifact
  `isaac-hil-hero10-current-gate-cc6d217-20260719-v2.json`，SHA-256 `91032d…0de`。
- 該序列是確定性 NavigationGateway／Nav2 壓力樣本，不是 10 次自然語言 LLM 試驗、
  10 次完整三分鐘 demo、無碰撞事件率研究或實體載具成功率。

## 自然語言單-goal 補充

在隔離測試 TUI 輸入「請回到 dock，並在抵達後回報結果。」，qwen3.6:35b 正確查到
Dock、產生批准卡並在一次批准後回報成功。Nav2 component log 的歷史 goal 計數由 18
增加為 19，只新增一個 `(-8.51,-7.66) → (-6.00,-1.00)` goal，終態為 succeeded；未見
invalid JSON，也未再出現同 domain Twin 造成的第二次立即成功 goal。

該操作是在 `cc6d217` 加未提交修正的 dirty worktree 執行，修正後提交為 `d942130`；因此
只作互動補充，不當 clean-revision 成功率。原始摘要保存在本機
`artifacts/tui-natural-language-single-goal-20260719.json`，SHA-256 `3f19b5…303`。

## 歷史 artifact 的定位

較早的 clean `fb56456…b1e` artifact 通過當時的兩項 scan 比例門檻、兩條 route，以及
local task cancel／zero-drift；但它早於 Nav2 cancellation acknowledgement 與完整 scan
metadata gate，不能用來宣稱現行 gate 已通過。它保留為歷史樣本，不覆寫、不刪除。

## 主張邊界

本紀錄支持：在所記錄的 Isaac Sim、地圖、點位與 commit 組合中，JenAI 能經 production
navigation path 完成 Nav2 高階 route；最終 runner 能在送 goal 前拒絕低品質 scan，並
區分「Python task 已取消」與「Nav2 已確認取消」。Hero 序列補充 10 個固定 route legs 的
完成結果；自然語言樣本補充一次有監督的解析—批准—執行—回授鏈。

本紀錄不支持：實體 Ackermann 車的路徑誤差、煞停距離或碰撞安全；虛實同時在線的通訊
隔離；不同運動學載具的物理泛化；未知地圖探索；自然語言成功率；事故率；或功能安全
認證。這些仍須各自的實體 PoC、分離 ROS domain 驗收、使用者研究或預先定義的重複實驗。
