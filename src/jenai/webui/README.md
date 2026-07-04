# webui — 手機可用的監控/批准儀表板

`JenAI web` 起 http.server(預設 127.0.0.1:8760),**多頁式**:Console(chat+
slash+確認按鈕+SVG 地圖)、Camera(topic 下拉 + `/api/frame` 每秒一幀 + odom 小格,只在該頁輪詢)、Status(5s 更新)、API(端點目錄);紅色 **STOP** 鈕全頁常駐。

| 檔案 | 職責 |
|---|---|
| `server.py` | 端點:`/api/status` `/api/command` `/api/confirm` `/api/map` `/api/frame` `/api/topics` `/api/stop`;**token 認證**(Bearer/cookie/`?token=`,401 絕不 Set-Cookie);`_PendingConfirms`(動作 server 端一次性持有,瀏覽器改不了);PoseCache(退避重試);**`/api/stop` 是唯一免認證端點 —— 停車永遠安全** |
| `render.py` | 純渲染:儀表板 HTML/CSS/JS(嵌字串,E501 豁免) |
| `commands.py` | Web 版指令執行 + confirm 動作封存 |

UI 美學基準:WebUI 像 Claude Desktop。信任邊界見 `docs/THREAT_MODEL.md`。
