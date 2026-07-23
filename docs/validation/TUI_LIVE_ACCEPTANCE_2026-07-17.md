# JenAI v1.1.0 Isaac Sim / Nav2 現場驗收紀錄

驗收日期：2026-07-17
平台：DGX Spark、Isaac Sim 倉庫場景、ROS2 Jazzy、Nav2、本機 Ollama `qwen3.6:35b`
載具：小型 Ackermann 構型之模擬無人地面載具
介面：JenAI TUI，預設 approve 模式

本紀錄是當日互動式系統驗收，不取代 E1–E4 與 B4 的正式實驗資料。所有致動項目皆在 Isaac Sim 畫面中執行；本次未宣稱完成實體車安全驗證。

## 地點與地圖前置驗證

| 地點 | x (m) | y (m) | yaw (rad) | tags |
|---|---:|---:|---:|---|
| `map_left_up` | -8.5 | 15.5 | -0.785 | `explore-test`, `patrol` |
| `map_right_up` | 8.0 | 15.5 | -2.356 | `explore-test`, `patrol` |
| `map_left_down` | -8.5 | -7.5 | 0.785 | `explore-test`, `patrol` |
| `map_right_down` | 8.0 | -9.5 | 2.356 | `explore-test`, `patrol` |
| `dock` | 4.355 | 3.236 | -1.289 | `dock` |

- 四個角落皆位於 free space，中心與 1.0 m 周界均未碰到 occupancy 或 unknown cell。
- 四角間 12 組有向 Nav2 規劃皆成功，規劃路徑未進入設定禁區。
- `/loc add here`、`show`、`move`、`rename`、`rm` 與 GPS datum 換算均以暫存地點完成 CRUD 實測；結束後只保留上表五個正式地點。

## TUI 與 ROS2 驗收結果

| 類別 | 實測項目 | 結果 |
|---|---|---|
| 系統 | `/status`、`/queue`、`/permissions`、`/config`、provider/model、`/skills` | 通過；讀到 5 個地點與自訂 `inspect` skill |
| ROS graph | topics、topic-info、schema、echo | 通過；讀到 `/cmd_vel`、`/amcl_pose`、`/chassis/odom`、相機、LiDAR 與 Nav2 actions |
| ROS 致動 | `/ros pub`、`/ros drive`、`/drive 前進一秒` | 通過；批准後發布且結束自動送零速度 |
| 閉環回授 | 前進 1 秒前後 `/chassis/odom` | 位移約 0.60 m；停止後線速度約 0.00065 m/s |
| 中止 | 路線執行中 `/stop` | Nav2 action cancelled、零速度送出；5 秒漂移約 0.0025 m |
| 視覺 | `/vision camera` | 辨識倉庫、黃色堆高機、貨架、箱件與棧板 |
| 感知 | perception start/status/stop | 分析 2 幀；建議只標示需批准，未自行致動 |
| Shell | `/shell pwd`、`!pwd` | 皆先經批准後執行 |
| 報告 | `/report` | 正確讀回路線、2/2 成功率與兩站影像觀察，並產生中文摘要 |

## 高階任務驗收結果

| 輸入 | 實際結果 |
|---|---|
| `/dock` | 已在目標附近時仍能以同步初始／最終 pose 證據通過，不再誤判為 G3 無樣本 |
| `/patrol dock, map_right_down x1 photo` | `2/2`；兩站皆抵達、各完成一次 VLM 觀察；寫入 `patrol-20260717-191210.json` |
| `/explore 3m goals=1 failures=1 tag=explore-test seed=5` | 從四個合格候選點選到 `map_left_down`，`1/1` 成功 |
| 自然語言：「隨機巡邏兩個 explore-test 地點，三分鐘內完成，最多允許兩次未成功，seed=7。」 | Agent 轉成 Explore 工具；路線 `map_left_down → map_left_up`，兩個不同目標、`2/2` 成功、0 次失敗 |
| `/inspect` | 自訂 skill 依序走左上、右上、右下、左下，`4/4` 成功 |

上述結果支持 JenAI 的定位：LLM 負責理解意圖、選擇已註冊高階能力與整理回報；Nav2 與既有控制器負責規劃、避障和底盤控制。自然語言與 Slash 指令共用同一套批准、Navigation Gateway 與回授判定，而不是讓模型直接產生連續控制量。

## 當日發現與修正

1. `/ros schema` 的 LLM 人話摘要可能長時間等待。現加入 8 秒上限，超時即回傳確定性的欄位解析，不阻塞 TUI FIFO。
2. 模型可能把尚未執行的 plan step 標成 done/failed。現由 planner 將新計畫一律正規化為 pending，只有執行器能推進狀態；空計畫也由 schema 拒絕。
3. Nav2 回報成功但 Ackermann 端點常停在 0.75–0.77 m。當日場景的 `goal_tolerance_m` 由 0.5 m 校準為 0.8 m；這是模擬場景驗收值，不是可直接搬到實車的通用安全參數。
   此數值是當日 Twin G4 歷史門檻，已被 2026-07-24 的 Carter 精準 profile 與 JenAI
   terminal-pose 二次核對取代；不得引用為現行到點精度。
4. 已在目標附近時，Nav2 可能立即成功，舊版背景 sampler 尚未取得 pose 而造成 G3 refer。現於預演前同步擷取初始 pose，結束後再擷取最終 pose；非有限值與禁區判定仍保持 fail-closed。
5. 本機 35B 模型的自然語言路徑會有明顯推理等待；Slash Explore 幾乎立即進入執行，自然語言版本則需等待模型工具選擇與完成摘要。此延遲適合高階任務，不適合即時控制。

## 正式驗收窗外事件（H9）

2026 年 7 月 17 日後續操作稽核發現，一個舊 B4 driver 已在背景存活約 41 小時；Isaac Sim Stop 後再 Play 時，它恢復送出 Nav2 目標，模擬車因而卡牆。處置為終止 driver、取消活動導航目標，並確認最後 `/cmd_vel` 為零。

此事件不屬於上表的正式互動驗收，且發生在 B4 固定 subset 的 selection window 之後，因此不改寫可重建的 407／408 waypoint 任務結果；但原先的「0 安全事件」主張已撤回，因 report schema 沒有 incident 欄也沒有獨立觀察者。這仍是必須保留的負面工程證據。修正版 `b4_driver.sh` 已加入圈數／時間上限、`flock` 單實例鎖、EXIT `/stop` 與啟動前背景程序清查；`SIGKILL` 或主機故障仍可能略過清理，所以每次 Play 前仍須人工確認沒有舊 driver、活動 goal 與非零速度。此事件已列入 `SAFETY_CASE` H9。

## 通訊邊界聲明

本次 `twin.domain_id = 0` 是使用者刻意採用的單一 Isaac Sim、純模擬展示設定，因此預演與執行會作用在同一部模擬車，`doctor` 的 isolation 項目不應被解讀成實體部署已通過。模擬與實體同時上線時，必須改用不同 ROS domain、橋接白名單或等效隔離；純模擬開發期間實體車應關機。AI Decision Agent 的邊界是能力、權限與可觀察回授，通訊隔離是部署時防止虛擬命令誤觸實體的另一層保護。
