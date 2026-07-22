# 從零到第一次導航(Onboarding)

> 目標:一台裝好 ROS2 Jazzy 的機器人(或 Isaac Sim 模擬),從「什麼都沒有」走到「對 JenAI 說 `/route 去充電站`,機器人真的走過去」。全程約 30–60 分鐘,以阿克曼車(Leatherback)為例,實驗室尺度即可。
>
> 每一步都有驗收指令;卡住時先跑 `jenai doctor`——`nav` 區段的五項檢查(map/localization/laser/nav2/cmd_vel)就是本文件的進度條。

## 0. 前置

- JenAI 已裝好(見 [TECHNICAL_GUIDE.md](TECHNICAL_GUIDE.md) §2):`jenai doctor` 的 config/provider 是 pass
- ROS2 Jazzy 已 source;Nav2 與 slam_toolbox 已安裝:
  ```bash
  sudo apt install ros-jazzy-navigation2 ros-jazzy-nav2-bringup ros-jazzy-slam-toolbox
  ```

## 1. 確認底盤會動(cmd_vel 鏈路)

機器人的馬達控制器必須訂閱速度指令,否則後面全部白搭。

```bash
ros2 topic info /cmd_vel        # Subscription count 必須 ≥ 1
```

- **Subscription count: 0** → 底盤驅動沒起來,先啟動你的 base controller(Leatherback:確認 `cmdvel_to_ackermann` 轉換節點在跑)
- 有訂閱者後,用 JenAI 做第一次會動的驗收(**架高輪子或清空前方**):
  ```
  /drive 前進一秒
  ```
  批准後車應動一下並自動停。速度上限由 `~/.config/jenai/config.toml` 的 `[vehicle] max_linear/max_angular` 硬夾,LLM 給再大也超不過。
- **急停測試(必做)**:讓車動起來,按 WebUI 紅色 STOP 或輸入 `/stop`,確認輪子真的停。第一次上真車,這顆按鈕要先確認能用。

## 2. 確認感測(laser + odom)

```bash
ros2 topic hz /scan     # LiDAR 要有穩定頻率(10Hz 上下)
ros2 topic hz /odom     # 里程計要在跑
```

沒有 `/scan` → 檢查 LiDAR 驅動;沒有 `/odom` → 檢查底盤驅動。JenAI 裡可用 `/ros topics`、`/ros echo /scan 1` 快速查看。

> **Isaac RTX LiDAR 注意**：`topic hz` 只證明訊息持續到達，不能證明每筆掃描的空間
> 覆蓋。ROS2 RTX LiDAR Helper 請設 `Publish Full Scan=True`，讓每筆 PointCloud2 是
> 時間上完整的 360° 點雲，再由 converter 裁成目前 Nav2 使用的前向 180° `/scan`；
> 10 Hz full cloud 時 `scan_time=0.1`。用 [Isaac HIL preflight](validation/ISAAC_HIL_ACCEPTANCE.md) 的 scan-quality
> preflight 確認全空比例與 finite-bin coverage，不要只看頻率。

## 3. 建圖(slam_toolbox)

第一次到新場地,先建一張地圖:

```bash
ros2 launch slam_toolbox online_async_launch.py
```

然後**慢速**開車繞場地(用 `/drive` 或你的遙控),牆面走廊都掃到。另開終端看進度:

```bash
ros2 run rviz2 rviz2   # 加 Map display,topic /map
```

覆蓋率滿意後**存圖**(不要關 slam_toolbox):

```bash
mkdir -p ~/maps
ros2 run nav2_map_server map_saver_cli -f ~/maps/lab
# 產出 ~/maps/lab.yaml + lab.pgm
```

存完即可 Ctrl-C 關掉 slam_toolbox。

> 實驗室小場地技巧:速度放最慢、轉彎多停頓,迴圈閉合(loop closure)至少繞完整一圈;玻璃與細桌腳雷射看不清,地圖上可事後用影像編輯器把它們補黑。

