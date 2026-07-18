# JenAI 命令規格

> 對應版本:v2.0.0(2026-07)。

JenAI 的命令分為兩層：
1. **CLI 命令**：在 shell 中直接執行，以 `JenAI` 開頭（裝了啟動器則用小寫 `jenai`）
2. **Slash 指令**：在 TUI/WebUI 輸入框中使用，以 `/` 開頭

---

## CLI 命令表

| 命令 | 說明 |
|---|---|
| `JenAI` | 主入口。首次使用進入 setup wizard，已設定則直接進 TUI |
| `JenAI help` | 一頁總覽：全部 CLI 命令 + 一鍵常用範例（TUI 內的 slash 指令用 `/help` 查） |
| `JenAI web` | 啟動 WebUI 監控中心（`--host` / `--port`，預設 127.0.0.1:8760）。**token 認證**常開（`--token` 可固定，否則每次重啟換新）；唯一例外 STOP 免認證。啟動時**列印所有可開方式**：本機網址、SSH 轉發指令；`--host 0.0.0.0` 時逐一列出各介面的區網網址（只印真的打得開的） |
| `JenAI mcp` | MCP stdio server：把機器人工具開放給 Claude Code/Desktop 等 client。預設唯讀，`--allow-actions` 才註冊 `navigate_to` |
| `JenAI daemon` | 常駐規則引擎：監看 topics 觸發規則（`--rules` 指定檔案，預設 `~/.config/jenai/rules.toml`） |
| `JenAI doctor` | 檢查 ROS2、provider、model、locations、環境（`--json` 機器可讀） |
| `JenAI onboard` | 備份目前 `config.toml` 後重跑 setup wizard;`.env`、locations、skills、reports、run history 全部保留。已有 config 時需確認,`--yes/-y` 可略過確認 |
| `JenAI data status` | **唯讀**盤點 sessions、pending runs、locations、reports、traces、audit、config 與 backups 的位置、檔數、大小、mode 與不安全／拒絕項；`--json` 提供機器可讀輸出，`--config PATH` 可指定設定檔 |
| `JenAI data harden` | 顯示 legacy operational data 的精確權限修復計畫（目錄 0700、檔案 0600），拒絕 symlink／hardlink 等不安全路徑；預設再詢問一次才執行，`--dry-run` 絕不改動，`--yes/-y` 只批准畫面所列計畫 |
| `JenAI data export <archive.tar.gz>` | 以 allow-list 匯出 operational data，排除 config、credentials 與 config backups，文字秘密遮罩後以 0600 原子寫入；既有目的檔預設拒絕覆寫，只有 `--force` 才原子替換，`--config PATH` 可指定設定檔 |
| `JenAI data prune` | 依 `--older-than-days N`（預設 30，最少 1）列出並刪除過期 session／pending／report 檔及 trace／audit rows；預設顯示計畫後再詢問，`--dry-run` 不刪除，`--yes/-y` 只批准該計畫 |
| `JenAI data purge` | 顯示精確永久刪除計畫；預設只清 operational state，保留 locations、config、`.env` credentials 與 config backups。納入保護類別須分別加 `--include-locations`、`--include-config`、`--include-credentials`、`--include-config-backups`；預設再詢問，`--dry-run` 不刪除，`--yes/-y` 只批准畫面所列計畫 |
| `JenAI config` | 顯示目前設定（JSON） |
| `JenAI providers` | 顯示 provider 清單 |
| `JenAI models` | 顯示 model 綁定 |
| `JenAI route "<text>"` | 非互動式 route 任務（互動確認後送出） |
| `JenAI scaffold "<描述>"` | **自然語言生成 ROS2 (ament_python) 套件**：確定性 boilerplate + LLM 寫的 node 主體（送出前確認）。**`--build`：寫完即 colcon build，失敗自動餵錯誤給 LLM 修一輪（生成即驗證）**；`--ws` 指定工作區 |
| `JenAI eval <scenarios.toml>` | **決策腦 E1 評測**(v0.21):跑場景庫(種子庫 `scenarios.example.toml`、正式庫 `scenarios.e1.toml`),輸出 per-family accuracy / refer rate / unsafe rate;標註支援 `action:target` 綁定目標且 gold 優先於 unsafe(v0.37);`--repeats/-k` 每場景重複數、`--json` 機器可讀 |
| `JenAI loc list` / `loc show <名>` | 非互動式 location 查詢 |
| `JenAI version` | 顯示版本資訊 |

