# ISAAC_NAV2_SETUP — Isaac Sim 佔位圖 + Nav2 試跑完整流程

> 目的:在 Isaac Sim 生成 **occupancy map(佔位圖)**、跑起 **Nav2**,
> 讓 `/route` 走真正的全域路徑規劃 + costmap 避障(取代 ground plane 的
> odom 直驅)。依官方文件整理(2026-07,Isaac Sim latest);來源列文末。
> 本機驗證組合:**Ubuntu 24.04 + ROS2 Jazzy — 官方推薦組合**,直接可用。
>
> 在整條 twin 建置路上,本文件是**階段 1(路線 A)與階段 2(路線 B)**;
> 四階段總覽與後續的孿生側/Gate 設定見 [TWIN_SETUP](TWIN_SETUP.md) §0。

---

## 0. 前置確認(一次性)

1. **先 source ROS 再開 Isaac Sim**(bridge 才會用你系統的 Jazzy 而非內建庫):
   ```bash
   source /opt/ros/jazzy/setup.bash
   ./isaac-sim.sh   # 或你的啟動方式
   ```
2. Isaac Sim 內 **Window → Extensions** 確認 `isaacsim.ros2.bridge` 已啟用。
3. 安裝 Nav2(工作站):
   ```bash
   sudo apt install ros-jazzy-navigation2 ros-jazzy-nav2-bringup
   ```
4. (走路線 A 才需要)建 ROS2 workspace 放官方導航包:
   `carter_navigation`、`isaac_ros_navigation_goal`(在 NVIDIA 的
   IsaacSim-ros_workspaces repo,checkout 對應版本後 `colcon build`)。

> ⚠️ 多 domain 提醒:如果之後要同時跑 Twin Gate(孿生 domain 42)與
> 這個 Nav2 場景,記得它們的 `ROS_DOMAIN_ID` 要一致於你想連的那側。

---

## 路線 A|官方 Warehouse 範例(最快驗證整條工具鏈,建議先跑這條)

**目標**:用 NVIDIA 現成的 Nova Carter 倉庫場景,30 分鐘內看到 Nav2 避障。

1. **開場景**:Isaac Sim 選單 **Window → Examples → Robotics Examples**,
   展開 **ROS2 → Navigation → Nova Carter** → 載入倉庫 + Nova Carter。
2. **生成佔位圖**:**Tools → Robotics → Occupancy Map**:
   - Origin:`X=0, Y=0, Z=0`
   - **Lower bound Z = 0.1、Upper bound Z = 0.62**(= Carter 雷射高度;
     换你自己的車就填你的 LiDAR/depth 感測高度帶)
   - 在 stage 選 `warehouse_with_forklifts` prim → **BOUND SELECTION**
   - **CALCULATE** → **VISUALIZE IMAGE**
   - Rotate Image = **180°**、Coordinate Type = **ROS Occupancy Map
     Parameters File (YAML)** → **RE-GENERATE IMAGE**
   - **存檔(注意:工具沒有「存 YAML」按鈕)**:`Save Image` 只存 PNG;
     YAML 是 RE-GENERATE 後顯示在視窗下方的**那段文字**,要自己複製、
     在 PNG 旁手建 `.yaml` 貼上,並確認 `image:` 行指向你存的 PNG 檔名
     (同資料夾用相對路徑)。resolution/origin 照工具顯示的抄,不要手打。
   - 存到 `<ros2_ws>/src/navigation/carter_navigation/maps/carter_warehouse_navigation.yaml`
3. **按 Play ▶**(場景要在跑,topics 才會出現)。
4. **起 Nav2**(工作站,另一個 sourced shell):
   ```bash
   ros2 launch carter_navigation carter_navigation.launch.py
   ```
   RViz2 會載入佔位圖;定位偏了用 **2D Pose Estimate** 校正。
5. **試跑**:RViz2 按 **Navigation2 Goal** 點目標 → Carter 應繞開貨架導航。

### 路線 A 直接當驗證載具(sim-first 的捷徑,已實測可行)

Carter 倉庫場景本身就能當 V1_GATE 的「載具側」用——RViz goal 能導航避障
= B1 的 `/navigate_to_pose` 確認完成。接 JenAI 只要對齊 vehicle profile:

