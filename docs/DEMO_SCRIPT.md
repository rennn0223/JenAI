# DEMO_SCRIPT — 3 分鐘 Hero + 15 分鐘技術 Demo

> 場地：DGX Spark + 目前驗收用 Isaac Sim/Nav2 場景（Carter 或 Leatherback 必須在 artifact
> 記明）。主軸一句話：**「用高階能力操作機器人，而且失敗不會被包裝成成功。」**
> 單次跑順只算排練；對外 gate 是同一 commit/model/scene 連跑 10 次至少 9 次完整成功，
> 保存逐次時間、失敗與環境指紋。任何失敗都依誠實回報原則現場說明。

## 三分鐘 Hero（Slash-first）

E4 在 DGX Spark + qwen3.6:35b 的單次決策中位數約 68.8 秒、P95 約 81.8 秒；因此三分鐘版
刻意使用確定性 Slash 能力，不假裝自然語言一定能塞進時限。

| 時間 | 操作 | 必須看見 |
|---|---|---|
| 0:00–0:20 | 已開好的 TUI → `/status` → `/ros topics` | provider/config 與 live ROS graph；完整 doctor 已於演前保存 |
| 0:20–1:20 | `/route map_right_up` → Yes | 批准、Twin/導航階段與真實 result；途中可下 `/stop` |
| 1:20–2:10 | `/stop` | 先送立即 halt，再取消並 reap 舊 action/publisher，最後再送 halt／zero；記錄實際 stop latency |
| 2:10–3:00 | `/route sw_test_zone` → Yes | Twin `block`/`refer`，robot domain 不收到 goal；最後秀 audit/result |

若 Twin 裁決超過剩餘時限，這次 hero 記 FAIL，不用口頭宣稱代替。自然語言、VLM、WebUI
與 provider failover 全部放到下方 15 分鐘技術版。

## 前置檢查(開演前 10 分鐘)

```bash
# 1) Isaac Sim Carter 場景 + Nav2 起好(照 ISAAC_NAV2_SETUP),車停在 dock 附近
# 2) ★必做★ 開 Twin Gate:改 ~/.config/jenai/config.toml 的 [twin] enabled = true
#    否則 7:00 的禁區 block 橋段完全失效(閘關著 = 直接送 Nav2,不會擋)。
#    twin 需在自己的 ROS_DOMAIN_ID 上跑孿生場景(見 TWIN_SETUP);enabled 但場景沒起
#    → 每個 route 都會 refer(twin unreachable),一樣做不了 demo。
# 3) 健檢:doctor 的 twin 段必須是 PASS。
#    v1.0 起 enabled = false 會顯示 WARN「Twin Gate is DISABLED」——看到它就是還沒開。
#    (v1.0 前 doctor 對關閉的 twin 完全靜默,B7 首輪排練因此踩坑,已修)
source /opt/ros/jazzy/setup.bash && uv run JenAI doctor
# 4) 雲端金鑰活著(斷網橋段要先「在線」才有戲):
uv run JenAI providers        # nvidia-cloud 要在
# 5) WebUI 先起好、手機同網段掃 token 網址:
uv run JenAI web --host 0.0.0.0
# 6) 確認權限模式在「審批」(批准卡是展示重點,別留在 auto)
# 7) 準備斷網開關:拔網線或關 Wi-Fi 的具體動作先演練一次
```

`sw_test_zone` 不是五個正式地點之一，不能假設另一台機器已經有它。請在**演示專用的
locations 檔**加入下列項目；座標位於目前 `SW-narrow-aisle` 禁區內，`forbidden` tag
也會讓 `/explore` 排除它。不得把車開進禁區再用 `/loc add here` 建點。

```toml
[[locations]]
name = "sw_test_zone"
aliases = []
frame_id = "map"
tags = ["forbidden", "demo-only"]

[locations.pose]
x = -6.75
y = -11.0
yaw = 0.0
```

> **開演前先 `JenAI loc show sw_test_zone`，再批准 `/route sw_test_zone` 驗一次**：
> 必須由目標點禁區預檢立即得到 `G3 block`，且 Twin 與 target graph 均不得收到 goal。
> 這一段證明禁區邊界；完整孿生預演則由前一個安全 `/route` 的 G1–G5 報告展示。

## 15 分鐘技術版時間軸