`data harden／prune／purge` 不會因非互動環境而默認同意；要自動化必須明確使用 `--yes`，
而 `--yes` 也不會擴張當次畫面列出的範圍。完整資料分類、匯出排除項與解除安裝流程見
[DATA_LIFECYCLE](DATA_LIFECYCLE.md)。

### 關於 `JenAI` 主入口行為

```
JenAI 啟動流程：

1. 讀取 ~/.config/jenai/.env（API 金鑰;shell 已 export 者優先）
2. 讀取設定檔
   ├─ 找不到 or 不完整 → 進入 setup wizard
   └─ 完整
       ├─ 啟動健檢通過 → 進入 TUI 主畫面
       └─ config/provider 異常 → 提示執行 JenAI doctor
```

---

## Slash 指令總表

### Safety

| 指令 | 說明 | 範例 |
|---|---|---|
| `/stop` | **緊急停止**：取消 Nav2 goal + 連發零速度。**免批准**；任務執行中輸入也會搶佔，並清空等待佇列，避免舊移動意圖稍後執行 | `/stop` |

> 對應介面:WebUI 右上角紅色 **STOP** 鈕（免確認）、MCP `stop` 工具（唯讀模式也有）、daemon `action = "halt"` 規則。另有 bridge 端 watchdog:導航中 client 斷線/卡死超過 6 秒,bridge 自主停車。

### Session

| 指令 | 說明 | 範例 |
|---|---|---|
| `/help` | 顯示指令簡介、分類、範例與快捷鍵 | `/help` |
| `/status` | 顯示 provider、model、config、doctor 摘要 | `/status` |
| `/queue [clear]` | 顯示 FIFO 指令佇列;`clear` 清除等待項目。任務執行中也會立即回應，最多保留 20 項 | `/queue` |
| `/mode [approve\|plan\|auto]` | 切換權限模式(Shift+Tab 的鍵盤備援);不帶參數循環,中文別名 審批/規劃/自動 也通 | `/mode plan` |
| `/clear` | 清除目前對話畫面**與跨重啟記憶** | `/clear` |
| `/quit` / `/exit` | 離開 JenAI | `/quit` |

### Planning

| 指令 | 說明 | 範例 |
|---|---|---|
| `/plan <task>` | 規劃任務，不執行 side effects | `/plan 導航到機械系館並回報電量` |
| `/run <task>` | 執行任務：Supervisor agent 依需求 handoff 給 ROS/Motion/Navigation/Perception 專職 agent | `/run 帶我到應科大樓` |
| `/why` | 解釋 agent 目前決策原因 | `/why` |
| `/review` | 重新檢視目前 plan 並建議修改 | `/review` |
| `/abort` | 中止目前 run，接著執行下一個排隊項目 | `/abort` |

### Provider / Model

| 指令 | 說明 | 範例 |
|---|---|---|
| `/provider [名]` | 顯示/切換 active provider（含編號快選），即時生效並持久化 | `/provider local` |
| `/providers` | 列出所有 provider profiles | `/providers` |
| `/model [名\|編號]` | 列出端點上真實可用的模型（含 Ollama）並切換 chat 綁定 | `/model qwen3.6:35b` 或 `/model 2` |
| `/models` | 顯示 model 綁定（chat/plan/vision/route/default） | `/models` |
| `/permissions` | 顯示哪些指令需要批准 | `/permissions` |
| `/config` | 顯示 config 檔重點 | `/config` |
| `/doctor` | 在 TUI 內跑健檢 | `/doctor` |

### ROS2

