# Isaac FullScan HIL 驗收（2026-07-19）

本紀錄保存 Isaac Sim／ROS2 Jazzy／Nav2 在乾淨 Git revision 上的正式 route、取消與
停止結果，也記錄 RTX LiDAR 從局部掃掠改為完整掃描的診斷依據。它是模擬 HIL 證據，
不是實體載具、跨載具泛化、Twin 通訊隔離或功能安全認證。

## 固定環境與證據

- JenAI `2.0.1`，revision
  `fb5645620c787bd54fc8368fe402366371561b1e`，`source_dirty=false`
- Linux aarch64、Python 3.13.2、ROS domain 0
- 起點／目前 Dock：`map (-6.0, -1.0, 3.14159)`；歷史場次使用的舊 Dock
  `(4.355, 3.236, -1.289)` 保留為歷史資料，兩者不可混用
- 正式 artifact：本機
  `artifacts/isaac-hil-live-fullscan-guard-fb56456-20260719.json`
- SHA-256：
  `51b3a7451273fd3e361d8f40c251ebdf1350d7c476c0fe913e2c7509af129d00`

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
正式 HIL 的另一個觀測窗為 10/10 筆可用、0 筆全空、有限 bin 覆蓋率 53.7569%。兩個
百分比來自不同時間窗與車輛朝向，不應互相取代；兩者都通過正式門檻（全空比例不高於
20%，有限 bin 覆蓋率至少 25%）。外部 Carter navigation workspace 的
`pointcloud_to_laserscan.scan_time` 同步校正為 `0.1 s`，對應完整掃描 10 Hz。

## 正式結果

| 檢查 | 結果 | 可稽核觀測 |
|---|---|---|
| 必要 preflight | PASS | ROS2 CLI、map、AMCL、laser、Nav2、cmd_vel 必要項全通過；起點不在 `SW-narrow-aisle` |
| `/scan` 品質門檻 | PASS | 10/10 筆、0 筆全空、3,620 bins 中 1,946 finite（53.7569%）；NaN、`-inf`、malformed 均為 0 |
| `map_left_down` route | PASS | `Arrived at the goal`；66.985 s；0 recoveries |
| `dock` route | PASS | `Arrived at the goal`；46.754 s；0 recoveries |
| cancel + software halt | PASS | 取消傳到執行端；halt 回報已送零速度；4.396 s；停止後位姿漂移 0.0000 m（門檻 0.05 m） |
| Twin isolation | SKIP | target 與設定的 Twin 都是 ROS domain 0，本場次不主張隔離或 Twin verdict |

整體結果為 `pass_with_skips`。這表示本場次必要的模擬 route／cancel／stop 已通過，而
選配的 Twin 隔離證據明確缺席；不能把 `skip` 改寫成 `pass`。

## 主張邊界

本場次支持：在所記錄的 Isaac Sim、地圖、點位與 commit 組合中，JenAI 能經 production
navigation path 完成兩條 Nav2 route，並把取消與零速度停止傳到模擬執行端；正式 runner
能在送 goal 前拒絕低品質雷射資料。

本場次不支持：實體 Ackermann 車的路徑誤差、煞停距離、碰撞安全；虛實同時在線的
通訊隔離；不同運動學載具的物理泛化；未知地圖探索；或長時間成功率。這些仍須各自的
實體 PoC、分離 ROS domain 驗收、使用者研究或多次固定 protocol 實驗。
