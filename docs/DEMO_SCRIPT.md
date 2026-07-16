# DEMO_SCRIPT — 15 分鐘 Demo 劇本(V1_GATE B7)

> 場地:DGX Spark 工作站 + Isaac Sim Carter 場景。跑順一次即可勾 B7。
> 主軸一句話:**「說人話操作機器人,而且它不會因為你說錯話就做危險的事。」**
> 每段都有備援台詞——demo 中任何失敗都照「誠實回報」原則現場講,這本身就是賣點。

## 前置檢查(開演前 10 分鐘)

```bash
# 1) Isaac Sim Carter 場景 + Nav2 起好(照 ISAAC_NAV2_SETUP),車停在 dock 附近
# 2) ★必做★ 開 Twin Gate:改 ~/.config/jenai/config.toml 的 [twin] enabled = true
#    否則 7:00 的禁區 block 橋段完全失效(閘關著 = 直接送 Nav2,不會擋)。
#    twin 需在自己的 ROS_DOMAIN_ID 上跑孿生場景(見 TWIN_SETUP);enabled 但場景沒起
#    → 每個 route 都會 refer(twin unreachable),一樣做不了 demo。
# 3) 健檢:doctor 必須「主動」印出 twin 段且為 ready。
#    ⚠️ 陷阱:enabled = false 時 doctor 對 twin 完全靜默(不是紅、是沒有)——
#    「全綠」會騙過你。看到 twin 段有 PASS 才算數。
source /opt/ros/jazzy/setup.bash && uv run JenAI doctor
# 4) 雲端金鑰活著(斷網橋段要先「在線」才有戲):
uv run JenAI providers        # nvidia-cloud 要在
# 5) WebUI 先起好、手機同網段掃 token 網址:
uv run JenAI web --host 0.0.0.0
# 6) 確認權限模式在「審批」(批准卡是展示重點,別留在 auto)
# 7) 準備斷網開關:拔網線或關 Wi-Fi 的具體動作先演練一次
```

> **開演前用 `/route 去 sw_test_zone` 空跑驗一次**:必須看到 `G3 block`。
> 沒 block = twin 沒真的開,別上台。這是整場的招牌橋段,唯一不能臨場壞的。

## 時間軸

| 時間 | 段落 | 操作與預期 |
|---|---|---|
| 0:00–1:00 | 開場 | 一句話定位(LLM 決策 + 三層安全鏈 + 數位孿生閘門)。秀 `doctor` 全綠畫面 |
| 1:00–2:00 | TUI | `uv run JenAI` 進 TUI → `/status`。台詞:LLM 永不進即時迴路,急停/限速/watchdog 不依賴模型與網路 |
| 2:00–4:00 | 自然語言導航 | 輸入 `去 map_wall`。**注意:agent 可能先反問確認**(如「要我帶你去 map_wall 嗎?」)——這時回 `Go`/`好` 才出批准卡,這正是 HITL 在運作,不是 bug。要百分百穩就改用 slash `/route map_wall`(確定性出卡)。批准 → Isaac 裡車動、TUI 即時剩餘距離 → 到達 |
| 4:00–7:00 | 巡邏 + 視覺 | `/patrol map_right_down, dock x1 photo` → 每個到達點抓相機幀給 VLM、即時回報觀察 → 結束後 `/report` 秀自動日報 |
| 7:00–9:00 | **Twin Gate** | `/route 去 sw_test_zone`(禁區內測試點)→ 批准 → 孿生車先跑、實體車不動 → 預演軌跡進禁區 → **G3 block,誠實拒絕**。台詞:人批准了也擋——HITL 攔意圖層,Twin Gate 攔執行層,邏輯正交 |
| 9:00–10:00 | 急停 | `/route 去 map_left_up` → 車動起來後 `/stop` → **1 秒內停 + 清空佇列**(免批准:停下來永遠安全) |
| 10:00–12:00 | **斷網備援** | `/provider nvidia-cloud` → 問「你看得到什麼?」(雲端答)→ **當眾斷網** → 再問 → 誠實報錯不假裝 → `/provider local` → 同句話由 qwen3.6:35b 接手答。台詞:反射層全程有效,斷網影響的只有對話,不是安全 |
| 12:00–13:30 | WebUI | 手機開 WebUI(token 認證)看地圖與狀態;示範紅色 **STOP** 鈕(免認證,LAN 內任何人可急停) |
| 13:30–15:00 | 收尾 | `/report list` + audit(SQLite 稽核:run/approval/gate 事件可回放)→ Q&A |

## 已知眉角(2026-07-16 首次排練實測)

- **切模型用編號不是箭頭**:`/model` 列出**帶編號**清單,切換是 `/model 2` 或 `/model qwen3.6:35b`——**上下鍵不會選模型**(上下鍵是輸入歷史/palette)。demo 前先 `/model` 記好號碼
- **NL 導航可能先被反問**:見 2:00 段;不是漏批准,是 agent 在澄清意圖。沒有「沒批准就動」的情況(動作一律過批准卡)
- **禁區沒 block = twin 沒開**:見前置 ★必做★;開演前務必空跑 `/route 去 sw_test_zone` 見到 G3

## 各段備援(出事就照講)

- **導航失敗 / Nav2 沒 ready**:秀錯誤訊息本身——「它不會假裝成功,這是設計」;`doctor` 當場查
- **Twin 預演等待(~40–60 秒才裁決)**:空檔講 G1–G5 五閘語意(碰撞/超時/禁區/終點偏差/Nav2 失敗)
- **VLM 回報慢**:photo 段只跑兩點一圈;等待時講「觀察不動作,建議動作一律標需批准」
- **雲端在斷網前就掛**:直接跳 `/provider local`,台詞改「我們預設就地端,雲端只是選配」
- **WebUI 手機連不上**:工作站瀏覽器開同一網址即可,重點是 STOP 鈕與 token

## 跑完之後

1. `/provider local` 還原、`/dock` 收車
2. 全程順 → V1_GATE **B7 打勾**(排練日期記上);卡住的點=劇本 bug,回報修劇本再跑
