# JenAI 測試手冊(TEST.md)

> 對應版本:v0.16.0(快照隨 release 更新)。所有可測項目(CLI / Slash / 對話)與期望輸出的總表,
> 附本機(Jetson 工作機)實測現況快照。自動化測試見「自動化測試」節;
> 其餘為手動驗收項目。

**狀態圖例**
- ✅ 本機現在就能測(2026-07 快照,見下)
- 🔶 需要更多後端才能測(Nav2 / 地圖 / RGB 相機 / Isaac twin —— 缺什麼寫在「前置」欄)
- 🧪 單元測試已涵蓋(TUI 互動路徑,headless 手測不便;`uv run pytest` 即驗)

---

## 測試環境須知(三種組合,結果不同)

| 環境 | 用途 | 結果 |
|---|---|---|
| `env -u PYTHONPATH uv run pytest` | **跑測試套件的唯一正確方式** | ROS 的 PYTHONPATH 會遮蔽 venv 依賴,必須 unset |
| `source /opt/ros/jazzy/setup.bash` 後直接 `uv run JenAI …`(保留 PYTHONPATH) | **跑 app 的正確方式** | ros2 CLI 可用,doctor nav 區段誠實回報 |
| 沒 source ROS | app 照跑(誠實降級) | doctor 報 `ros2_cli fail`,ROS 相關指令回報 unavailable,不假裝成功 |

> 注意:source ROS 但 unset PYTHONPATH 是**壞組合** —— ros2 CLI 自己需要 ROS 的
> PYTHONPATH,會 `ros2 --help exit 1`。

## 自動化測試

| 項目 | 指令 | 期望輸出 |
|---|---|---|
| 單元測試(全) | `env -u PYTHONPATH uv run pytest` | 全綠(v0.16.0 基準 `342 passed`,約 24s,無 ROS 環境也全過);含安全鏈故障注入(bridge 啟動失敗/watchdog 武裝失敗/twin 預演中斷/halt 失敗誠實回報)與架構鐵律測試 |
| Lint | `env -u PYTHONPATH uv run ruff check src tests` | 無輸出(exit 0) |
| CI | push PR | `test` job(ruff+pytest,coverage 表進 job summary,基準 74%)、`build` job(uv build + uvx 全新環境裝 wheel 跑 `jenai --help`)皆綠 |
| Release gate | 推 `vX.Y.Z` tag | release workflow:版本一致檢查 → lint+測試 → build → wheel 冒煙測試 → 草稿 release 附 wheel/sdist |
| 安全鏈覆蓋閘 | CI `test` job 自動跑 | `coverage report --fail-under=90`(estop/watchdog/bridge/gate/rules);現況 92%,倒退即紅 |
| 24h soak(A6) | `python3 scripts/soak.py --rules <rules.toml>`(ROS-sourced shell、掛機時跑) | `soak-*/report.md`:RSS baseline/final/peak、增長 %、**PASS/WARN**(>20% 增長 = WARN);短跑驗證:`--minutes 5 --interval 5 --warmup 60` |

## 本機實測現況快照(v0.16.0,Jetson 工作機)

- **doctor overall:`warn`**(source ROS 後):environment / config / provider / locations / webui 全 pass;nav 區段:無 `/map`、無 `/amcl_pose`、無 `/scan`、**Nav2 未跑**;`/cmd_vel` **有 controller 訂閱** ✅
- **ROS graph**:`/cmd_vel`、`/ackermann_cmd`、`/depth`(**無 `/rgb`、`/odom`、`/scan`**)
- **LLM**:ollama 本地 6 模型(qwen3:8b=chat/plan/route、qwen3.6=vision 等);nvidia-cloud 備援
- **locations**:`No locations configured`(要測 `/loc`、`/route`、`/patrol` 得先建點)

→ 一句話:**對話層、感知層(depth 除外要 RGB)、直接駕駛層現在可測;導航層(route/mission/patrol/dock)缺 Nav2+地圖;twin 閘門缺 Isaac 場景。**

---

## CLI 命令(shell 直接執行)