```toml
[vehicle]
type = "diff"                 # Nova Carter 是差速,不是阿克曼
cmd_vel_topic = "/cmd_vel"    # 以 `ros2 topic list` 實際看到的為準
camera_topic = "/front_stereo_camera/left/image_raw"   # 同上,以實際 topic 為準
max_linear = 0.8              # 倉庫內保守值
max_angular = 1.0

route_adapter = "nav2"
```

再照 [TWIN_SETUP](TWIN_SETUP.md) §5 走(doctor → 建點 → `/route`)即可開始
B2–B4。**代價要知道**:Carter 是差速車,阿克曼運動學(Smac Hybrid-A* /
最小轉彎半徑)的 sim 數據不會從這裡產生——論文若要阿克曼章節的模擬證據,
之後仍需路線 B 的 Leatherback 場景;架構層(決策/Gate/安全鏈)的驗證數據
則載具無關,Carter 收的完全算數。

## 路線 B|你的 Leatherback 場景(接 JenAI 的正式路線)

**目標**:現有 ground plane 場景 → 加障礙物 → 佔位圖 → Nav2 → JenAI `/route`。

1. **加障礙物**:在你的場景擺牆/箱子(Create → Shape,調成靜態 collider)。
   ground plane 本身無邊界,佔位圖會是空的 —— **要有幾何,佔位圖才有內容**。
2. **確認車輛 topics**:你的 Leatherback 已發 `/odom` `/cmd_vel`(現況實測過)。
   Nav2 還需要:
   - **雷射或 pointcloud**:你只有 `/depth`(Image)。兩個選項:
     (a) 在 Isaac 的 Leatherback 上加 **RTX Lidar** sensor 發 `/scan`(官方
     Carter 做法,最省事);(b) 工作站跑 `depthimage_to_laserscan` 節點把
     `/depth` 轉 `/scan`。建議 (a)。
   - **TF**:`odom → base_link`(Isaac ROS2 bridge 的 TF publisher;檢查
     `ros2 run tf2_tools view_frames`)。
3. **佔位圖**:同路線 A 的 Tools → Robotics → Occupancy Map,bound 你的
   場景 prim,Z 帶用你感測器高度(小車低,約 0.05–0.3m),存 PNG + 手貼 YAML
   (工具只顯示 YAML 文字不存檔,見路線 A 步驟 2 的存檔說明)。
4. **Nav2 bringup**(不用 carter 包,直接 nav2_bringup):
   ```bash
   ros2 launch nav2_bringup bringup_launch.py \
     map:=/path/to/your_map.yaml \
     params_file:=/path/to/nav2_params.yaml
   ros2 launch nav2_bringup rviz_launch.py   # 另開 RViz
   ```
   `nav2_params.yaml` 從 `/opt/ros/jazzy/share/nav2_bringup/params/` 複製後改:
   - 車小(軸距 15cm):`robot_radius` ≈ 0.12、速度上限對齊 `[vehicle]`
   - 阿克曼構型:planner 換 **Smac Hybrid-A\***(`nav2_smac_planner/SmacPlannerHybrid`,
     設 `minimum_turning_radius`)、controller 換 **Regulated Pure Pursuit**
   - 起步可先用預設(差速假設)驗流程,再換阿克曼參數調精
5. **RViz 2D Pose Estimate** 設初始位姿(AMCL 需要),Navigation2 Goal 試跑。

## 接上 JenAI(場景跑通後)

1. config 切回 Nav2:
   ```toml
   route_adapter = "nav2"     # 從 "odom" 換回來
   ```
2. TUI:`/route 從應科大樓到機械系館` → 走真 Nav2(全域規劃 + costmap 避障),
   即時剩餘距離、Esc 真取消照舊。
3. **驗證我的避障演算法 vs Nav2**(你提的模擬驗證):同一組起終點 + 障礙,
   分別用 `route_adapter="odom"`(stop-and-go detour)與 `"nav2"` 跑,比:
   成功率、路徑長、最小障礙距離 —— 這正是論文 E2/E3 的素材,
   也直接餵 [ROADMAP](ROADMAP.md) 軌道 2/3。
