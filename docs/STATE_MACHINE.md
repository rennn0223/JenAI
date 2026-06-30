# JenAI Run 狀態機

## 狀態定義

| 狀態 | 說明 | 使用者可見 |
|---|---|---|
| `idle` | 尚未開始任務 | ✅ |
| `understanding` | 解析使用者需求中 | ✅ |
| `planning` | 產生 execution plan 中 | ✅ |
| `running` | 執行工具或生成回應中 | ✅ |
| `awaiting_approval` | 等待人工核准敏感工具 | ✅ |
| `blocked` | 因缺少環境/參數/權限而中止 | ✅ |
| `completed` | 任務成功完成 | ✅ |
| `failed` | 任務失敗 | ✅ |

---

## 狀態轉移

### `/plan <task>`
```
idle
 └─> understanding
      └─> planning
           └─> completed
```

### `/run <task>`（無敏感工具）
```
idle
 └─> understanding
      └─> planning
           └─> running
                └─> completed
```

### `/run <task>`（含敏感工具）
```
idle
 └─> understanding
      └─> planning
           └─> running
                └─> awaiting_approval
                     ├─ [Enter / Approve] ──> running (resume)
                     │                         └─> completed
                     └─ [Esc / Reject]   ──> blocked
                                               └─> (agent fallback or report failure)
```

### 執行錯誤
```
running
 └─> failed
      └─> (顯示錯誤 + 建議修復步驟)
```

---

## Approval Card 規格

當進入 `awaiting_approval` 時，TUI 與 WebUI 必須顯示 approval card，包含：

| 欄位 | 說明 |
|---|---|
| `title` | 操作標題，例如「發送 ROS2 訊息」 |
| `summary` | 一句自然語言摘要 |
| `raw_action` | 實際要執行的原始命令或 payload |
| `risk_level` | `p0`（低）/ `p1`（中）/ `p2`（高） |
| `effect_scope` | `none` / `read` / `local_write` / `sim_control` / `host_command` |
| `justification` | agent 解釋為何需要這個操作 |

### Risk Level 定義

| Level | 定義 | 範例 |
|---|---|---|
| `p0` | 唯讀，無副作用 | 查詢 topics、讀取 schema |
| `p1` | 有可逆副作用 | 發送低速移動訊號 |
| `p2` | 不可逆或高風險 | 刪除檔案、複雜 shell 命令 |

---

## Blocked 狀態後的行為

當使用者拒絕一個操作：

1. Agent 收到 `rejection` 結果
2. Agent 必須選擇：
   - 提出替代方案
   - 回報任務無法完成的原因
   - 詢問使用者是否要調整策略

不允許靜默跳過，也不允許重新嘗試相同操作。

