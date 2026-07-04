# adapters — 外部資源的薄包裝

沒有業務邏輯,只把外部世界翻成乾淨的 Python 介面(容錯、timeout、錯誤分類)。

| 檔案 | 職責 |
|---|---|
| `ros2_adapter.py` | `ros2` CLI subprocess 包裝(topics/echo/pub/action);有 timeout 與錯誤分類 |
| `locations.py` | `locations.toml` 載入/儲存/模糊搜尋;`load_locations_tolerant` 是全介面共用的容錯載入(錯誤變訊息不變例外) |
| `route_adapter.py` | RouteAdapter 協定:`stub`(誠實拒絕)/ `nav2`(CLI send_goal,bridge 不可用時的後備) |

需要「即時回饋、取消、相機幀」時不要用這層 —— 那是 `bridge/` 的工作。
