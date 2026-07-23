# JenAI UX 規格

> **現行規格（2026-07-18）**：對應已實作並通過寬／窄終端測試的 TUI；外觀變更須先以獨立樣本取得使用者確認。


## 設計原則

- **Terminal-first**：TUI 是主要操作介面，WebUI 是監控輔助介面
- **折疊優先**：細節預設折疊，主畫面保持乾淨
- **批准可見**：所有敏感操作都有清楚的 approval card，不隱藏
- **進度透明**：run 的每個階段都有可見狀態

---

## TUI 規格

### 主畫面佈局（Claude Code 風格）

無頂端 header bar。寬終端的歡迎區採 40/60 雙欄：左欄顯示問候、會動的臘腸狗、
產品名稱與 provider/model/path，右欄顯示 Quick start 與本 session 最近兩筆輸入。寬度小於 92
columns 時折成單欄並隱藏右欄；小於 56 columns 時再隱藏吉祥物與產品副標。

```text
╭─ JenAI v1.x ─────────────────────────────────────────────────────╮
│  Welcome back!                  Quick start                       │
│       (動態像素臘腸狗)          /help       Learn JenAI commands │
│  Robot decision agent           /doctor     Check ROS2 setup      │
│  qwen3.6:35b · ollama           /run        Execute a task        │
│  ~/JenAI                         ────────────────────────────────  │
│                                 Recent activity                  │
╰──────────────────────────────────────────────────────────────────╯

❯ /route 從應科大樓到機械系館
● 解析起終點並產生路徑…
● route_execute_tool (從應科大樓到機械系館)
  ⎿ Route from 應科大樓 to 機械系館.

──────────────────────────────────────────────────────────────────
⚠ Send navigation route
/route 從應科大樓到機械系館
Send a navigation goal to the route adapter.
May move the connected robot or simulator.

Do you want to proceed?
❯ 1. Yes
  2. Yes, and remember this tool for this session
  3. No
──────────────────────────────────────────────────────────────────
✻ Running… (8s · esc to interrupt)
──────────────────────────────────────────────────────────────────
❯ Ask JenAI, / for commands, ! for shell
──────────────────────────────────────────────────────────────────
approve · ollama                              qwen3.6:35b · ~/JenAI
```

### 視覺設計

- 配色：深色背景、低彩文字、橘色結構線；保留品牌色但避免每段都做卡片
- 轉錄格式：使用者為 `❯`，事件為 `●`，結果／細節以 `⎿` 縮排
- 批准區：全寬、僅上下分隔線，不用圓角卡片或左側 accent 條
- composer：上下水平線包住無邊框輸入；狀態列左右分組
- slash palette：無外框卡片，只留上分隔線，選取列使用 `❯`
- 狀態圖示：`●`（項目）`⎿`（結果）`⚠`（批准）`✻`（執行中）

---

## Slash Command Palette

### 觸發條件
- 輸入框第一個字元為 `/` 時自動開啟
- 刪掉 `/` 後立即關閉

### 顯示格式（每列）
```text
❯ /ros schema     Summarize a ROS2 topic's message schema
```

palette 顯示命令名與一句說明；補全只填入命令名，參數格式另以灰色唯讀 hint 顯示，不把 placeholder 塞進 composer。

### 鍵盤行為

| 按鍵 | 行為 |
|---|---|
| Tab | 補全目前高亮的完整命令名，尾端保留空格並顯示參數 hint |
| Enter | 輸入仍是部分命令時先補全；已是完整命令／含參數時才提交 |
| Esc | 關閉 palette 並把焦點留在 composer |
| ↑/↓ | palette 開啟時移動選項；關閉時瀏覽 session history |

### 捲動顯示全部
- palette 一次顯示一個視窗（12 列），會隨選取捲動，頂部顯示 `(目前/總數)`
  與 `↑ N more / ↓ N more`，所有符合的指令都可用 `↑/↓` 瀏覽到，不再硬性截斷。

---

## Tab 補全

現行 palette 把多字命令視為完整命令名，例如 /ros topics、/loc add。輸入前綴後用
Tab 或 Enter 只補成命令名與尾端空格；需要的參數格式顯示於 palette hint，使用者直接
輸入真值，不必先刪除自動插入的 <placeholder>。

現行範圍：

1. 命令名與多字子命令前綴篩選。
2. 全部符合項可用 ↑/↓ 瀏覽，Tab／Enter 補全選中項。
3. 參數格式 hint；不插入模板。
4. topic／location 動態值補全尚未實作，仍由 /ros topics 與 /loc list 查詢。

## 歷史輸入