| 時間 | 段落 | 操作與預期 |
|---|---|---|
| 0:00–1:00 | 開場 | 一句話定位(LLM 高階決策 + 危害對應的互補防護 + 數位孿生閘門)。秀本 protocol required checks；預期例外不得假裝全綠 |
| 1:00–2:00 | TUI | `uv run JenAI` 進 TUI → `/status`。台詞:LLM 永不進即時迴路,急停/限速/watchdog 不依賴模型與網路 |
| 2:00–4:00 | 自然語言導航 | 輸入 `去 map_right_up`。Agent 應在同一輪完成地點解析與 route preview，接著呼叫執行工具；批准由框架的批准卡處理，不應用文字再問一次。批准 → Twin Gate → Nav2 action → TUI 回報實際 succeeded/failed。上台要最穩可用 Slash `/route map_right_up`（確定性出卡，且不等待 LLM）。 |
| 4:00–7:00 | 巡邏 + 視覺 | `/patrol map_right_down, dock x1 photo` → 每個到達點抓相機幀給 VLM、即時回報觀察 → 結束後 `/report` 秀自動日報 |
| 7:00–9:00 | **Twin Gate** | 先展示上一個安全目標的完整 G1–G5 rehearsal report；再 `/route sw_test_zone`（演示專用禁區內測試點）→ 批准 → 目標點預檢 **G3 block**，Twin 與 target graph 均不送 goal。台詞：人批准了也擋——HITL 與 Twin Gate 針對不同失效模式互補，但不宣稱通用正交或任兩層失效仍安全 |
| 9:00–10:00 | 急停 | `/route map_left_up` → 車動起來後 `/stop` → 先送立即 halt，舊 goal/publisher 被取消並 reap，最後 halt／zero 後不復動、佇列清空；HIL gate 要求取消確實傳播且 2 秒 settle 後漂移 ≤0.05 m。另記 operator-to-first-zero latency，但在場域危害分析訂出上限前不把它包裝成已通過的 latency 門檻 |
| 10:00–12:00 | **斷網備援** | `/provider nvidia-cloud` → 問「你看得到什麼?」(雲端答)→ **當眾斷網** → 再問 → 誠實報錯不假裝 → `/provider local` → 同句話由 qwen3.6:35b 接手答。台詞:反射層全程有效,斷網影響的只有對話,不是安全 |
| 12:00–13:30 | WebUI | 手機開 WebUI(token 認證)看地圖與狀態;示範紅色 **STOP** 鈕(免認證,LAN 內任何人可急停) |
| 13:30–15:00 | 收尾 | `/report list` + audit(SQLite 稽核:run/approval/gate 事件可回放)→ Q&A |

## 已知眉角(2026-07-16 首次排練實測)

- **切模型用編號不是箭頭**:`/model` 列出**帶編號**清單,切換是 `/model 2` 或 `/model qwen3.6:35b`——**上下鍵不會選模型**(上下鍵是輸入歷史/palette)。demo 前先 `/model` 記好號碼
- **NL 導航不應用文字重複詢問**:v1.1.1 會把已解析的導航請求直接交給框架批准流程；若本地模型產生不完整 action，出口會 fail closed 且不送 goal。需要穩定節奏時直接用 `/route`
- **禁區測試點不存在／沒 block**：先以 `JenAI loc show sw_test_zone` 核對演示專用地點與
  forbidden zone；目標點預檢的 G3 block 不依賴孿生程序。完整 rehearsal 是否可用要另看
  `doctor` twin 檢查與安全目標的 G1–G5 report，兩者不可混為一談

## 各段備援(出事就照講)

- **導航失敗 / Nav2 沒 ready**:秀錯誤訊息本身——「它不會假裝成功,這是設計」;`doctor` 當場查
- **Twin 預演等待(~40–60 秒才裁決)**:空檔講 G1–G5 五閘語意(碰撞/超時/禁區/終點偏差/Nav2 失敗)
- **VLM 回報慢**:photo 段只跑兩點一圈;等待時講「觀察不動作,建議動作一律標需批准」
- **雲端在斷網前就掛**:直接跳 `/provider local`,台詞改「我們預設就地端,雲端只是選配」
- **WebUI 手機連不上**:工作站瀏覽器開同一網址即可,重點是 STOP 鈕與 token

## 跑完之後

1. `/provider local` 還原、`/dock` 收車。
2. 保存 commit、model digest、Isaac/ROS/Nav2/RMW/domain、場景/map hash、每段起訖時間與失敗。
3. 單次全程順只記 `rehearsal PASS`；同設定 10-run 至少 9/10 且 artifact 完整後，銷售
   demo gate 才可 PASS。這仍不是功能安全認證。
