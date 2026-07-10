# config — 設定的載入、模型與精靈

| 檔案 | 職責 |
|---|---|
| `models.py` | `AppConfig` + provider profiles + **`VehicleProfile`(`[vehicle]`:cmd_vel topic、硬限速、相機 topic)** + `TwinProfile`(`[twin]`:孿生 domain、G1–G5 閾值、禁區)+ `MapDatum`(`[map_datum]`:map 原點經緯度與軸向,`/loc add gps` 的換算基準)+ `AvoidanceProfile`(`[avoidance]`:depth stop-and-go detour + freshness deadline,給 odom 直驅用)。**全 repo 唯一允許出現載具差異的地方** |
| — | `route_adapter`:`stub`(不動真機)/`nav2`(NavigateToPose)/`odom`(無 Nav2 的 odom→cmd_vel 直驅,開闊地測試) |
| — | `ros2_ws`:`JenAI scaffold` 生成套件的工作區根(預設 `~/ros2_ws`,套件寫入其 `src/`) |
| `store.py` | config/.env 尋徑與載入(`JENAI_CONFIG` → XDG → APPDATA;shell env 優先於 .env)、`build_minimal_config`、儲存 |
| `setup.py` | 首次執行的 setup wizard:ASCII banner → 供應商預設選單(Ollama/NIM/OpenAI/custom)→ 逐欄範例 → 摘要卡 |

config schema 是 public surface(見 `docs/VERSIONING.md`):欄位改動要附遷移,
安全預設只能收緊。