| 狀態 | 命令 | 期望輸出 |
|---|---|---|
| ✅ | `JenAI version` | `JenAI 0.16.0`(版本來自 package metadata,隨 release 走) |
| ✅ | `JenAI help` | 一頁總覽:CLI 命令表 + 一鍵常用範例(doctor → TUI /help → /route → /patrol → /stop)+ 文件指路 |
| ✅ | `JenAI doctor` | 分區檢查表(environment/config/ros2/nav/provider/locations/webui),每項 pass/warn/fail + 修法指引;**無後端時誠實 warn/fail,不假裝** |
| ✅ | `JenAI doctor --json` | 機器可讀 JSON:`{overall, items[{section, check_name, status, message, fix_suggestion}], checked_at}` |
| ✅ | `JenAI config` | 設定 JSON(active_provider、profiles、vehicle、twin…) |
| ✅ | `JenAI providers` | 表格:local(ollama)* / nvidia-cloud,`*` 標 active |
| ✅ | `JenAI models` | 綁定表:chat/plan/vision/route/default → 各自模型 |
| ✅ | `JenAI loc list` | 地點表;無地點時 `No locations configured.` |
| ✅ | `JenAI loc show <名>` | 該地點座標/別名/tags;不存在時誠實報錯 |
| 🔶 | `JenAI route "<text>"` | 解析目的地 → 互動確認 → 送 Nav2;**前置:Nav2 + 地圖 + 地點**。現在會誠實報 Nav2 unavailable |
| ✅ | `JenAI web` | 印出帶 `?token=…` 的網址;WebUI 起在 127.0.0.1:8760;含 STOP 鈕、地圖、批准卡 |
| ✅ | WebUI auth | 無 token / 錯 token → 401;`?token=` 開頁 → 200 並種 session cookie;`Authorization: Bearer` 亦可;**POST `/api/stop` 免 token 照樣 200** |
| 🔶 | WebUI Camera 頁 | 需影像 topic。切到 Camera tab 才開始輪詢:**topic 下拉選單**(影像類優先,免猜 /rgb vs /rgb/image,選擇記憶於瀏覽器)+ `/api/frame` 每秒一幀 + odom 小格同步刷新;無相機時誠實 unavailable |
| ✅ | WebUI API 頁 | 切到 API tab:ORDS 風格端點目錄(GET/POST 徽章、路徑、說明、auth 註記)+ **即時 ROS topics 清單**(`/api/topics`);指路 MCP 與文件 |
| ✅ | `JenAI mcp` | MCP stdio server 起動,Claude Code/Desktop 可接;預設唯讀,`--allow-actions` 才有 `navigate_to` |
| ✅ | `JenAI daemon --rules <toml>` | 常駐規則引擎;規則觸發時 notify/halt/goto(goto 需 `auto_approve` + nav2) |
| ✅ | `JenAI`(主入口) | 已設定 → 直接進 TUI;未設定 → setup wizard:ASCII banner → 3 步驟(供應商預設選單 local/NVIDIA/OpenAI/custom → 連線細節逐欄附範例 → 地點檔)→ 綠色摘要卡 + 金鑰放置提示 + 下一步指引 |

## Slash 指令(TUI/WebUI 輸入框)

### Safety

| 狀態 | 指令 | 測法 | 期望輸出 |
|---|---|---|---|
| ✅ | `/stop` | 任何時候輸入(含任務執行中) | **免批准**立即執行:取消 Nav2 goal + 連發零速度;執行中任務被搶佔;時間軸顯示 STOPPING → 停止完成 |

### Session

| 狀態 | 指令 | 測法 | 期望輸出 |
|---|---|---|---|
| ✅ | `/help` | 直接輸入 | 指令分類簡介 + 範例 + 快捷鍵表 |
| ✅ | `/status` | 直接輸入 | provider/model/config/doctor 摘要一屏 |
| ✅ | `/clear` | 直接輸入 | 清畫面**且清跨重啟記憶** |
| ✅ | `/quit` `/exit` | 直接輸入 | 離開 TUI,終端還原 |

### 對話(自然語言,非 slash)

| 狀態 | 測法 | 期望輸出 |
|---|---|---|
| ✅ | 直接打字聊天,如「你是誰?現在電量多少?」 | chat 模型(qwen3:8b)回答;查不到的機器人狀態**誠實說無法取得**,不編造 |
| ✅ | `/plan 導航到 A 並回報電量` | 產出任務計畫,**不執行任何 side effect** |
| 🔶 | `/run 帶我到大廳` | Supervisor handoff 給專職 agent 執行;side-effect 工具一律過批准卡。**前置:Nav2+地點**(無後端時誠實失敗) |
| ✅ | `/why` | 解釋 agent 當前決策原因 |
| ✅ | `/review` | 重新檢視 plan 並給修改建議 |
| ✅ | `/abort` | 中止目前 run |