4. Twin Gate:這個場景就是 B5 的雛形 —— `[twin]` 啟用 + 禁區設好,
   G1 用 contact sensor、G3 用你畫的禁區,消融數據(攔截率/誤攔率)就有了。

## RTX LiDAR → LaserScan 的完整掃描要求

Isaac Sim 的 ROS2 RTX LiDAR Helper 必須設定 **`Publish Full Scan=True`**。這個選項讓
PointCloud2 每次發布包含時間上完整的 360° 點雲，避免每一幀只送旋轉中的約 60° wedge；
目前 `pointcloud_to_laserscan` 再從該完整點雲裁出導航所用的前向 180° `/scan`
（`angle_min=-π/2`、`angle_max=π/2`、362 bins）。轉換器是逐筆轉換，不會自行把多筆
局部 wedge 累積成完整視野。

`ros2 topic hz /scan` 只證明訊息持續抵達，**不能證明空間覆蓋足夠**。2026-07-19 的
故障樣本雖然 topic 有頻率，10 筆 `/scan` 仍有 3 筆全為 `+inf`，有限 bin 覆蓋率只有
18.48%；開啟 Full Scan 後，最終正式 HIL 樣本為 10/10 筆可用、0 筆全空、每筆 362
bins、57.0442% valid finite。請以 [Isaac HIL preflight](ISAAC_HIL_ACCEPTANCE.md) 驗證，
而不是只看 Hz；現行 gate 亦檢查角寬／increment／幾何誤差、range bounds、scan_time、
固定 frame 與前進 timestamp。比例門檻為全空樣本不高於 20%、aggregate valid-finite
coverage 至少 25%。

若 Helper 的完整掃描輸出為 10 Hz，`pointcloud_to_laserscan` 的 `scan_time` 應設為
`0.1`；不要沿用與實際週期不符的 `0.3333`。`scan_time` 必須依實際 full-cloud 更新週期
校正，修改後重建 navigation workspace、重啟 Nav2，再確認 runtime parameter 與
LaserScan message 都是同一值。

## 常見坑

| 症狀 | 原因/解法 |
|---|---|
| 佔位圖全白/全黑 | Z 帶沒切到障礙物高度;或忘了 BOUND SELECTION |
| 場景缺 2D 雷射(AMCL 沒 /scan) | 重載場景後 2D lidar graph 遺失 | 用 3D 光達轉:`ros2 run pointcloud_to_laserscan pointcloud_to_laserscan_node --ros-args -r cloud_in:=/front_3d_lidar/lidar_points -r scan:=/scan -p target_frame:=front_3d_lidar -p min_height:=-0.3 -p max_height:=0.5 -p range_max:=25.0`(2026-07-15 實測可行,AMCL/Nav2 照常) |
| Nav2 起了但 RViz 沒地圖 | map yaml 路徑錯,或 map_server 沒起(看 bringup log) |
| 車不動、RViz 能規劃 | `/cmd_vel` 沒接到車(Isaac 端 topic 名對齊;或 controller 輸出 remap) |
| AMCL 不收斂 | 先 2D Pose Estimate;雷射高度帶與佔位圖 Z 帶不一致也會 |
| `/scan` 有頻率但 AMCL 跳位／資料忽空忽有 | RTX Helper 仍在逐幀發布旋轉 wedge；頻率不能代表視野完整 | 設 `Publish Full Scan=True`，10 Hz full cloud 時令 converter `scan_time=0.1`，再跑 HIL scan-quality preflight |
| topics 看不到 | Isaac 沒按 Play;或兩邊 ROS_DOMAIN_ID 不同 |
| Isaac 用了內建 ROS 庫 | 開 Isaac 前忘了 source(24.04 沒 source 會自動載內建 Jazzy 庫,通常也能通,但版本混用問題難查) |

---

**Sources**(2026-07 擷取):
- [ROS 2 Navigation — Isaac Sim Documentation](https://docs.isaacsim.omniverse.nvidia.com/latest/ros2_tutorials/tutorial_ros2_navigation.html)
- [ROS 2 Installation — Isaac Sim Documentation](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/install_ros.html)
- [Isaac Sim Requirements](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html)
