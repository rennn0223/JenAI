# providers — LLM 供應商抽象

全部走 OpenAI 相容端點:一個抽象吃遍 NVIDIA NIM(雲)、Ollama(Jetson 地端)、
OpenAI、任何相容 server;`/provider` 一鍵切換,斷網照跑(local)。

LiteLLM 若使用,部署在遠端伺服器作 gateway;JenAI 只設定該服務的 `base_url`,
不需要在機器人端安裝 LiteLLM 套件。

| 檔案 | 職責 |
|---|---|
| `chat.py` | `ask_provider`、`stream_provider`(串流)、`ask_json`、`ask_vision_json`、`list_provider_models`;`_provider_errors` 統一例外映射;`parse_json_reply` 寬容解析(thinking 模型的 ```json 圍欄與前後綴 —— 所有結構化輸出都經過它) |
| `agent_model.py` | openai-agents SDK 的模型綁定(chat/plan/vision/route/default 各自可綁不同模型) |

鐵律:這層的東西**永不被反射層 import**(CI 架構測試強制)。
