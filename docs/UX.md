# JenAI UX 規格

## 設計原則

- **Terminal-first**：TUI 是主要操作介面，WebUI 是監控輔助介面
- **折疊優先**：細節預設折疊，主畫面保持乾淨
- **批准可見**：所有敏感操作都有清楚的 approval card，不隱藏
- **進度透明**：run 的每個階段都有可見狀態

---

## TUI 規格

### 主畫面佈局

```
┌──────────────────────────────────────────────────────┐
│ JenAI  v0.1.0   provider: ollama   model: llama3.2  │  ← header bar
│                          ROS2: ✅  vision: ✅        │
├──────────────────────────────────────────────────────┤
│                                                      │
│  [conversation / output area]                        │  ← 主區域（可捲動）
│                                                      │
│  ┌── Plan ─────────────────────────────────────┐    │
│  │ Step 1: 解析起終點座標              [done]  │    │
│  │ Step 2: 產生導航路徑                [active]│    │
│  │ Step 3: 發送 route 指令             [需批准]│    │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  ┌── ⚠️ Approval Required ─────────────────────┐    │
│  │ 發送 ROS2 導航指令                           │    │
│  │ /move_base_simple/goal → {pose: ...}         │    │
│  │ Risk: p1 · Scope: sim_control                │    │
│  │                                              │    │
│  │  [Enter: 批准]          [Esc: 拒絕]          │    │
│  └──────────────────────────────────────────────┘   │
│                                                      │
├──────────────────────────────────────────────────────┤
│ > /route 從應科大樓到機械系館_                       │  ← input composer
└──────────────────────────────────────────────────────┘
```

### 視覺設計參考（附檔 TUI 風格）

- 配色：深色背景，橘色 accent
- 字型：JetBrains Mono
- 區塊：以卡片框線區隔 plan / tool / approval / error
- 狀態圖示：✅ ⚠️ ❌ ⏳

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

### 最近使用
- palette 頂部優先顯示最近使用的 3–5 個命令

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

Approval card 出現時：
- 取得鍵盤焦點
- Enter = 批准
- Esc = 拒絕
- 可用 Tab 切換到查看 raw_action 折疊區塊

必顯示欄位：
- title（操作名稱）
- summary（自然語言說明）
- raw_action（原始指令/payload，預設折疊）
- risk_level + effect_scope
- justification（agent 說明理由）
- [批准按鈕] [拒絕按鈕]

---

## Inline Hint

當輸入框包含已知命令前綴時，輸入框下方顯示淡色提示行：

```
> /ros schema
  hint: /ros schema <topic>  例: /ros schema /cmd_vel
```

---

## Block 類型

TUI 對話區中的所有結構化輸出，統一以以下 block 類型呈現：

| Block 類型 | 說明 |
|---|---|
| `PlanBlock` | 顯示 plan steps，可折疊 |
| `ToolBlock` | 顯示 tool call 執行結果，可折疊 |
| `ApprovalCard` | 等待批准的操作，可展開 raw |
| `ErrorBlock` | 錯誤訊息 + 修復建議 |
| `RosBlock` | ROS2 相關指令輸出，可折疊 |
| `VisionBlock` | Vision 分析結果，可折疊 |
| `SummaryBlock` | 任務完成後的最終摘要 |

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
  Enter 批准  Esc 拒絕  Tab 補全  ↑↓ 歷史/選單
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

