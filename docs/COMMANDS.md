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
| `/compact` | 壓縮對話歷史以節省 context | `/compact` |
| `/theme` | 切換顯示主題 | `/theme dark` |
| `/resume` | 恢復上一個被中斷的 run | `/resume` |

### Planning

| 指令 | 說明 | 範例 |
|---|---|---|
| `/plan <task>` | 規劃任務，不執行 side effects | `/plan 導航到機械系館並回報電量` |
| `/run <task>` | 執行任務，允許呼叫工具 | `/run 帶我到應科大樓` |
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
| `/ros graph` | 顯示 node/topic 連線關係圖 | `/ros graph` |

### Route

| 指令 | 說明 | 範例 |
|---|---|---|
| `/route <text>` | 自然語言路由，解析起終點並送出導航（需批准） | `/route 從應科大樓到機械系館` |
| `/loc list` | 列出所有可用地點 | `/loc list` |
| `/loc show <name>` | 顯示特定地點詳細資料 | `/loc show 應科大樓` |
| `/loc add` | 新增地點（互動式） | `/loc add` |

### Vision

| 指令 | 說明 | 範例 |
|---|---|---|
| `/vision image <path>` | 分析圖片並輸出結構化觀察 | `/vision image /tmp/scene.jpg` |
| `/vision camera` | 擷取目前 camera topic 並分析（待規格） | `/vision camera` |

### System

| 指令 | 說明 | 範例 |
|---|---|---|
| `/shell <cmd>` | 執行 shell 命令（需批准） | `/shell ls -la /var/log` |

---

## 鍵盤快捷鍵

| 按鍵 | 功能 |
|---|---|
| `Enter` | 送出輸入 / 批准 approval card |
| `Esc` | 關閉 palette / 拒絕 approval card |
| `Tab` | 補全命令名稱或模板 |
| `↑` | 查看前一筆歷史輸入（單行模式） |
| `↓` | 查看後一筆歷史輸入（單行模式） |
| `↑/↓` | 在 slash palette 中上下移動選項 |

---

## 需批准的指令（Approval Required）

以下指令在執行前一律進入 `awaiting_approval` 狀態，顯示 approval card，等待 Enter 批准或 Esc 拒絕：

- `/ros pub`
- `/route`
- `/shell`
- `/run` 內部觸發的任何 side-effect tool

