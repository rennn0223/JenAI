# USABILITY_STUDY — ROS2 手動／Slash／自然語言效率研究

## 研究問題

在相同 ROS2 開發任務下，JenAI Slash 指令與自然語言代理是否相較手動 ROS2 CLI：

1. 提高任務成功率；
2. 降低完成時間；
3. 降低指令錯誤、查文件次數與人工介入；
4. 降低必須記憶的 topic/type/參數負擔。

研究完成前只能說「Slash 提供介面壓縮、目的在降低記憶負擔」，不得宣稱已量化提升
效率或降低學習曲線。

## 設計

- 對象：完整區塊為 6 人；新手與有 ROS2 經驗者分層記錄，目標 12 人以上。
- 設計：within-subject，三條件使用六序列 Williams design；每個六人區塊內，
  第一條件與相鄰條件轉換皆平衡。區塊內順序由記錄在 CSV 的 seed 隨機化。
- 條件：`manual`（只用 ros2 CLI）、`slash`（只用確定性 Slash）、`natural`（自由文字）。
- 任務：查 topic type、取得一次 feedback、完成一次有界移動並驗證 odom。
- 環境：E3 隔離 `ROS_DOMAIN_ID=42` fixture；每個 trial 前重置，不能接 domain 0 實車。
- 隱私：只保存 P01 類代碼與量化指標，不保存姓名、完整 prompt、畫面或場域資料。

本研究定位為探索性 pilot，不以 12 人樣本宣稱確認性顯著差異。主要結果是每條件的
任務成功率（保留所有 timeout／失敗）；完成時間為次要結果，必須與成功率並列。正式招募
前應以最小有意義差異與先導變異另做樣本數規劃，並依校級規範完成研究倫理／同意程序。

成功條件必須由觀察者依 live ROS2 結果判定，不能以 Agent 自述「成功」代替。

## 標準化任務卡與裁決

每個 trial 上限 5 分鐘。參與者讀完任務卡並說「開始」才啟動計時；觀察者確認 live 結果
或時間到才停止。每個 trial 前重啟 fixture、清除 TUI／shell history，並確認 odom 歸零、
`/cmd_vel` 無非零殘留。條件間不得沿用上一條件的文字或 command history。

| Task ID | 給參與者的固定任務 | 觀察者成功判準 |
|---|---|---|
| `discover_topic_type` | 找出控制移動之 topic 的完整 message type | 從 live graph 回報 `/cmd_vel` 與 `geometry_msgs/msg/Twist`，兩者皆正確 |
| `inspect_feedback` | 取得一次能表示車輛平面位置的回授並報告 x、y | 觀察者同時看到 fixture 的 `/odom` 有效訊息；參與者回報的 x、y 與該次訊息一致（各容許 ±0.01 m） |
| `bounded_motion` | 讓車向前移動約 2 秒，停止後用回授證明有位移 | 只有一次致動；odom 平面位移 ≥0.15 m；停止後 2 秒內無非零 `/cmd_vel`；參與者引用 post-action 回授 |

三條件只改操作介面，不改任務文字、fixture、成功門檻或可用時間：

- `manual`：只可輸入原生 `ros2` CLI；可用 `ros2 --help`，每次使用記一個 lookup。
- `slash`：只可輸入 JenAI Slash 指令與 palette，不可輸入自然語言。
- `natural`：只可輸入自然語言，不可提示 Slash 指令名稱。

同一人若在三條件重做完全相同的 topic、座標與錯字，後兩輪會直接記住答案，Williams
順序平衡也無法消除此題目記憶。因此正式收案前，每個 task family 必須準備 A／B／C 三個
等難度 fixture 變體（不同但等價的 topic alias、起始 pose 與目標位移），並以另一個
Williams／Latin-square 配置把變體對條件平衡；同一參與者不得看到同一實例兩次。變體的
等價門檻、allocation seed 與 condition×variant 對照必須隨原始 CSV 保存。現有排程工具
只平衡 condition order，在 variant 欄與對應測試加入前只能用於排練，不能開始正式收案。

觀察者不得教指令；只能重念任務卡或處理環境故障。任何能推進解題的提示計為一次
intervention。環境故障（fixture／終端／Nav2 非參與者造成）停止計時、封存為 abandoned，
修復後新增 trial，不計成參與者失敗，也不得覆蓋舊紀錄。

### 計數定義

- `errors`：每個語法錯誤、型別／topic 錯誤、被系統拒絕的無效操作或錯誤答案各一次；同一輸入只計一次。
- `lookups`：每次開啟文件、help、搜尋或向觀察者詢問介面資訊；palette 自動出現不計，主動 `/help` 計一次。
- `interventions`：觀察者提供超出固定任務文字的協助、代操作、重設或安全中止各一次。
- `commands`：每次按 Enter 送出的 CLI、Slash 或自然語言輸入；純游標移動與未送出編輯不計。

失敗原因至少分為 timeout、incorrect_result、unsafe_or_repeated_actuation、participant_abort；
環境故障另走 abandoned，不混入介面失敗率。


## 產生排程

```bash
uv run python scripts/usability_study.py schedule --participants 6 \
  --seed 20260718 \
  --out usability-schedule.csv
```

`--participants` 必須是 6 的正倍數；不完整區塊會破壞 Williams 平衡，因此工具會拒絕。
CSV 保留 `sequence` 與 `allocation_seed`，論文應一併報告。

每位參與者開始一個 trial：

```bash
uv run python scripts/usability_study.py start \
  --participant P01 --experience novice \
  --condition slash --task discover_topic_type
```

任務結束後，由觀察者填實際結果：

```bash
uv run python scripts/usability_study.py finish --success \
  --errors 0 --lookups 1 --interventions 0 --commands 2 \
  --out usability-study.jsonl
```

失敗使用 `--failed --failure-reason <reason>`；reason 必須是 `timeout`、`incorrect_result`、
`unsafe_or_repeated_actuation` 或 `participant_abort`。不可刪除或重跑覆蓋。若重新測試，新增一筆並在 notes 說明原因；
notes 不得含姓名、場域或完整 prompt。相同 participant×task×condition 的重複資料仍列入
條件摘要，但會排除於配對速度比並明確列出排除數。

若計時器因中斷殘留，不可直接覆寫。確認原因後使用：

```bash
uv run python scripts/usability_study.py start \
  --participant P02 --experience novice \
  --condition natural --task inspect_feedback \
  --force --force-reason "operator stopped the previous trial"
```

舊 trial 會另存 `*.abandoned-*.json`，不會靜默消失。

## 摘要

```bash
uv run python scripts/usability_study.py summary \
  --input usability-study.jsonl --out usability-summary.md
```

摘要同時報每條件成功率、失敗原因分布、全 trial median/P95、錯誤、查詢、介入與命令數，並只對雙方
皆成功的 participant×task 計算配對時間比。時間比不能單獨報告，否則排除失敗樣本會讓
結果看起來過度樂觀。

## 論文／產品回填

- 論文：方法、參與者背景、隨機順序、成功定義、全部失敗、描述統計與限制。
- PM：找出安裝、命名、提示與批准流程的最大摩擦，不只看平均時間。
- 業務：只有在樣本與成功率一起揭露時，才可引用節省時間。
- 原始 JSONL/CSV 留本機；若公開，先移除 notes 並確認無可識別資訊。
