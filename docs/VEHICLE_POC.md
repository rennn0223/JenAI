# VEHICLE_POC — 跨載具介面與物理驗收

Vehicle Profile 與高階 capability schema 證明的是**設計可移植性**。只有完成本文件的
固定任務與證據保存，某一載具才可從 `Experimental` 升為 `Validated`。

## Phase 0：安全與隔離

- 指定平台、ROS distro、RMW、domain、網路拓撲、硬體 emergency stop 操作者。
- 實體第一次測試採架空／低速／隔離區；先在 Isaac 或廠商模擬器完成相同任務。
- 禁止同一 ROS domain 同時接實體與孿生致動 topic。
- 記錄 vehicle profile、adapter commit、Nav2/controller 版本與地圖版本。

## Phase 1：介面盤點

| ID | 驗收 | 通過條件 |
|---|---|---|
| V1 | ROS graph | JenAI 能找到 pose、速度命令、feedback、Nav2 action 與可選相機 |
| V2 | Schema | topic/action type 與欄位由 live graph 取得，不靠模型猜測 |
| V3 | Profile-only | 優先只改 vehicle profile；任何程式 adapter 都記錄新增／修改 LOC |
| V4 | Failure honesty | 缺 topic、type 不合、Nav2 unavailable 時不得顯示成功或致動 |

## Phase 2：固定任務

| ID | 任務 | 主要指標 |
|---|---|---|
| T1 | 唯讀 inspect topics/type/pose | 成功率、錯誤型別、耗時 |
| T2 | 一次低速有界移動＋自動停止 | 里程／角度誤差、停止延遲、是否重複致動 |
| T3 | 兩個安全點 Nav2 導航 | goal success、終點偏差、回授完整性 |
| T4 | 導航中取消與 `/stop` | cancel latency、stop latency／距離、殘留 goal |
| T5 | 不可達 goal／回授中斷 | refer/block/fail 是否正確，禁止盲目重送 |
| T6 | 一圈 patrol／dock | waypoint 成功率、總時間、任務報告一致性 |

每個任務至少 10 次；任何擦撞、失控、hard stop、錯誤成功宣稱都記 incident，不從分母
刪除。四足平台另記步態／姿態 API，但 JenAI 不負責即時 locomotion。

## Phase 3：跨平台比較

至少比較 Ackermann 與另一種運動學平台：

- profile 設定時間；
- adapter 新增／修改 LOC；
- capability 覆蓋率；
- 各任務成功率與 P50/P95；
- stop latency／距離；
- Nav2／物理行為差異與未支援能力。

只有「profile/薄 adapter 成本低且固定任務通過」時，才可說高階介面具有跨載具可移植
證據。不得由相同 topic 名稱推導物理泛化。

## 必留 artifact

`run.json`（環境／版本／commit）、vehicle profile、逐 trial JSONL、doctor 輸出、事故表、
adapter diff、場景／地圖 ID 與結果摘要。照片可作佐證，但不能取代機器可讀結果。
