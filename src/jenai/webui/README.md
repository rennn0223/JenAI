# webui — 手機可用的監控/批准儀表板

`JenAI web` 起 http.server(預設 127.0.0.1:8760),**多頁式**:Console(chat+
slash+**指令選擇表**(輸入 `/` 彈出;清單=`commands.py` 的 `WEB_SLASH_COMMANDS`,
與 `_slash` 實作同源)+確認按鈕+SVG 地圖)、Camera(topic 下拉 + `/api/frame` 每秒一幀 + odom 小格,只在該頁輪詢)、Status(5s 更新)、API(端點目錄);紅色 **STOP** 鈕全頁常駐。

| 檔案 | 職責 |
|---|---|
| `server.py` | 端點:`/api/status` `/api/command` `/api/confirm` `/api/reject` `/api/map` `/api/frame` `/api/topics` `/api/stop`;**token 認證**(Bearer/cookie/`?token=`,401 絕不 Set-Cookie);`_PendingConfirms`(動作 server 端一次性持有,瀏覽器改不了);PoseCache(退避重試);StatusCache(doctor 30s / ROS graph 2s,跨分頁合併 probe);**`/api/stop` 是唯一免認證端點 —— 停車永遠安全** |
| `render.py` | 純渲染:儀表板 HTML/CSS/JS(嵌字串,E501 豁免) |
| `commands.py` | Web 版指令執行 + confirm 動作封存;`WEB_SLASH_COMMANDS`(選擇表資料,必與 `_slash` 同步擴充) |
| `monitoring.py` | 將 `RunRecord` 投影成有界且不含 raw action／secret 的監控 transcript。 |
| `presentation.py` | 將 tolerant status payload 轉成 typed、使用者可見的 health／run／approval／tool view。 |
| `monitoring_render.py` | 純渲染目前任務、待批准、工具時間軸與本工作階段紀錄。 |
| `run_tracking.py` | 維護 WebUI 指令、批准、拒絕、工具與 task outcome 的共用 `RunStore` lifecycle。 |

UI 美學基準:WebUI 像 Claude Desktop。信任邊界見 `docs/validation/THREAT_MODEL.md`。