| 行為 | 說明 |
|---|---|
| ↑ | palette 關閉且 composer 聚焦時取得上一筆 session 輸入 |
| ↓ | 取得下一筆；超過最新一筆後回到空白 |
| 即時輸入 | 重新設定 history cursor；目前沒有前綴過濾 |
| 輸入型態 | Textual 單行 Input，沒有多行游標模式 |
| Session 範圍 | 本次 TUI session；對話記憶另由 session 檔管理 |


---

## Approval Card 規格

Approval card 出現時取得鍵盤焦點，採 Claude Code 風格的**編號選項**。有界、非 host 的 P0/P1 可顯示：

```
❯ 1. Yes
  2. Yes, and remember this tool for this session
  3. No
```

`HOST_COMMAND` 或 P2 僅顯示一次性的 `Yes`／`No`；P2、`HOST_COMMAND` 與機器人控制預選 `No`。`↑/↓` 移動、`Enter` 選定，
或直接按畫面上存在的數字鍵；`Esc` 永遠拒絕。auto mode 與 session remember 都不可跳過
HOST_COMMAND／P2，因此自然語言 agent 與直接 `/shell` 走相同邊界。

必顯示欄位：
- title（操作名稱）
- raw_action（原始指令／payload）
- summary（自然語言說明）
- 由 risk_level + effect_scope 轉成的一句白話影響說明
- 問句、依風險產生的兩或三個編號選項與鍵盤提示
## 底部狀態列與執行指示器

- **狀態列**：左側顯示 permission mode 與 provider，右側顯示 model 與 cwd
- **Spinner**：執行任務時輸入框上方顯示 `✻ <Planning/Running/Thinking…> (Ns · esc to interrupt)`
- **Esc 中斷**：執行中按 `Esc` 取消目前任務並回到輸入
- **! Bash 模式**：以 `!` 開頭的輸入直接當 `/shell` 執行（仍需批准）

## Inline Hint（🚧 規劃中）

當輸入框包含已知命令前綴時，於下方顯示淡色提示行（v0.1.0 尚未實作；
目前以 slash palette 的命令說明取代）：

```
❯ /ros schema
  hint: /ros schema <topic>  例: /ros schema /cmd_vel
```

---

## Block 類型（現行實作）

TUI 對話區中的所有輸出，統一以 `●` 項目符號時間軸呈現：

| Block 類型 | 說明 |
|---|---|
| `TimelineItem` | 單行 `●` 事件（提示、成功、警告、錯誤、助理回覆） |
| `OutputPanel` | `●` 標題 + `⎿` 縮排內容（ROS/vision/loc/摘要等一般輸出共用） |
| `PlanBlock` | 顯示 plan steps（`●` + 各步驟 `⎿`） |
| `ToolBlock` | 顯示 tool call：`● tool(args)` + `⎿ result` |
| `ApprovalCard` | 等待批准的操作，編號選項 |
| `ErrorBlock` | 錯誤訊息 + 修復建議 |

> ROS / Vision / Summary 目前共用 `OutputPanel`，未拆成獨立 block widget。

---

## /help 輸出結構

```
JenAI — ROS2 AI Agent Terminal

What can I do?
  - 規劃並執行機器人任務（/plan, /run）
  - 探索 ROS2 topics 與訊息結構（/ros）
  - 自然語言路由到目標位置（/route）
  - 分析圖片場景（/vision）
  - 執行 shell 命令（/shell）

Command Groups:
  Session   /help /status /clear /compact /resume
  Planning  /plan /run /why /review /abort
  ROS2      /ros topics /ros schema /ros echo /ros pub
  Route     /route /loc list /loc show
  Vision    /vision image
  System    /shell /permissions

Examples:
  /plan 巡邏 A 到 D 區域並記錄異常
  /ros schema /cmd_vel
  /route 從應科大樓到機械系館
  /vision image /tmp/photo.jpg

Keyboard:
  Enter 送出/選定   ! shell   Esc 中斷/拒絕   數字鍵 approval   Tab 補全   ↑↓ 歷史/選單
```

---

## WebUI 規格（概要）

WebUI 是監控中心，不是主要操作介面。

### 必備面板
- Session transcript（對話紀錄）
- Current run state（即時狀態）
- Approval queue（待批准列表）
- Tool call timeline（執行時間軸）
- ROS explorer（topics / graph）
- Vision result viewer
- Settings / Provider health

### TUI vs WebUI 分工

| 功能 | TUI | WebUI |
|---|---|---|
| 輸入命令 | ✅ 主要 | ✅ 輔助 |
| 批准操作 | ✅ | ✅ |
| 監控 run 狀態 | 折疊顯示 | 完整 timeline |
| 查看 tool call 細節 | 折疊 | 展開視覺化 |
| Vision 結果 | 文字摘要 | 圖文並排 |
| Settings | 基本 | 完整 |
