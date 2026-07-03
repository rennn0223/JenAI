# Twin Gate:孿生閘控執行(M3)

> 目標:讓每一個導航目標在真車動之前,先在 Isaac Sim 孿生場景「預演」一遍。預演由五項判準(G1–G5)評分,結論只有三種:**pass**(放行)、**block**(硬性安全違規,禁止執行)、**refer**(預演不可行或不確定,轉人工)。整層可用一行設定關閉,關閉時導航行為與從前完全相同。
>
> 驗收進度條:`jenai doctor` 的 `twin` 區段(twin_graph / twin_nav2 / twin_contact_sensor)。

## 1. 架構:孿生走自己的 ROS_DOMAIN_ID

真車與孿生是**兩個完全隔離的 ROS graph**:

```
真車  graph:ROS_DOMAIN_ID=<環境預設>   ← 平常的 bridge、Nav2、感測器
孿生  graph:ROS_DOMAIN_ID=42(可改)   ← Isaac Sim + 同一套 Nav2 + 同一張地圖
```

JenAI 會在需要預演時,額外起一條**孿生 bridge**(同一支 `ros_bridge.py`,但以 `ROS_DOMAIN_ID=[twin].domain_id` 啟動),預演的 Nav2 goal、位姿取樣、碰撞事件全部走這條,**不可能誤發到真車**。孿生可以跑在同一台 Jetson,或跑在工作站上、與 Jetson 同網段(DDS 會自動發現同 domain 的節點)。

## 2. Isaac Sim 孿生場景(工作站)

以 Leatherback(阿克曼)為例:

1. **場景**:在 Isaac Sim 建你的實驗場地孿生(照實際尺寸擺牆、障礙),放入 Leatherback USD。
2. **ROS2 橋接**:啟用 `omni.isaac.ros2_bridge` extension,並在啟動 Isaac Sim 的終端先設 domain:
   ```bash
   export ROS_DOMAIN_ID=42     # 必須和 JenAI config 的 [twin] domain_id 一致
   ./isaac-sim.sh
   ```
3. **必要的 topic**(用 Action Graph 接出,名稱與真車一致):
   - `/cmd_vel` 進(或經 `cmdvel_to_ackermann`)、`/odom`、`/scan`、TF 出
4. **Nav2(孿生側)**:在同一個 domain 起 Nav2,用**與真車同一張地圖**:
   ```bash
   export ROS_DOMAIN_ID=42
   ros2 launch nav2_bringup bringup_launch.py map:=<真車同一張>.yaml
   ```
5. 驗收:
   ```bash
   ROS_DOMAIN_ID=42 ros2 action list   # 應看到 /navigate_to_pose
   ros2 action list                    # 真車 domain 看不到孿生的節點才算隔離成功
   ```

## 3. 碰撞感測(G1 的資料來源)

在 Isaac Sim 給車體加 **Contact Sensor**,經 Action Graph 發佈為:

- topic:`/twin/collision`(可在 config 改)
- 型別:`std_msgs/msg/Bool`,接觸時發 `true`

沒有這個 topic 時 Gate 仍可運作,但 G1 會標記 `skipped`(doctor 會提醒)。

## 4. JenAI 設定

`~/.config/jenai/config.toml`:

```toml
[twin]
enabled = true            # 關閉整層:false(預設)
domain_id = 42            # 孿生的 ROS_DOMAIN_ID
nav_timeout_s = 180.0     # G2:預演超過此秒數視為超時
goal_tolerance_m = 0.5    # G4:孿生終點與目標的最大容許偏差
collision_topic = "/twin/collision"   # G1:接觸感測 topic
pose_sample_s = 0.5       # G3:軌跡取樣週期

# G3:禁區(map 座標系的矩形,可多個)
[[twin.forbidden_zones]]
name = "樓梯口"
x_min = 3.0
y_min = -1.0
x_max = 5.0
y_max = 1.5
```

## 5. 判準與裁決

| 判準 | 內容 | 失敗時 |
|---|---|---|
| G1 | 碰撞:孿生接觸感測器觸發 | **block** |
| G2 | 超時:預演未在 `nav_timeout_s` 內完成 | refer |
| G3 | 禁區:目標點在禁區,或孿生軌跡進入禁區 | **block** |
| G4 | 終點偏差:抵達但離目標 > `goal_tolerance_m` | refer |
| G5 | Nav2 失敗:孿生的 Nav2 abort / reject | refer |

裁決原則:**G1/G3 是硬性安全違規 → block**(真車絕不動);**G2/G4/G5 是「預演做不到或不確定」→ refer**(孿生場景可能過時,由人判斷)。孿生連不上時同樣 refer——**Gate 啟用中絕不默默放行**。

refer 的行為依入口而異:互動介面(TUI/WebUI/MCP)會把完整的 Gate 報告誠實回報給操作者,由人決定關閉 Gate、修正場景或改目標;**daemon 自主路徑沒有人可轉,refer 一律視同 block**,機器人原地不動。

## 6. 生效範圍

Gate 掛在導航的唯一出口(`navigate_with_fallback`)與 daemon 的自主導航上,因此 `/route`、`/mission`、`/patrol`、`/dock`、MCP `navigate`、WebUI、daemon 規則觸發的移動**全部**經過同一道閘門;`/stop` 急停與 `/drive` 低速手動不經 Gate(停車永遠安全;手動微調由速度硬夾與 HITL 批准把關)。

## 7. 疑難排解

- `twin gate: REFER — twin unreachable`:孿生 bridge 起不來。確認 Isaac Sim 與孿生側 Nav2 都在 `domain_id` 指定的 domain 上(`ROS_DOMAIN_ID=42 ros2 topic list`)。
- 每次預演都 G2 超時:孿生即時率(RTF)太低,調高 `nav_timeout_s` 或把 Isaac Sim 移到工作站跑。
- G1 永遠 skipped:場景沒有接觸感測器,或 topic 名稱與 `collision_topic` 不一致。
- 真車 domain 出現孿生節點:兩邊 `ROS_DOMAIN_ID` 沒分開,回到 §1。