| 指令 | 說明 | 範例 |
|---|---|---|
| `/ros topics` | 列出目前 ROS2 graph 中的 topics（含類型提示） | `/ros topics` |
| `/ros topic-info <topic>` | 查詢 topic 的 type、publishers、subscribers | `/ros topic-info /cmd_vel` |
| `/ros schema <topic>` | 解析 message type 並以人話摘要欄位 | `/ros schema /cmd_vel` |
| `/ros echo <topic> [count]` | 擷取 N 筆訊息快照（snapshot 模式） | `/ros echo /scan 3` |
| `/ros pub <topic> <payload>` | 向 topic 發送訊息（需批准；速度過 `[vehicle]` 硬限速） | `/ros pub /cmd_vel {"linear":{"x":0.5}}` |
| `/ros drive <topic> <payload> [秒]` | 定頻發布 N 秒後自動送 0 停車（需批准） | `/ros drive /cmd_vel {"linear":{"x":0.5}} 2` |

### Route / 地點

| 指令 | 說明 | 範例 |
|---|---|---|
| `/route <text>` | 自然語言路由（需批准）。**「從 A 到 B」兩端都是已知地點 → 依序先去 A 再去 B**（兩段導航逐段回報）;只認得目的地 → 從當前位置導航過去。即時剩餘距離、Esc 真取消。`route_adapter=odom` + `[avoidance]` 時,直驅會用 depth 反應式繞開障礙 | `/route 從應科大樓到機械系館` |
| `/drive <自然語言>` | 說人話控車：解析成速度指令後定時發布（需批准；發到 `vehicle.cmd_vel_topic`） | `/drive 前進兩秒` |
| `/loc list` | 列出所有地點 | `/loc list` |
| `/loc show <名>` | 顯示地點詳細資料 | `/loc show 應科大樓` |
| `/loc add here <名>` | **抓機器人當下位置**存成地點（讀 /amcl_pose，退回 /odom） | `/loc add here 走廊測試點` |
| `/loc add gps <名> <緯> <經>` | **GPS 經緯度**存成地點：經 `[map_datum]`（map 原點經緯度 + yaw_deg）換算成 map 座標；未設基準點時誠實拒絕並給設定教學 | `/loc add gps 機械系館 24.1201 120.6773` |
| `/loc move <名>` | 把**既有地點**更新為機器人目前位置（改座標＝重新站位再存） | `/loc move dock` |
| `/loc rename <舊> <新>` | 改名（名稱含空白用 `舊名 -> 新名`）；撞名/撞 alias 誠實拒絕 | `/loc rename map_right_up 右上角` |
| `/loc rm <名>` | 刪除地點（**精確名稱**才刪，alias 與模糊比對不觸發刪除） | `/loc rm 走廊測試點` |

### Skills（任務技能）

| 指令 | 說明 | 範例 |
|---|---|---|
| `/mission <地點, …>` | 多步任務：依序導航/移動各點並回報（批准一次跑整趟；`drive <動作>` 段落可混排） | `/mission 廚房, drive 左轉, 大廳` |
| `/patrol <地點, …> [xN] [photo]` | **循環巡邏**：點位 × 圈數；`photo` 時每個到達點抓相機幀給 VLM 並即時回報觀察。一點失敗記錄後續行 | `/patrol A, B x3 photo` |
| `/explore [時間] [goals=N] [failures=N] [tag=標籤] [photo] [seed=N]` | **有界隨機巡遊**：在已儲存點位中，隨機選擇目前造訪次數最少者；同一趟不重試失敗點。預設 5 分鐘／最多 8 個目標／連續失敗 2 次即停。排除 dock 與 `restricted`、`hazard`、`no-explore` 標籤。這是已知點位巡遊，不是未知地圖的 frontier SLAM | `/explore 5m goals=8 tag=room photo` |
| `/dock` | 回充：導航到 `tags = ["dock"]` 的地點（名字/別名是 Dock、充電站也認得） | `/dock` |
| `/report` | 顯示最近一次巡邏日報（確定性內容 + LLM 摘要段；provider 離線時誠實只給前者）。log 存 `<config 目錄>/reports/`，patrol 結束自動寫入 | `/report`、`/report list` |
| `/skills` | 列出**檔案定義技能**：`<config 目錄>/skills/*.toml`（name/description/steps=/mission 語法）→ 重啟後 `/名稱` 即新指令，進 palette、走同一張批准卡；保留字（stop/route…）拒載。範例見 repo 根目錄 `skills.example.toml` | `/skills`、`/inspect` |

### Vision

