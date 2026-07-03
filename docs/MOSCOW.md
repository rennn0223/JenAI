# JenAI v0.1.0 功能優先級（MoSCoW）

> 📜 **設計期文件**(v0.1 規劃階段)。實際實作已演進,現況以 [TECHNICAL_GUIDE.md](TECHNICAL_GUIDE.md)、[COMMANDS.md](COMMANDS.md) 與程式碼為準;方向與 roadmap 見 [PROJECT_DIRECTION.md](PROJECT_DIRECTION.md)。


## Must Have — 沒有這些就無法驗收

| 功能 | 說明 |
|---|---|
| `JenAI` 主入口 + setup wizard | 首次設定流程 |
| `JenAI doctor` | 環境健康檢查，讓開發可自助除錯 |
| `/plan` | 任務規劃，不 side effect |
| `/run` | 任務執行，含 tool call |
| Approval 機制 | Enter 批准 / Esc 拒絕，未批准不執行 |
| `/ros topics` | 最基本的 ROS2 探索能力 |
| `/ros schema` | 了解 topic 結構 |
| `/ros pub` | 基本 ROS2 控制，含批准 |
| `/route` | 自然語言導航，含批准 |
| `/loc list` + `/loc show` | 位置管理 |
| Slash palette（基本） | `/` 觸發、↑↓移動、Tab 補全模板 |
| 歷史輸入 | ↑↓ 查看歷史 |
| `/help` | 基本 onboarding |
| `RunRecord` 與 `SessionState` | 資料結構實作 |

---

## Should Have — 重要但可以第一版稍後補

| 功能 | 說明 |
|---|---|
| `/ros topic-info` | topic 詳細資訊 |
| `/ros echo` | 即時監看 topic |
| `/vision image` | 圖片分析 |
| `/shell` | shell 執行，含批准 |
| `/status` | session 狀態顯示 |
| `/why` | 決策解釋 |
| `/review` | plan 複查 |
| Inline hint | 輸入框提示行 |
| 最近使用命令 | palette 頂部顯示 |
| WebUI 基本框架 | 最少有 transcript + run status |
| `JenAI config` | 設定管理 CLI |

---

## Could Have — 有時間就做，沒有也沒差

| 功能 | 說明 |
|---|---|
| `/ros graph` | node/topic 關係圖 |
| `/loc add` | 互動式新增地點 |
| `/vision camera` | 直接擷取 camera topic |
| `/compact` | context 壓縮 |
| `/resume` | 恢復上次中斷 |
| topic / location 動態 Tab 補全 | 參數自動補全 |
| 跨 session 歷史輸入 | 持久化歷史 |
| WebUI 完整 timeline | tool call 視覺化時間軸 |
| WebUI approval queue | 遠端批准操作 |
| 多主題 theme | `/theme` 切換 |

---

## Won't Have（v0.1.0 不做）

| 功能 | 原因 |
|---|---|
| 多 agent 協作 | 架構複雜，先做單 agent 驗證 |
| 語音輸入 | 不在 terminal-first 核心 |
| 雲端部署 / Kubernetes | 先做本地 |
| 自訓練模型 | 使用 off-the-shelf LLM |
| Web 遠端操控 | 安全性需另外設計 |
| `/ros pub` 連發模式 | 先做單次控制 |

---

## 建議實作順序

### Phase 1 — 基礎能跑
1. CLI 入口 + config 讀寫
2. `JenAI doctor`
3. LiteLLM provider 接通
4. Agent run + streaming
5. TUI 基本框架（輸入 + 輸出區域）

### Phase 2 — Agent 可用
6. `/plan` + PlanBlock
7. `/run` + ToolBlock
8. Approval 機制（ApprovalCard + Enter/Esc）
9. `/ros topics` + `/ros schema`
10. `/route` + locations

### Phase 3 — 體驗完整
11. Slash palette + Tab 補全
12. 歷史輸入
13. `/help`
14. `/ros echo` + `/ros pub`
15. `/vision image`
16. `/shell`
17. WebUI 基本框架

