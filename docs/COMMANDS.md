# JenAI 命令規格

JenAI 的命令分為兩層：
1. **CLI 命令**：在 shell 中直接執行，以 `JenAI` 開頭
2. **Slash 指令**：在 TUI/WebUI 輸入框中使用，以 `/` 開頭

---

## CLI 命令表

| 命令 | 說明 |
|---|---|
| `JenAI` | 主入口。首次使用進入 setup wizard，已設定則直接進 TUI |
| `JenAI web` | 啟動 WebUI 監控中心 |
| `JenAI doctor` | 檢查 ROS2、provider、model、vision、locations、環境 |
| `JenAI config` | 顯示或編輯設定 |
| `JenAI providers` | 顯示 provider 清單與健康狀態 |
| `JenAI models` | 顯示已配置模型與能力綁定 |
| `JenAI route "<text>"` | 非互動式 route 任務 |
| `JenAI loc <subcommand>` | 非互動式 location 管理 |
| `JenAI version` | 顯示版本資訊 |

### 關於 `JenAI` 主入口行為

```
JenAI 啟動流程：

1. 讀取設定檔
   ├─ 找不到 or 不完整 → 進入 setup wizard
   └─ 完整
       ├─ provider/model 可用 → 進入 TUI 主畫面
       └─ 環境異常 → 提示執行 JenAI doctor
```

---

## Slash 指令總表

### Session

| 指令 | 說明 | 範例 |
|---|---|---|
| `/help` | 顯示指令簡介、分類、範例與快捷鍵 | `/help` or `/help ros` |
| `/status` | 顯示目前 session 狀態、provider、model、ROS 連線 | `/status` |
| `/clear` | 清除目前對話畫面 | `/clear` |
| `/compact` | 壓縮對話歷史以節省 context（🚧 規劃中，v0.1.0 未實作） | `/compact` |
| `/theme` | 切換顯示主題（🚧 規劃中，v0.1.0 未實作） | `/theme dark` |
| `/resume` | 恢復上一個被中斷的 run（🚧 規劃中，v0.1.0 未實作） | `/resume` |

### Planning

| 指令 | 說明 | 範例 |
|---|---|---|
| `/plan <task>` | 規劃任務，不執行 side effects | `/plan 導航到機械系館並回報電量` |
| `/run <task>` | 執行任務：Supervisor agent 依需求 handoff 給 ROS/Motion/Navigation/Perception 專職 agent（多-agent） | `/run 帶我到應科大樓` |
| `/why` | 解釋 agent 目前決策原因 | `/why` |
| `/review` | 重新檢視目前 plan 並建議修改 | `/review` |
| `/abort` | 中止目前 run | `/abort` |

### Provider / Model

| 指令 | 說明 | 範例 |
|---|---|---|
| `/provider` | 顯示目前使用的 provider | `/provider` |
| `/model` | 顯示目前使用的 model | `/model` |
| `/models` | 列出所有可用 model | `/models` |
| `/permissions` | 顯示目前工具權限設定 | `/permissions` |

### ROS2

| 指令 | 說明 | 範例 |
|---|---|---|
| `/ros topics` | 列出目前 ROS2 graph 中的 topics | `/ros topics` |
| `/ros topic-info <topic>` | 查詢 topic 的 type、publishers、subscribers | `/ros topic-info /cmd_vel` |
| `/ros schema <topic>` | 解析 topic message type 並以人話摘要欄位 | `/ros schema /cmd_vel` |
| `/ros echo <topic>` | 即時監看 topic 訊息流 | `/ros echo /scan` |
| `/ros pub <topic> <payload>` | 向 topic 發送訊息（需批准） | `/ros pub /cmd_vel {"linear":{"x":0.5}}` |
| `/ros drive <topic> <payload> [秒]` | 定頻發布 N 秒後自動送 0 停車（需批准） | `/ros drive /cmd_vel {"linear":{"x":0.5}} 2` |
| `/ros state` | 觀察機器人當下狀態（/odom + /scan 快照，閉環感知） | `/ros state` |
| `/ros graph` | 顯示 node/topic 連線關係圖（🚧 規劃中，未實作） | `/ros graph` |

> `/ros echo` 目前為 snapshot 模式：擷取 N 筆訊息（`/ros echo <topic> [count]`）後結束，尚未支援連續 streaming。

### Route

| 指令 | 說明 | 範例 |
|---|---|---|
| `/route <text>` | 自然語言路由，解析起終點並送出導航（需批准） | `/route 從應科大樓到機械系館` |
| `/drive <自然語言>` | 說人話控車：解析成速度指令後定時發布（需批准） | `/drive 前進兩秒` |
| `/mission <地點, …>` | 多步巡邏任務：依序前進/導航各點並回報結果（需批准一次） | `/mission 廚房, drive 左轉, 大廳` |
| `/loc list` | 列出所有可用地點 | `/loc list` |
| `/loc show <name>` | 顯示特定地點詳細資料 | `/loc show 應科大樓` |
| `/loc add` | 新增地點（互動式）（🚧 規劃中，v0.1.0 未實作） | `/loc add` |

### Vision

| 指令 | 說明 | 範例 |
|---|---|---|
| `/vision image <path>` | 分析圖片並輸出結構化觀察 | `/vision image /tmp/scene.jpg` |
| `/vision camera` | 擷取目前 camera topic 並分析（🚧 規劃中，v0.1.0 未實作） | `/vision camera` |

### System

| 指令 | 說明 | 範例 |
|---|---|---|
| `/shell <cmd>` | 執行 shell 命令（需批准） | `/shell ls -la /var/log` |
| `!<cmd>` | Bash 模式：以 `!` 開頭的輸入直接當 `/shell` 執行（仍需批准） | `!ls -la` |

---

## 鍵盤快捷鍵

| 按鍵 | 功能 |
|---|---|
| `Enter` | 送出輸入 / 選定 approval 目前選項 |
| `!` | 以 `!` 開頭 → 該行當 shell 命令執行 |
| `Esc` | 中斷執行中的任務 / 拒絕 approval / 關閉 palette |
| `1` `2` `3` | 直接選 approval 選項（Yes / Yes 並記住 / No） |
| `Tab` | 補全命令名稱或模板 |
| `↑` `↓` | 歷史輸入、slash palette 選項、或 approval 選項 |

---

## 需批准的指令（Approval Required）

以下指令在執行前一律進入 `awaiting_approval` 狀態，顯示 approval card：

- `/ros pub`
- `/route`
- `/shell`（含 `!` bash 模式）
- `/run` 內部觸發的任何 side-effect tool

Approval card 為 Claude Code 風格的**編號選項**，可用 `↑/↓`+`Enter` 或直接按數字鍵：

1. **Yes** — 批准這一次
2. **Yes, and don't ask again this session** — 批准並在本 session 自動核准同類指令
3. **No**（或 `Esc`）— 拒絕，並可告訴 JenAI 改用別的做法

