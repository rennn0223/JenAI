# Twin Gate:孿生閘控執行(M3)— 從零到 doctor 全綠

> 目標:讓每一個導航目標在載具動之前,先在 Isaac Sim 孿生場景「預演」一遍。
> 預演由五項判準(G1–G5)評分,結論只有三種:**pass**(放行)、**block**(硬性
> 安全違規,禁止執行)、**refer**(預演不可行或不確定,轉人工)。整層可用一行
> 設定關閉,關閉時導航行為與從前完全相同。
>
> 驗收進度條:`jenai doctor` 的 `twin` 區段(twin_graph / twin_nav2 / twin_contact_sensor)。

## 0. 建置總覽:四個階段,照順序做

sim-first 驗證(2026-07 定調)下,「載具」與「孿生」**都是 Isaac Sim 場景**,
差別只在 ROS_DOMAIN_ID。整條路是:

| 階段 | 做什麼 | 照哪份文件 | 完成的檢查點 |
|---|---|---|---|
| 1 | 官方 Nova Carter 倉庫樣例跑通 Nav2(學工具鏈,不接 JenAI) | [ISAAC_NAV2_SETUP](ISAAC_NAV2_SETUP.md) 路線 A | RViz 點 Navigation2 Goal,Carter 會繞開貨架 |
| 2 | 建**你的** Leatherback 場景 + 佔位圖 + Nav2(這就是「載具」,跑在預設 domain)。**捷徑**:直接沿用階段 1 的 Carter 倉庫當載具場景(見 ISAAC_NAV2_SETUP「路線 A 直接當驗證載具」;差速車,阿克曼 sim 數據需回頭補路線 B) | [ISAAC_NAV2_SETUP](ISAAC_NAV2_SETUP.md) 路線 B(或 A 捷徑) | `ros2 action list` 看得到 `/navigate_to_pose`;RViz 下 goal 車會走 |
| 3 | 接上 JenAI:`route_adapter="nav2"`、建點、`/route` 實測(解鎖 B1–B4,**還不需要 Gate**) | 本文件 §5 | TUI `/route` 能導航、即時剩餘距離、Esc 可取消 |
| 4 | 孿生側:同一場景複製到 domain 42 + 接觸感測器 + `[twin]` 啟用(B5) | 本文件 §1–§4 | `jenai doctor` twin 三項全 pass;`/route` 前多一步預演 |

> 常見誤解:Twin Gate 不是階段 2 的一部分。**先讓載具側(階段 2–3)能跑,
> 再加孿生側(階段 4)**;`[twin] enabled = false` 時 JenAI 完全不碰孿生。

## 1. 架構:孿生走自己的 ROS_DOMAIN_ID

載具與孿生是**兩個完全隔離的 ROS graph**:

```
載具 graph:ROS_DOMAIN_ID=<環境預設,通常 0>  ← bridge、Nav2、感測器(sim-first:Isaac 實例 #1)
孿生 graph:ROS_DOMAIN_ID=42(可改)          ← Isaac 實例 #2 + 同一套 Nav2 + 同一張佔位圖
```

JenAI 會在需要預演時,額外起一條**孿生 bridge**(同一支 `ros_bridge.py`,但以
`ROS_DOMAIN_ID=[twin].domain_id` 啟動),預演的 Nav2 goal、位姿取樣、碰撞事件
全部走這條,**不可能誤發到載具側**。

sim-first 模式的具體形態:**兩個 Isaac Sim 實例、兩套 Nav2**,各自在自己的
domain。同一份場景 USD、同一張佔位圖開兩份即可;孿生側可用 headless 跑省資源
(`./isaac-sim.headless.sh` 或啟動參數 `--no-window`),也可以只在跑 Gate
實驗時才開(doctor 與 Gate 都是要用時才探測,平時不開不影響其他功能)。

## 2. 孿生側 Isaac Sim + Nav2(階段 4 的第一步)

前提:階段 2 已完成——你已有場景 USD、佔位圖 YAML+PNG、調好的 `nav2_params.yaml`。
孿生側就是把同一套東西在 domain 42 再開一份:

1. **開第二個 Isaac 實例**(終端先設 domain,再 source ROS、再啟動——順序重要):
   ```bash
   export ROS_DOMAIN_ID=42     # 必須和 JenAI config 的 [twin] domain_id 一致
   source /opt/ros/jazzy/setup.bash
   ./isaac-sim.sh              # 開同一份場景 USD;孿生側可改用 headless
   ```
   按 **Play ▶**(場景沒在跑就不會有 topics)。
2. **起孿生側 Nav2**(另一個 shell,同樣先設 domain):
   ```bash
   export ROS_DOMAIN_ID=42
   source /opt/ros/jazzy/setup.bash
   ros2 launch nav2_bringup bringup_launch.py \
     map:=/path/to/your_map.yaml params_file:=/path/to/nav2_params.yaml
   ```
   用**與載具側同一張佔位圖與同一份參數**(sim-first:就是階段 2 產出的那張;
   若日後接真車,則兩側都用真車的地圖)。
3. **驗收隔離**:
   ```bash
   ROS_DOMAIN_ID=42 ros2 action list   # 應看到 /navigate_to_pose
   ros2 action list                    # 載具 domain 看不到孿生的節點才算隔離成功
   ```

## 3. 碰撞感測(G1 的資料來源)