## 4. 定位(AMCL)

之後每次開機,用存好的地圖做定位:

```bash
ros2 launch nav2_bringup localization_launch.py map:=$HOME/maps/lab.yaml
```

在 RViz 用 **2D Pose Estimate** 點一下車的實際位置與朝向,然後:

```bash
ros2 topic echo /amcl_pose --once    # 有輸出 = 定位成功
```

此後 JenAI 的 `/loc add here`、WebUI 地圖、`robot_pose`(MCP)都會優先用 AMCL 位姿(沒有 AMCL 時誠實退回 /odom)。

## 5. 啟動 Nav2

```bash
ros2 launch nav2_bringup navigation_launch.py
```

> **阿克曼注意**:預設 Nav2 參數是差速車。阿克曼要換規劃器/控制器——planner 用 **Smac Hybrid-A\***(`nav2_smac_planner/SmacPlannerHybrid`,設 `minimum_turning_radius` = 你的 L/tan δ_max)、controller 用 **Regulated Pure Pursuit**。改 `nav2_params.yaml` 後以 `params_file:=` 帶入。這一步做錯的症狀:窄處規劃出原地轉向的路徑,實車走不了。

驗收:

```bash
ros2 action list | grep navigate_to_pose   # 要出現 /navigate_to_pose
```

## 6. JenAI 收線:建點 + 第一次導航

```bash
jenai doctor        # nav 區段五項應全 pass
jenai               # 進 TUI
```

TUI 裡:

```
/loc add here 起點          ← 站在哪存哪
(把車開到充電座旁)
/loc add here Dock          ← 之後 /dock 一鍵回充會認它
/route 去起點               ← 批准後:即時剩餘距離、Esc 真取消
/patrol 起點, Dock x2       ← 巡邏跑起來
```

WebUI(`jenai web`)的地圖此時會顯示你存的點位和車的即時位置。

## 7. 疑難排解速查

| 症狀 | 最可能原因 | 解法 |
|---|---|---|
| `jenai doctor` nav 全 warn | 機器人/模擬器根本沒開 | 依 §1–§5 順序啟動 |
| `/route` 回 unavailable | Nav2 沒起 or `route_adapter` 不是 `"nav2"` | §5;config 檢查 `route_adapter = "nav2"` |
| 車不動但 Nav2 說在跑 | cmd_vel 沒訂閱者 / topic 名不對 | §1;`[vehicle] cmd_vel_topic` 對齊實際 topic |
| 定位一直飄 | 初始位姿沒給 / 地圖品質差 | RViz 重給 2D Pose Estimate;重建圖 |
| 窄處規劃失敗或原地打轉 | 用了差速車預設參數 | §5 的阿克曼參數 |
| 位置對但朝向錯 | `/loc add here` 存的 yaw 是當下朝向 | 存點時把車擺成你要的到達朝向 |
| Esc 取消後車還在走 | bridge 沒起(走了 CLI 後備路徑) | `jenai doctor`;確認系統 python 有 rclpy |

## 8. Isaac Sim 模擬(替代真車)

沒有真車時，以上流程對 Isaac Sim 同樣成立：場景裡的車發 `/odom`，ROS2 RTX LiDAR Helper 設 `Publish Full Scan=True` 後發布時間上完整的 360° 點雲，再由 `pointcloud_to_laserscan` 產生 Nav2 使用的前向 180° `/scan`；10 Hz full cloud 時 converter `scan_time=0.1`。車輛訂閱 `/ackermann_cmd`（裝 `cmdvel_to_ackermann` 轉換後即 `/cmd_vel`）。相機 topic 通常是 `/rgb`——把 `[vehicle] camera_topic` 改過去，`/vision camera`、`/patrol … photo` 就可使用。孿生與實車同時在線時，請用不同 `ROS_DOMAIN_ID` 隔離；domain 0 的純模擬成功不代表隔離已驗證。
