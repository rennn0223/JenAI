# JenAI UX 規格

> 📜 **設計期文件**(v0.1 規劃階段)。實際實作已演進,現況以 [TECHNICAL_GUIDE.md](TECHNICAL_GUIDE.md)、[COMMANDS.md](COMMANDS.md) 與程式碼為準;方向與 roadmap 見 [PROJECT_DIRECTION.md](PROJECT_DIRECTION.md)。


## 設計原則

- **Terminal-first**：TUI 是主要操作介面，WebUI 是監控輔助介面
- **折疊優先**：細節預設折疊，主畫面保持乾淨
- **批准可見**：所有敏感操作都有清楚的 approval card，不隱藏
- **進度透明**：run 的每個階段都有可見狀態

---

## TUI 規格

### 主畫面佈局（v0.1.0，Claude Code 風格）

無頂端 header bar；最上方是橘色 hero 歡迎卡，其下是 `⏺` 項目符號時間軸，
底部固定一條輸入框 + 狀態列，執行中會插入 spinner。

```
╭─ JenAI v0.1.0 ───────────────────────────────────────╮  ← 橘色 hero 卡
│  Robot workflow console        Ready for ROS2 work    │
│      (像素狗)                  Plan / inspect / route  │
│  qwen3.6:35b · ollama          Doctor: pass           │
╰───────────────────────────────────────────────────────╯

> /route 從應科大樓到機械系館                              ← 使用者輸入（> 前綴）
⏺ 解析起終點並產生路徑…
⏺ route_execute_tool (從應科大樓到機械系館)
  ⎿ Route from 應科大樓 to 機械系館.

⚠ Send navigation route                                    ← approval（左側橘 accent 條）
  Send a navigation goal to the route adapter.
  {"start": ..., "goal": ...}
  Risk: p1 · Scope: sim_control

❯ 1. Yes
  2. Yes, and don't ask again this session
  3. No, and tell JenAI what to do differently (Esc)
──────────────────────────────────────────────────────────
 ✻ Running… (8s · esc to interrupt)                        ← spinner（執行中才出現）
 > ▏Ask JenAI, / for commands, ! for shell                 ← input composer
 ⏵⏵ ollama · qwen3.6:35b · ~/JenAI                         ← 底部狀態列
```

### 視覺設計

- 配色：深色背景，橘色 accent；橘色 hero 卡保留，其餘輸出走精簡項目符號
- 轉錄格式：每則輸出為 `⏺` 項目符號，結果/細節以 `⎿` 縮排（取代原本的卡片框線）
- 捲軸：細版、低調灰（非橘色）
- slash palette 背景：接近黑（`#0d0f12`）
- 狀態圖示：`⏺`（項目）`⎿`（結果）`⚠`（批准）`✻`（執行中）

---

## Slash Command Palette

### 觸發條件
- 輸入框第一個字元為 `/` 時自動開啟
- 刪掉 `/` 後立即關閉

### 顯示格式（每列）
```
/ros schema    解析 topic message 欄位    例: /ros schema /cmd_vel    [ROS2]
```

- 命令名
- 一句說明
- 執行範例
- 分類 badge（ROS2 / Vision / Planning / System）

### 鍵盤行為

| 按鍵 | 行為 |
|---|---|
| `↑/↓` | 上下移動選項 |
| `Tab` | 補全目前高亮命令為模板 |
| `Enter` | 套用模板到輸入框（不直接執行） |
| `Esc` | 關閉 palette，保留輸入內容 |

### 捲動顯示全部
- palette 一次顯示一個視窗（12 列），會隨選取捲動，頂部顯示 `(目前/總數)`
  與 `↑ N more / ↓ N more`，所有符合的指令都可用 `↑/↓` 瀏覽到，不再硬性截斷。

---

## Tab 補全

補全分三層：

1. **命令名補全**：`/ro` → `/ros`
2. **子命令補全**：`/ros t` → `/ros topics` 或 `/ros topic-info`
3. **參數模板補全**：選定後自動補成 `/ros topic-info <topic>`

### v0.1.0 範圍
- 命令名與子命令補全：必做
- 參數模板補全：必做
- topic / location 動態補全：建議納入，可延後

---

## 歷史輸入

| 行為 | 說明 |
|---|---|
| `↑` | 上一筆歷史輸入 |
| `↓` | 下一筆歷史輸入 |
| 前綴過濾 | 若已輸入前綴，`↑/↓` 優先搜尋同前綴歷史 |
| 多行模式 | 游標在第一行按 `↑` 才切歷史，否則保持行內移動 |
| Session 範圍 | v0.1.0 先做 session 內歷史，跨 session 可延後 |

---

## Approval Card 規格

Approval card 出現時取得鍵盤焦點，採 Claude Code 風格的**編號選項**：

```
❯ 1. Yes
  2. Yes, and don't ask again this session
  3. No, and tell JenAI what to do differently (Esc)
```

- `↑/↓` 移動選項、`Enter` 選定，或直接按 `1` / `2` / `3`
- `Esc` = 選項 3（拒絕）
- 選 2「不再詢問」會把同類指令（`/shell`、`/ros pub`、`/route`）加入本 session 自動核准，之後跳過卡片直接執行

必顯示欄位：
- title（操作名稱）
- summary（自然語言說明）
- raw_action（原始指令/payload）
- risk_level + effect_scope
- justification（agent 說明理由）
- 編號選項列

---

## 底部狀態列與執行指示器（v0.1.0）

- **狀態列**：輸入框下方固定一行 `⏵⏵ provider · model · cwd`
- **Spinner**：執行任務時輸入框上方顯示 `✻ <Planning/Running/Thinking…> (Ns · esc to interrupt)`
- **Esc 中斷**：執行中按 `Esc` 取消目前任務並回到輸入
- **! Bash 模式**：以 `!` 開頭的輸入直接當 `/shell` 執行（仍需批准）

## Inline Hint（🚧 規劃中）

當輸入框包含已知命令前綴時，於下方顯示淡色提示行（v0.1.0 尚未實作；
目前以 slash palette 的命令說明取代）：

```
> /ros schema
  hint: /ros schema <topic>  例: /ros schema /cmd_vel
```

---

## Block 類型（v0.1.0 實作）

TUI 對話區中的所有輸出，統一以 `⏺` 項目符號時間軸呈現：

| Block 類型 | 說明 |
|---|---|
| `TimelineItem` | 單行 `⏺` 事件（提示、成功、警告、錯誤、助理回覆） |
| `OutputPanel` | `⏺` 標題 + `⎿` 縮排內容（ROS/vision/loc/摘要等一般輸出共用） |
| `PlanBlock` | 顯示 plan steps（`⏺` + 各步驟 `⎿`） |
| `ToolBlock` | 顯示 tool call：`⏺ tool(args)` + `⎿ result` |
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
  Enter 送出/選定   ! shell   Esc 中斷/拒絕   1/2/3 approval   Tab 補全   ↑↓ 歷史/選單
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