G1 需要孿生場景在車體碰到東西時,於 `/twin/collision` 發 `std_msgs/msg/Bool`
的 `true`。在**孿生側** Isaac 實例上:

1. **加感測器**:Stage 面板選 Leatherback 的底盤 rigid-body prim(有 Physics
   collider 的那個)→ 選單 **Create → Isaac → Sensors → Contact Sensor**
   (不同版本選單位置略異,找不到就在 Create 搜 "Contact Sensor");確認它
   掛在底盤 prim 底下。
2. **接 Action Graph 發佈**(Window → Graph Editors → Action Graph,新建或
   沿用現有 graph),三個節點串起來:
   - `On Playback Tick` → `Isaac Read Contact Sensor`(指到剛加的 sensor prim)
   - `Isaac Read Contact Sensor` 的 **In Contact** 輸出 → `ROS2 Publisher`
     (messageType 選 `std_msgs/msg/Bool`,topicName 填 `/twin/collision`)
3. **驗收**:按 Play 後,把場景裡一個箱子拖去撞車(或反過來),另開 shell:
   ```bash
   ROS_DOMAIN_ID=42 ros2 topic echo /twin/collision   # 撞到時應看到 data: true
   ```

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

# G3:禁區(map 座標系的矩形,可多個)。座標怎麼查:在 RViz 地圖上
# 用 Publish Point 點禁區兩角,看 /clicked_point 的 x/y。
[[twin.forbidden_zones]]
name = "樓梯口"
x_min = 3.0
y_min = -1.0
x_max = 5.0
y_max = 1.5
```

改完跑 `jenai doctor`:`twin` 區段 twin_graph / twin_nav2 / twin_contact_sensor
三項 pass 即為 **B5 場景建置完成**,之後每個導航目標都會先過閘。

## 5. 載具側接上 JenAI(階段 3;不需要 Gate 就能做)

1. config 切 Nav2:`route_adapter = "nav2"`(從 `"odom"`/`"stub"` 換回來)。
2. `jenai doctor` 的 `nav` 區段應轉綠(看得到 map/amcl/Nav2)。
3. 建點:車開到定點後 `/loc add here <名>`;充電點加 `tags=["dock"]`。
4. `/route 從A到B` 實測:即時剩餘距離、Esc 真取消。這一步通了就能開始
   B3(TEST.md 解鎖)與 B4(模擬里程)的數據累積。

## 6. 判準與裁決

| 判準 | 內容 | 失敗時 |
|---|---|---|
| G1 | 碰撞:孿生接觸感測器觸發 | **block** |
| G2 | 超時:預演未在 `nav_timeout_s` 內完成 | refer |
| G3 | 禁區:目標點在禁區,或孿生軌跡進入禁區 | **block** |
| G4 | 終點偏差:抵達但離目標 > `goal_tolerance_m` | refer |
| G5 | Nav2 失敗:孿生的 Nav2 abort / reject | refer |

裁決原則:**G1/G3 是硬性安全違規 → block**(載具絕不動);**G2/G4/G5 是
「預演做不到或不確定」→ refer**(孿生場景可能過時,由人判斷)。孿生連不上時
同樣 refer——**Gate 啟用中絕不默默放行**。

配置了 forbidden zones 時,G3 必須至少取得一筆 twin pose sample 才能 pass;
整段預演都拿不到 pose 時,G3 標記 `skipped`、整體 verdict 為 `refer`,不會把
「沒有觀測到」誤當成「沒有進入」。

refer 的行為依入口而異:互動介面(TUI/WebUI/MCP)會把完整的 Gate 報告誠實
回報給操作者,由人決定關閉 Gate、修正場景或改目標;**daemon 自主路徑沒有人
可轉,refer 一律視同 block**,機器人原地不動。

## 7. 生效範圍

Gate 掛在導航的唯一出口(`navigate_with_fallback`)與 daemon 的自主導航上,
因此 `/route`、`/mission`、`/patrol`、`/dock`、MCP `navigate`、WebUI、daemon
規則觸發的移動**全部**經過同一道閘門;`/stop` 急停與 `/drive` 低速手動不經
Gate(停車永遠安全;手動微調由速度硬夾與 HITL 批准把關)。

## 8. 疑難排解

- `twin gate: REFER — twin unreachable`:孿生 bridge 起不來。確認孿生側 Isaac
  **有按 Play** 且與 Nav2 都在 `domain_id` 指定的 domain 上
  (`ROS_DOMAIN_ID=42 ros2 topic list`)。
- 每次預演都 G2 超時:孿生即時率(RTF)太低(兩個 Isaac 實例搶 GPU 常見)。
  孿生側改 headless、關掉不必要的 viewport 渲染,或調高 `nav_timeout_s`。
- G1 永遠 skipped:場景沒有接觸感測器、topic 名稱與 `collision_topic` 不一致,
  或感測器掛錯 prim(要掛在有 collider 的 rigid body 上)。
- G3 skipped / `no twin pose samples`:孿生沒有發佈 `/amcl_pose` 或 `/odom`,
  或 pose 全是 NaN/inf(定位壞掉;非有限座標不算有效樣本);修好 pose 後才會
  放行有禁區的導航。
- 載具 domain 出現孿生節點:兩邊 `ROS_DOMAIN_ID` 沒分開,回到 §1。
- 兩個 Isaac 實例太重:孿生側只在跑 Gate 實驗(B5 消融)時才開;平日跑
  B3/B4 把 `[twin] enabled = false` 即可,兩件事互不相欠。
