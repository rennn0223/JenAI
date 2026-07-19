# EVIDENCE_LEDGER — 對外主張的單一證據表

> 本表是 README、論文、簡報與銷售說法的數字來源。原始資料留本機且由 SHA-256
> 辨識；歷史 release notes 不回寫。新結果若取代舊結果，必須新增一列並說明原因，不能
> 覆蓋失敗樣本。

## 實驗版本追溯矩陣

下表的 revision 只接受原始 artifact 內保存的完整 Git revision；檔名中的 `v111`、執行
日期、當時附近的 tag 或事後使用的工作樹均不能代替 execution revision。`not recorded`
表示現有資料無法證明，不以合理推測補值。

| 實驗／資料角色 | Execution revision | Analysis revision | 觀測型態與可追溯限制 |
|---|---|---|---|
| E1-20260716 | `not recorded` | `not recorded` | 64 次模型輸出為 observed；artifact 保存結果與摘要，但未保存 Git revision |
| E2-20260715c（C） | `not recorded` | `8c53aef0a031cca89b5bd74b9c6c426523e3abe9` | C 是舊 full-twin live run 的 observed 結果；analysis revision 只證明後續重建程式版本，不回填 C 的執行版本 |
| E2-PAIR-20260716（A／B） | 不適用（未 live 執行） | `8c53aef0a031cca89b5bd74b9c6c426523e3abe9` | A／B 是對相同 targets 套用決定性政策得到的 derived counterfactual rows |
| E3-20260718／E3-D2-20260718 | `not recorded` | `not recorded` | meta 保存 domain 42、provider、model 與 cases，但沒有 Git revision；檔名不能補足此欄 |
| E4-20260716 | `not recorded` | `not recorded` | observed latency rows與 meta 保存 run/model/snapshot，但沒有 Git revision |
| B4-20260716 | `not recorded` | `not recorded` | 102 份 patrol reports 可由本機固定的事後 subset manifest 重建；report schema 沒有 revision／run ID／獨立事件標註 |
| HIL-FS-20260719 | `fb5645620c787bd54fc8368fe402366371561b1e` | 不適用 | clean source 的 prospective Isaac Sim/Nav2 live run；artifact 直接保存 revision、dirty=false、必要 preflight、scan 品質、route 與 cancel/stop 證據 |

### Protocol-specific preflight

歷史 artifact 未保存各場次的完整 `doctor --json`，故不得寫成「所有 doctor 項目均通過」。
重跑時依實驗依賴採下列 gate；不相關檢查不得把場次誤判為失敗，相關檢查也不得省略：

| Protocol | 必要 gate | 不作為該 protocol 的通過條件 | 歷史 preflight 證據 |
|---|---|---|---|
| E1／E4 | Python/config、provider、實際 model binding、artifact 輸出可寫 | ROS2、Nav2、Twin | `not recorded` |
| E2 | ROS2、Nav2、map/pose、Isaac Sim/Twin Gate 與判準所需 topics | 純模擬 domain 0 場次的跨 domain `twin_isolation` | `not recorded` |
| E3 | provider/model、`ROS_DOMAIN_ID=42`、mock `/cmd_vel`／`/odom` fixture | Nav2、Isaac Sim/Twin | meta 證明 domain/model；完整 doctor JSON `not recorded` |
| B4 | ROS2、Nav2、map/pose、四個 route locations、模擬器 Play 與背景 driver 清查 | 純模擬 domain 0 場次的跨 domain `twin_isolation` | `not recorded` |
| Isaac HIL | ROS2、Nav2、map/pose、`/scan` 品質、cmd_vel controller、合法起點；live run 另須雙重 opt-in | 未要求 Twin 時的跨 domain `twin_isolation` | HIL-FS-20260719 保存所有必要 gate；Twin 同 domain 0 明記 `skip` |

純模擬同 domain 0 只在實體載具關機、有人監督的開發條件下使用；這不構成已驗證的虛實
通訊隔離。跨 domain 隔離是虛實並存部署要求，須由另一份 protocol 與 artifact 驗收。


## 已接受基準

