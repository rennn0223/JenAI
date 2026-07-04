# config — 設定的載入、模型與精靈

| 檔案 | 職責 |
|---|---|
| `models.py` | `AppConfig` + provider profiles + **`VehicleProfile`(`[vehicle]`:cmd_vel topic、硬限速、相機 topic)** + `TwinProfile`(`[twin]`:孿生 domain、G1–G5 閾值、禁區)。**全 repo 唯一允許出現載具差異的地方** |
| `store.py` | config/.env 尋徑與載入(`JENAI_CONFIG` → XDG → APPDATA;shell env 優先於 .env)、`build_minimal_config`、儲存 |
| `setup.py` | 首次執行的 setup wizard:ASCII banner → 供應商預設選單(Ollama/NIM/OpenAI/custom)→ 逐欄範例 → 摘要卡 |

config schema 是 public surface(見 `docs/VERSIONING.md`):欄位改動要附遷移,
安全預設只能收緊。