### Provider / Model

| 狀態 | 指令 | 測法 | 期望輸出 |
|---|---|---|---|
| ✅ | `/provider` / `/provider local` | 顯示/切換(含編號快選) | 即時生效並持久化;重啟後仍是新 provider |
| ✅ | `/providers` | 直接輸入 | 同 CLI providers 表 |
| ✅ | `/model` / `/model qwen3:8b` | 直接輸入 | 列出**端點上真實可用**模型(打 ollama API)並切換 chat 綁定 |
| ✅ | `/models` | 直接輸入 | 綁定表(chat/plan/vision/route/default) |
| ✅ | `/permissions` | 直接輸入 | 列出哪些指令需批准 |
| ✅ | `/config` `/doctor` | 直接輸入 | TUI 內顯示設定重點 / 跑健檢 |

### ROS2

| 狀態 | 指令 | 測法 | 期望輸出 |
|---|---|---|---|
| ✅ | `/ros topics` | 直接輸入 | 本機現在應列出 `/cmd_vel` `/ackermann_cmd` `/depth` `/rosout` `/parameter_events`(含類型) |
| ✅ | `/ros topic-info /cmd_vel` | 直接輸入 | type=Twist、publishers、subscribers(本機:有 controller 訂閱) |
| ✅ | `/ros schema /cmd_vel` | 直接輸入 | 欄位人話摘要(linear.x = 前進速度 m/s…) |
| ✅ | `/ros echo /depth 3` | 直接輸入 | 3 筆訊息快照;沒資料的 topic 誠實 timeout |
| ✅ | `/ros pub /cmd_vel {"linear":{"x":0.2}}` | 輸入後批准卡按 1 | **批准卡先出**;速度過 `[vehicle]` 硬限速夾限;車輪應動(⚠️ 實體會動,場地淨空) |
| ✅ | `/ros drive /cmd_vel {"linear":{"x":0.2}} 2` | 同上 | 定頻發布 2 秒後**自動送 0 停車**(⚠️ 實體會動) |
| 🔶 | `/ros state` | 直接輸入 | /odom + /scan 快照;**本機現在無此二 topic → 誠實回報 unavailable** |

### Route / 地點

| 狀態 | 指令 | 測法 | 期望輸出 |
|---|---|---|---|
| 🔶 | `/route 去機械系館` | 建點 + Nav2 後測 | 解析目的地 → 批准 → 導航,**即時剩餘距離**,Esc 真取消 goal。前置:Nav2+地圖+地點 |
| ✅ | `/drive 前進兩秒` | 批准後 | 自然語言 → 速度指令定時發布到 `vehicle.cmd_vel_topic`,結束自動停(⚠️ 實體會動) |
| ✅ | `/loc list` | 直接輸入 | 地點表;現在為空 |
| 🔶 | `/loc add here 測試點` | 需 /amcl_pose 或 /odom | 抓當下位置存檔;**本機現在兩者皆無 → 誠實失敗** |
| ✅ | `/loc add gps <名> <緯> <經>` | 先在 config 設 `[map_datum]` | 未設基準點 → 誠實拒絕 + 設定教學;設好 → 換算 map 座標存檔(提示:實地驗證第一次導航,基準誤差會整批平移) |
| ✅ | `/loc show <名>` | 建點後 | 座標/別名/tags |

### Skills(任務技能)

| 狀態 | 指令 | 測法 | 期望輸出 |
|---|---|---|---|
| 🔶 | `/mission 廚房, drive 左轉, 大廳` | Nav2+地點後測 | 批准一次跑整趟,逐步回報;drive 段可混排 |
| 🔶 | `/patrol A, B x3 photo` | Nav2+地點+RGB 相機後測 | 點位×圈數;photo 時每到達點抓幀→VLM 觀察即時顯示 👁;一點失敗記錄後續行,**統計誠實 n/m**;Esc/`/stop` 可搶佔 |
| 🔶 | `/dock` | 建 `tags=["dock"]` 地點後測 | 導航到 dock 點;無 dock 點時**誠實提示建法**(`/loc add here Dock`) |
| ✅ | `/report` / `/report list` | 沒 log 時直接輸入;有 log 後再測 | 無 log → `No patrol logs yet`;有 log → 日報(時間/路線/n:m/逐點 ✓✗/👁 觀察)+ LLM 摘要段;provider 離線 → 誠實標示只有確定性內容 |