| ID | 範圍／版本 | 結果 | 可支持的主張 | 不支持的主張 | 原始 artifact（本機） |
|---|---|---|---|---|---|
| E1-20260716 | qwen3.6:35b；64 個有界決策情境 | accuracy 54/64 = 84.4%；unsafe 4/64 = 6.25%；refer 32/64 = 50.0% | 決策層可量測，且錯誤需要 HITL／Twin／執行器多層邊界 | LLM 本身已安全、可直接做低階控制 | `e1-20260716.json`；SHA-256 `82a7cf…16d` |
| E2-20260715c | Isaac Sim/Nav2；full-twin 前導；5 類各 20，N=100 | 硬陷阱 60/60 block/refer；正常 20/20 pass；zone crossing 6 block／2 refer／12 pass（路徑繞開禁區） | Twin Gate 能攔截該固定場景集的界外、不可達與禁區終點；正常點無誤攔 | A/B/C 完整消融、未知場景泛化、實車碰撞保證 | `e2-20260715c/{targets.json,trials.jsonl}`；trials SHA-256 `332463…069` |
| E2-PAIR-20260716 | `e2-20260715c-paired-reanalysis`；舊 C observed 結果配對 A／B 決定性 derived 政策；100 targets 中預先標為 `zone_crossing` 的 20 組全列探索性，不進主要描述分母 | 主要描述 subset 80 組／240 rows：60 組困難案例介入 A=0、B=20、C=60；20 組 normal 三政策皆 0 誤介入；A／B derived、C observed | 描述同一固定目標集下三個政策輸出的差異，並保留舊 C live 結果 | 非前瞻性三條件 live run；A／B 非獨立觀測，故 Cochran’s Q／McNemar p 值不作確認性推論；不支持 `zone_crossing`、未知場景泛化或實體安全結論 | `run.json` SHA-256 `6a9e4b…08e`；`trials.jsonl` `055f64…f6e`；`targets.json` `f71def…450` |
| E3-20260718 | ROS_DOMAIN_ID=42 fixture；qwen3.6:35b；完整 8 題 | 7/8 通過；8/8 無重複致動；唯一失敗在致動前呼叫不存在工具 | 自然語言可做 live graph 發現、一次有界動作與回授驗證；失敗不盲目重送 | 每次 prompt 都成功、domain 0 實體或 Isaac 結果 | `e3-agent-boundary-v111-20260718.jsonl`；SHA-256 `2285dc…937` |
| E3-D2-20260718 | 同上；只重跑 3 個動作題 | 3/3 通過 | 修正後三個固定動作題可通過 | 不能取代完整批次的 1 次失敗 | `e3-agent-motion-v111-20260718.jsonl`；SHA-256 `2378d5…178` |
| E4-20260716 | DGX Spark；local qwen3.6:35b；單一固定快照 100 次 | 100/100 呼叫回傳可解析的 `navigate_to`；median 68.80 s；P95 81.76 s | 此一模型、provider、快照與序列負載下，成功決策呼叫的端到端延遲描述 | 任務執行成功、不同 prompts/actions/models、並發負載、雲端比較或任何即時控制結論 | `e4-local-formal-20260716.jsonl`；SHA-256 `8ebc45…c18`；meta 未記 Git revision |
| B4-20260716 | Isaac Sim/Nav2；相同四點 route 的 102 份 patrol reports；事後重建 selection window | 101/102 reports 為 4/4、1/102 為 3/4；407/408 waypoint `succeeded`；唯一 `unavailable` 明記 goal 未送出 | 固定 subset 的模擬導航任務完成紀錄與誠實失敗 | 精確 20 h 暴露量、實車里程、安全事件為零或事件風險；reports 無專用 incident 欄且無獨立觀察者 | 本機 `b4-20260716-subset-manifest.json` SHA-256 `b79717…7f7`；report-set SHA-256 `070591…f7c`（不進 GitHub） |
| A6-20260716 | daemon 24 h soak | 1439.6 min／2880 樣本；RSS +1.2%（門檻 20%）；PASS | daemon 在該 workload 的記憶體穩定性 | 任意 workload 或整個機器人 stack 的 24 h 穩定性 | `soak-20260715-012527/report.md`；SHA-256 `884345…492` |
| TUI-20260717 | Isaac Sim/Nav2 人工互動驗收 | 詳見逐項紀錄；四角補充預檢曾 3/4，左下由 G5 refer | 當日互動功能與誠實失敗行為 | 不取代 E1–E4／B4，不是實車驗證 | `TUI_LIVE_ACCEPTANCE_2026-07-17.md` |
| TUI-R2-20260718 | Isaac Sim/Nav2；approve TUI；同 session NL follow-up | 修正前 1 次 `loc_lookup_tool` not found；修後 follow-up 到批准卡且拒絕後未移動；Esc session marker 通過；成功導航 0 次 | 導航 fallback、批准／拒絕與中斷後誠實收尾 | 不支持 Hero 10-run、成功導航、Twin 隔離或實體安全 | `TUI_LIVE_ACCEPTANCE_2026-07-18.md`；本機 trace IDs 詳見該檔 |
| HIL-PF-20260718 | Isaac Sim/Nav2 domain 0；唯讀 production bridge 起點檢查 | ROS2/map/AMCL/scan/Nav2/cmd_vel 全 pass；AMCL `(-7.16,-9.48)` 命中 `SW-narrow-aisle`，overall fail，0 goal sent；revision `073de89…a90`、dirty=true | preflight 可在執行前拒絕禁區起點並保存實際位姿與來源狀態 | 成功 route、cancel/stop、Twin 隔離或實體安全 | 本地 `isaac-hil-preflight-start-guard-20260718.json`；SHA-256 `7fef28…392`（不進 GitHub） |
| HIL-FS-20260719 | Isaac Sim 5.1.0-rc.19／ROS2 Jazzy／Nav2；domain 0；Dock `(-6,-1,π)`；clean `fb56456…b1e` | `pass_with_skips`；scan 10/10、0 blank、finite-bin 53.7569%（門檻 ≥25%）；`map_left_down` 66.985 s、Dock 46.754 s，皆 0 recoveries；cancel propagated、停止漂移 0.0000 m | 此固定環境的 production route、cancel/halt 與送 goal 前 scan 品質 gate 可被 artifact 稽核 | Twin 隔離／verdict（同 domain 明記 skip）、長時間成功率、實體或跨載具安全／泛化 | 本機 `isaac-hil-live-fullscan-guard-fb56456-20260719.json`；SHA-256 `51b3a7…d00`（不進 GitHub）；詳見 `TUI_LIVE_ACCEPTANCE_2026-07-19.md` |

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
   舊 artifact 缺欄位時必須寫 `not recorded`，不得由檔名、日期或鄰近 tag 回填。
4. README 只放摘要並連回本表；論文表格若與本表不同，先修證據來源再修文字。
