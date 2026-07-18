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

- 對象：至少 6 人；新手與有 ROS2 經驗者分層記錄，目標 12 人以上。
- 設計：within-subject，三條件以平衡 Latin order 輪替。
- 條件：`manual`（只用 ros2 CLI）、`slash`（只用確定性 Slash）、`natural`（自由文字）。
- 任務：查 topic type、取得一次 feedback、完成一次有界移動並驗證 odom。
- 環境：E3 隔離 `ROS_DOMAIN_ID=42` fixture；每個 trial 前重置，不能接 domain 0 實車。
- 隱私：只保存 P01 類代碼與量化指標，不保存姓名、完整 prompt、畫面或場域資料。

成功條件必須由觀察者依 live ROS2 結果判定，不能以 Agent 自述「成功」代替。

## 產生排程

```bash
uv run python scripts/usability_study.py schedule --participants 6 \
  --out usability-schedule.csv
```

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

失敗使用 `--failed`，不可刪除或重跑覆蓋。若重新測試，新增一筆並在 notes 說明原因；
notes 不得含姓名、場域或完整 prompt。相同 participant×task×condition 的重複資料仍列入
條件摘要，但會排除於配對速度比並明確列出排除數。

## 摘要

```bash
uv run python scripts/usability_study.py summary \
  --input usability-study.jsonl --out usability-summary.md
```

摘要同時報每條件成功率、全 trial median/P95、錯誤、查詢、介入與命令數，並只對雙方
皆成功的 participant×task 計算配對時間比。時間比不能單獨報告，否則排除失敗樣本會讓
結果看起來過度樂觀。

## 論文／產品回填

- 論文：方法、參與者背景、隨機順序、成功定義、全部失敗、描述統計與限制。
- PM：找出安裝、命名、提示與批准流程的最大摩擦，不只看平均時間。
- 業務：只有在樣本與成功率一起揭露時，才可引用節省時間。
- 原始 JSONL/CSV 留本機；若公開，先移除 notes 並確認無可識別資訊。