### Vision / Perception

| 狀態 | 指令 | 測法 | 期望輸出 |
|---|---|---|---|
| ✅ | `/vision image /tmp/scene.jpg` | 準備一張圖 | VLM(qwen3.6)結構化觀察輸出 |
| 🔶 | `/vision camera` | 需 RGB topic | 抓一幀→VLM 分析;**本機現在只有 /depth 無 /rgb → 誠實失敗**;`/vision camera /depth` 可測抓幀路徑 |
| 🔶 | `/perception start /rgb 1` | 需 RGB topic | 持續迴圈:定頻抓幀→SceneAnalysis(場景/物件/affordances/建議動作);**只觀察不動作**,建議動作標「需批准」;錯誤連發只報一次 |
| 🔶 | `/perception stop` / `status` | 迴圈中 | 停止並回報分析幀數 / 顯示迴圈狀態 |

### System

| 狀態 | 指令 | 測法 | 期望輸出 |
|---|---|---|---|
| ✅ | `/shell ls -la` | 批准後 | 命令輸出進時間軸 |
| ✅ | `!ls -la` | 批准後 | 同 `/shell` |

### Twin Gate(M3)

| 狀態 | 項目 | 期望輸出 |
|---|---|---|
| 🧪 | Gate pipeline(G1–G5 判準、pass/block/refer、refer→daemon 視為 block) | 16 個單元測試涵蓋;`[twin]` 預設關閉 = 零行為變化 |
| 🔶 | 端到端預演(真孿生) | **前置:Isaac Sim 場景建置(TWIN_SETUP.md,工作站作業)**;建好後:`[twin]` 啟用 → 每個導航目標先在隔離 ROS_DOMAIN_ID 預演,硬違規(碰撞/禁區)block、無法判定 refer,**閘門啟用時絕不靜默放行**;`doctor` twin 區段回報孿生 graph/Nav2/接觸感測器 |

### 批准機制(橫切驗收)

| 狀態 | 測法 | 期望輸出 |
|---|---|---|
| 🧪✅ | 任何需批准指令(`/ros pub`、`/drive`、`/route`、`/mission`、`/patrol`、`/dock`、`/shell`、`/run` 內 side-effect) | 一律先出 Claude Code 風格編號批准卡:1 Yes / 2 Yes 本 session 不再問 / 3(Esc)No;`/dock` 與 `/route` 的「不再問」**互不洩漏**(獨立 auto_key) |
| ✅ | 任務執行中打其他輸入 | 顯示 `Busy —` 回饋,不是無聲吞掉;任何 `/stop` 寫法照樣搶佔;**Esc 取消不了緊急停止本身** |

### 介面對等(WebUI / MCP / daemon)

| 狀態 | 項目 | 測法 | 期望輸出 |
|---|---|---|---|
| ✅ | WebUI STOP | `JenAI web` → 手機開 → 按紅色 STOP | 免確認立即停;冷啟動也走 PoseCache 活 bridge 或即時 fallback |
| ✅ | MCP 唯讀 | Claude Code 接上 `JenAI mcp` | 只有查詢工具 + `stop`;`navigate_to` 需 `--allow-actions` |
| ✅ | daemon 規則 | `JenAI daemon --rules rules.example.toml` | 數值規則(`battery < x → 回充`)與感知規則(`@perception` + affordance + min_confidence)同一套 gating;`halt` 免批准,`goto` 需 `auto_approve`+nav2 |

---

## 現在不能測的,各缺什麼(補齊順序照 ONBOARDING.md)

| 缺口 | 解鎖的測項 | 補法 |
|---|---|---|
| RGB 相機 topic(現只有 /depth) | `/vision camera`、`/perception`、`/patrol photo` | 起 Isaac bridge 的 RGB 相機或接實機相機(`vehicle.camera_topic`) |
| /odom + /scan | `/ros state`、`/loc add here`(odom 退路) | 起車端 odometry 與雷射 |
| 地圖 + AMCL | `/loc add here`(正路)、定位回報 | slam_toolbox 建圖 → AMCL(ONBOARDING.md 手把手) |
| Nav2 | `/route` `/mission` `/patrol` `/dock`、daemon `goto` | Nav2 bringup(Ackermann:Smac Hybrid-A* + RPP) |
| Isaac Sim 孿生場景 | Twin Gate 端到端、M6 消融實驗 | TWIN_SETUP.md(工作站作業,M3 唯一剩餘項) |