| 指令 | 說明 | 範例 |
|---|---|---|
| `/vision image <path>` | 分析本機圖片並輸出結構化觀察 | `/vision image /tmp/scene.jpg` |
| `/vision camera [topic]` | 抓一張相機幀給 VLM 分析；不帶 topic 用 `vehicle.camera_topic` | `/vision camera /rgb` |
| `/perception start [topic] [hz]` | **持續感知迴圈**：定頻抓幀 → VLM 結構化分析（場景/物件/affordances/建議動作）。只觀察不動作；建議動作標示「需批准」 | `/perception start /rgb 1` |
| `/perception stop` | 停止感知迴圈並回報分析幀數 | `/perception stop` |

### System

| 指令 | 說明 | 範例 |
|---|---|---|
| `/shell <cmd>` | 執行 shell 命令（需批准） | `/shell ls -la /var/log` |
| `!<cmd>` | Bash 模式：以 `!` 開頭直接當 `/shell` 執行（仍需批准） | `!ls -la` |

---

## 鍵盤快捷鍵

| 按鍵 | 功能 |
|---|---|
| `Enter` | 送出輸入;忙碌時自動加入 FIFO 佇列 / 選定 approval 目前選項 |
| `Shift+Tab` | **切換權限模式**:⏵ 審批(NL→agent 執行,動作過批准卡)→ ⏸ 規劃(只規劃教學,零執行)→ ⏩ 自動(有界、非 host 的 P0/P1 可自動批准;HOST_COMMAND/P2 仍逐次詢問;急停/限速/閘門仍有效)。目前模式顯示在底部狀態列。**終端不支援 Shift+Tab 時用 `/mode`**(不帶參數循環;`/mode approve|plan|auto` 直接指定,中文別名 審批/規劃/自動 也通) |
| `!` | 以 `!` 開頭 → 該行當 shell 命令執行 |
| `Esc` | 中斷目前任務並繼續下一個排隊項目（**Nav2 goal 真的會取消**）/ 拒絕 approval / 關閉 palette |
| `1` `2` `3` | 直接選畫面上存在的 approval 選項；HOST_COMMAND/P2 為一次性 Yes/No，P2 預選 No |
| `Tab` | 補全命令名稱或模板 |
| `↑` `↓` | 歷史輸入、slash palette 選項、或 approval 選項 |

---

## 需批准的指令（Approval Required）

以下指令在執行前一律顯示 approval card：

- `/ros pub`、`/ros drive`、`/drive`
- `/route`、`/mission`、`/patrol`、`/explore`、`/dock`
- `/shell`（含 `!` bash 模式）
- `/run` 內部觸發的任何 side-effect tool

**例外:`/stop` 永遠不需批准** —— 停下來永遠是安全的。

Approval card 為 Claude Code 風格的**編號選項**，可用 `↑/↓`+`Enter` 或直接按畫面上的數字鍵：

- 有界、非 host 的 P0/P1：`Yes`／`Yes, and remember...`／`No`。
- `HOST_COMMAND` 或 P2：只有一次性的 `Yes`／`No`，不能記住；P2 預選 `No`。
- `auto` 模式與既有 remembered tool 都不能繞過 `HOST_COMMAND`／P2。
- `Esc` 永遠等同拒絕；`/stop` 是唯一免批准的致動相關例外。

---

## daemon 規則動作（rules.toml）

| action | 批准需求 | 行為 |
|---|---|---|
| `"notify"`（預設） | 無 | 只通報，不動作 |
| `"halt"` | **無**（停車永遠安全） | 緊急停止：取消進行中導航 + 零速度 |
| `"goto <地點>"` | `auto_approve = true` **且** `route_adapter = "nav2"` | 導航到地點；條件不足時印出「本來會做什麼」 |

### 感知規則（`topic = "@perception"`）

規則的觸發條件除了數值閾值（below/above/equals），也可以是相機 VLM 的 **affordance**：

```toml
[[rules]]
name = "blocked-path-notify"
topic = "@perception"           # daemon 會啟動相機→VLM 迴圈（頻率 = throttle_s）
affordance = "path_blocked"     # SceneAnalysis.affordances 含此字串時觸發
min_confidence = 0.6            # 低信心的辨識不觸發
action = "notify"               # 動作 gating 與數值規則完全相同，無捷徑
```
