# agent — /plan 與 /run 的代理協調

openai-agents SDK 上的多代理層:Supervisor 解析意圖後 handoff 給專職
agent(ROS/Motion/Navigation/Perception),任何 side-effect 工具都以
批准中斷(interruption)交回 UI 的批准卡 —— **代理不能繞過人**。

| 檔案 | 職責 |
|---|---|
| `orchestrator.py` | /run 主迴圈:規劃 → 工具呼叫 → 批准暫停/續跑 → 回報 |
| `plan_agent.py` | /plan:產計畫不執行;/review 重審 |
| `specialists.py` | 專職 agent 定義與 handoff 圖 |
| `run_agent.py` / `runtime.py` | agent 建構與執行期黏合 |
| `guardrails.py` | 輸入/輸出護欄 |
| `instructions.py` | 系統提示詞(誠實回報原則在這裡成文) |
| `context.py` / `session.py` | run 上下文與跨重啟對話記憶(/clear 會清) |
| `tracing.py` | 執行軌跡(/why 的資料來源) |

工具本體不在這裡 —— 在 `tools/*_agent_tools.py`(包裝 `*_core.py` 純邏輯)。
