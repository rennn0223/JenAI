# JenAI

> An agentic AI terminal assistant for ROS2-based robot systems.

JenAI 是一套以 terminal 為核心的 AI Agent 操作介面，專為機器人開發者設計。它整合大型語言模型、ROS2 工具鏈、視覺理解能力與 human-in-the-loop 批准機制，讓你能以自然語言規劃、執行、監控機器人任務。

---

## 核心能力

- **自然語言任務規劃與執行**：以 `/plan` 與 `/run` 驅動 agent 完成多步驟任務
- **ROS2 整合**：topics 探索、schema 解析、echo 監看、pub 控制
- **自然語言路由**：以地點名稱描述起終點，自動解析成導航指令
- **視覺理解**：接受圖片輸入，由 VLM 分析場景並產出結構化觀察
- **Human-in-the-loop 批准機制**：敏感操作一律暫停等待人工核准，Enter 批准、Esc 拒絕
- **TUI + WebUI 雙介面**：terminal 優先操作，WebUI 提供監控與視覺化

---

## 快速入門

```bash
# 首次執行（自動進入 setup wizard）
JenAI

# 環境健康檢查
JenAI doctor

# 啟動 WebUI 監控中心
JenAI web
```

---

## 文件導覽

| 文件 | 說明 |
|---|---|
| [docs/COMMANDS.md](docs/COMMANDS.md) | CLI + slash 命令完整規格 |
| [docs/FEATURES.md](docs/FEATURES.md) | 14 個核心功能可實作規格 |
| [docs/UX.md](docs/UX.md) | TUI/WebUI 互動設計規格 |
| [docs/DATA_SCHEMAS.md](docs/DATA_SCHEMAS.md) | 共用資料結構定義 |
| [docs/STATE_MACHINE.md](docs/STATE_MACHINE.md) | Run 狀態機設計 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 模組架構設計 |
| [docs/MOSCOW.md](docs/MOSCOW.md) | v0.1.0 功能優先級 |

---

## 技術棧（規劃中）

- **Agent Framework**：OpenAI Agents SDK
- **LLM Provider**：LiteLLM（多 provider 統一接口）
- **TUI**：Textual（Python）
- **WebUI**：待定
- **ROS2**：Jazzy
- **語言**：Python 3.12+

---

## 狀態

> 🚧 目前為規格設計階段，程式碼尚未開始實作。

