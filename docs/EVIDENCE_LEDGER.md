# EVIDENCE_LEDGER — 對外主張的單一證據表

> 本表是 README、論文、簡報與銷售說法的數字來源。原始資料留本機且由 SHA-256
> 辨識；歷史 release notes 不回寫。新結果若取代舊結果，必須新增一列並說明原因，不能
> 覆蓋失敗樣本。

## 已接受基準

| ID | 範圍／版本 | 結果 | 可支持的主張 | 不支持的主張 | 原始 artifact（本機） |
|---|---|---|---|---|---|
| E1-20260716 | qwen3.6:35b；64 個有界決策情境 | accuracy 54/64 = 84.4%；unsafe 4/64 = 6.25%；refer 32/64 = 50.0% | 決策層可量測，且錯誤需要 HITL／Twin／執行器多層邊界 | LLM 本身已安全、可直接做低階控制 | `e1-20260716.json`；SHA-256 `82a7cf…16d` |
| E2-20260715c | Isaac Sim/Nav2；full-twin 前導；5 類各 20，N=100 | 硬陷阱 60/60 block/refer；正常 20/20 pass；zone crossing 6 block／2 refer／12 pass（路徑繞開禁區） | Twin Gate 能攔截該固定場景集的界外、不可達與禁區終點；正常點無誤攔 | A/B/C 完整消融、未知場景泛化、實車碰撞保證 | `e2-20260715c/{targets.json,trials.jsonl}`；trials SHA-256 `332463…069` |
| E3-20260718 | ROS_DOMAIN_ID=42 fixture；qwen3.6:35b；完整 8 題 | 7/8 通過；8/8 無重複致動；唯一失敗在致動前呼叫不存在工具 | 自然語言可做 live graph 發現、一次有界動作與回授驗證；失敗不盲目重送 | 每次 prompt 都成功、domain 0 實體或 Isaac 結果 | `e3-agent-boundary-v111-20260718.jsonl`；SHA-256 `2285dc…937` |
| E3-D2-20260718 | 同上；只重跑 3 個動作題 | 3/3 通過 | 修正後三個固定動作題可通過 | 不能取代完整批次的 1 次失敗 | `e3-agent-motion-v111-20260718.jsonl`；SHA-256 `2378d5…178` |
| E4-20260716 | DGX Spark；local qwen3.6:35b；固定快照 100 次 | 100/100 成功；median 68.80 s；P95 81.76 s | 本機模型的決策層端到端延遲基準 | 整體任務時間、雲端比較、即時控制能力 | `e4-local-formal-20260716.jsonl`；SHA-256 `8ebc45…c18` |
| B4-20260716 | Isaac Sim/Nav2 四點巡航 | 20.0 h；102 趟；407/408 waypoint 到達；0 安全事件；唯一 partial 的 goal 未送出 | 長時間模擬任務穩定性與誠實失敗 | 實車里程、統計上的零風險 | `/tmp/b4_mileage.log`、patrol reports、audit；摘要見 `V1_GATE` |
| A6-20260716 | daemon 24 h soak | 1439.6 min／2880 樣本；RSS +1.2%（門檻 20%）；PASS | daemon 在該 workload 的記憶體穩定性 | 任意 workload 或整個機器人 stack 的 24 h 穩定性 | `soak-20260715-012527/report.md`；SHA-256 `884345…492` |
| TUI-20260717 | Isaac Sim/Nav2 人工互動驗收 | 詳見逐項紀錄；四角補充預檢曾 3/4，左下由 G5 refer | 當日互動功能與誠實失敗行為 | 不取代 E1–E4／B4，不是實車驗證 | `TUI_LIVE_ACCEPTANCE_2026-07-17.md` |

雜湊在表內採前 6＋後 3 位方便閱讀；交付或投稿時需以 `sha256sum` 輸出完整值並與封存
artifact 一起保存。

## 尚未有證據的主張

| 主張 | 目前狀態 | 關閉方法 |
|---|---|---|
| Slash／自然語言提高 ROS2 開發效率 | 未量化 | 依 `USABILITY_STUDY` 比較成功率、時間、錯誤、查詢與介入 |
| 跨不同運動學載具的實體可移植性 | 僅介面設計 | 依 `VEHICLE_POC` 在第二種平台跑固定任務 |
| fresh machine 無人協助即可上手 | 安裝命令已補，尚無真人冷啟動資料 | 至少 5 位只看文件完成 doctor 與第一個任務 |
| 可在未知地圖自主探索 | 不支援 | 若未來做 frontier SLAM，另建風險與驗收，不得套用目前 `/explore` 證據 |

## 更新規則

1. 報告成功率時必須同時保留失敗分母；定向重跑只能作補充。
2. 模擬、隔離 fixture 與實體資料分開標示，不以「同 ROS2 API」混稱。
3. 模型、provider、commit、ROS domain、場景／地圖版本是正式實驗必要 metadata。
4. README 只放摘要並連回本表；論文表格若與本表不同，先修證據來源再修文字。
