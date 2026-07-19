# JenAI 測試手冊(TEST.md)

> 對應版本:v2.0.2(快照隨 release 更新)。所有可測項目(CLI / Slash / 對話)與期望輸出的總表,
> 附本機(DGX Spark 工作機)實測現況快照。自動化測試見「自動化測試」節;
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
| 自動化測試(全) | `env -u PYTHONPATH uv run pytest` | v2.0.2 候選本機 652/652 全綠；v2.0.1/main@`79a295a` 的 597/597 與 [main CI 29654418747](https://github.com/rennn0223/JenAI/actions/runs/29654418747) 是上一版獨立證據。PR 三版本 CI 仍須在候選 branch 重跑；含安全鏈故障注入、輸入邊界、指令 FIFO／批准暫停／stop 清隊列與架構鐵律測試 |
| Isaac HIL（人工啟動） | Actions → `Isaac HIL Acceptance`，或依 `docs/ISAAC_HIL_ACCEPTANCE.md` 執行 | 一般 CI 絕不動車；精確確認後在 self-hosted runner 驗 route、Nav2 cancel acknowledgement、software halt、完整 scan metadata gate 與可選 Twin verdict。clean `d942130…855` 本機 artifact 已通過，Twin 同 domain 明記 skip；這不等於已產生 GitHub workflow artifact |
| Lint | `env -u PYTHONPATH uv run ruff check src tests` | 無輸出(exit 0) |
| CI | push PR | `test` job 以 Python 3.12／3.13／3.14 matrix 跑 ruff+pytest(coverage 表進 job summary,基準 76%)、`build` job(uv build + uvx 全新環境裝 wheel 跑 `jenai --help`)皆綠 |
| Release gate | 推 `vX.Y.Z` tag,或手動 dispatch(輸入 tag) | release workflow:版本一致檢查 → lint+測試 → build → wheel 冒煙測試 → tag push 建草稿(人工發佈);dispatch 由 workflow 建 tag 並以 `docs/releases/<tag>.md` 直接發佈 |
| 安全鏈覆蓋閘 | CI `test` job 自動跑 | `coverage report --fail-under=90`(estop/watchdog/bridge/gate/rules);現況 93%,倒退即紅 |
| 稽核紀錄 | 自動化測試 + 執行任一 TUI run | `<config 目錄>/audit.sqlite3` 保存 run/approval/tool/gate 事件,重啟後仍在;最多 10,000 筆且不含 prompt/raw payload |
| 24h soak(A6) | `python3 scripts/soak.py --rules <rules.toml>`(ROS-sourced shell、掛機時跑) | `soak-*/report.md`:RSS baseline/final/peak、增長 %、**PASS/WARN**(>20% 增長 = WARN);短跑驗證:`--minutes 5 --interval 5 --warmup 60` |

## 本機實測現況快照（更新至 2026-07-19，DGX Spark／Isaac Sim 倉庫場景）

- **doctor**:ROS/Nav2/地圖／相機皆可用；同 domain 0 純模擬驗收時 `twin_isolation` 的警告／失敗屬預期,不得寫成隔離通過
- **ROS graph**:Isaac Sim 倉庫場景(`/cmd_vel`=Twist、`/amcl_pose`、`/map`、`/scan`、
  `/front_stereo_camera/left/image_raw`、Nav2 全家 + docking_server)
- **FullScan HIL（2026-07-19）**：clean `d942130…855`、JenAI 2.0.2；10/10 samples、362 bins/筆、57.0442% valid-finite，角寬／increment／geometry／range／scan_time／frame／timestamp gate 全通過；`map_left_down` 82.881 s、Dock 45.804 s，皆 0 recoveries；Nav2 cancel acknowledged=`true`、停止漂移 0.0000 m；overall `pass_with_skips`（Twin 同 domain 0）
- **Hero 固定序列**：clean `cc6d217…f6e` 首次因 pose feed 暫失而 fail closed、0 goal sent；恢復後 `map_left_down`／Dock 交替 10 legs 為 10/10 succeeded（45.155–111.668 s、0–4 recoveries）。這不是 10 次自然語言或完整 demo。
- **自然語言單-goal**：測試 TUI 以「請回到 dock」產生正確批准卡並 succeeded；Nav2 goal count 18→19，只新增 1 個 goal。執行時是 dirty patch、後續提交為 `d942130…855`，故只作互動補充。
- **LLM**:ollama 本地(全 binding = qwen3.6:35b);nvidia-cloud 備援
- **locations**:5 點:`dock` 加 RViz2 地圖左上、右上、左下、右下四點;四角均具 1.0 m free-space 周界,12 組有向 Nav2 規劃全數成功
- **E3 v1.1.1**:完整 8 題批次 7/8 通過、8/8 無重複致動；唯一失敗為模型在致動前
  呼叫不存在工具。另行限定三項運動的重跑為 3/3，觀察量 0.100025 m、約 0.2000 rad
  與 0.0250 m，均符合隔離 mock 的理論命令量。
- **B4 preflight**:目前四角一圈 3/4；左下因 Twin Gate G5 Nav2 aborted 被 refer，
  執行側 goal 未送出。這是補充測試，不取代固定 subset 的 102 份 reports／407-of-408
  waypoint succeeded；約 20 h 只來自歷史 driver 摘要，不能當成精確暴露量或零事件證據。

→ 一句話:**TUI 的 Slash、自然語言、ROS2 introspection、Nav2、vision、perception、
patrol/explore/inspect 與報告已在 2026-07-17 以真實 Isaac Sim graph 逐項驗收;完整紀錄見
[TUI_LIVE_ACCEPTANCE_2026-07-17.md](TUI_LIVE_ACCEPTANCE_2026-07-17.md)。WebUI、MCP 與 daemon
保留既有 2026-07-15 驗收證據。**

正式 FullScan route／cancel／stop 的 2026-07-19 結果另見
[TUI_LIVE_ACCEPTANCE_2026-07-19.md](TUI_LIVE_ACCEPTANCE_2026-07-19.md)；它是模擬 HIL，
不是實體或 Twin 隔離驗證。

**已修正與仍需注意(更新至 2026-07-19)**:
1. ~~route 解析器對 destination-only 句式(`去X`、`Go to X`)失敗~~ → **v0.36.3 已修**
   (goal-only regex 快路 + LLM fallback 接受空 start;起點一律取機器人當前位置)。
2. ~~agent 的 `ros_state` 讀不到位姿~~ → **v0.36.3 已修**(新增 `pose` 欄位,latched QoS
   讀 `/amcl_pose`;`/odom` 被載具改名(Carter=`/chassis/odom`)時位姿不再落空)。
3. ~~`/run` 批准後工具不執行~~ → **已修並於 2026-07-17 真實驗證**:自然語言探索
   正確呼叫 Explore,走兩個不同地點並以 `2/2` 完成;批准恢復與工具續跑鏈路成立。
4. qwen3.6:35b 仍可能偶發幻覺工具名（本次為
   `ros_shutdown_tool_not_available`）→ SDK 誠實將 run 標為 failed，且在批准前
   不會致動；未知工具的有界重試仍是後續改善項目。
5. 本機 qwen3.6:35b 的單一推理回合需數十秒至約兩分鐘，多工具導航會再疊加 Twin
   Gate／Nav2 時間；Slash 指令不經 LLM。此延遲再次支持 LLM 只能位於高階層。

---

## CLI 命令(shell 直接執行)

| 狀態 | 命令 | 期望輸出 |
|---|---|---|
| ✅ | `JenAI version` | `JenAI 2.0.2`(版本來自 package metadata,隨 release 走) |
| ✅ | `JenAI help` | 一頁總覽:CLI 命令表 + 一鍵常用範例(doctor → TUI /help → /route → /patrol → /stop)+ 文件指路 |
| ✅ | `JenAI scaffold "<描述>"` | 自然語言生成 ROS2 套件:印出 plan → 確認 → 寫入;boilerplate 定死永遠可 build、node 主體 LLM 寫需審閱;拒絕覆蓋。實測:local qwen 生成 greeting_publisher 全樹 ✅ |
| ✅ | `JenAI eval scenarios.example.toml` | 決策腦 E1 評測:各場景家族 accuracy / unsafe rate / refer rate 表格(`--json` 機器可讀、`-k` 重複取樣);越界動作與幻覺目的地一律降級 refer_to_human |
| ✅ | `JenAI scaffold … --build` | **生成即驗證**:寫完即 colcon build;失敗餵錯誤給 LLM 修一輪再 build,結果誠實回報。實測:生成套件真 colcon build 1.5s 過 ✅ |
| ✅ | `JenAI doctor` | 分區檢查表(environment/config/ros2/nav/provider/locations/webui),每項 pass/warn/fail + 修法指引;**無後端時誠實 warn/fail,不假裝** |
| ✅ | `JenAI doctor --json` | 機器可讀 JSON:`{overall, items[{section, check_name, status, message, fix_suggestion}], checked_at}` |
| ✅ | `JenAI onboard` | 備份 `config.toml` 後重跑 setup wizard;`.env`、locations、skills、reports、run history 保留;`--yes` 可略過替換確認 |
| ✅ | `JenAI config` | 設定 JSON(active_provider、profiles、vehicle、twin…) |
| ✅ | `JenAI providers` | 表格:local(ollama)* / nvidia-cloud,`*` 標 active |
| ✅ | `JenAI models` | 綁定表:chat/plan/vision/route/default → 各自模型 |
| ✅ | `JenAI loc list` | 地點表;無地點時 `No locations configured.` |
| ✅ | `JenAI loc show <名>` | 該地點座標/別名/tags;不存在時誠實報錯 |
| 🔶 | `JenAI route "<text>"` | 解析目的地 → 互動確認 → 送 Nav2;**前置:Nav2 + 地圖 + 地點**。現在會誠實報 Nav2 unavailable |
| ✅ | `JenAI web` | 印出帶 `?token=…` 的網址;WebUI 起在 127.0.0.1:8760;含 STOP 鈕、地圖、批准卡 |
| ✅ | WebUI auth | 無 token / 錯 token → 401;`?token=` 開頁 → 200 並種 session cookie;`Authorization: Bearer` 亦可;JSON body >64 KiB → 413、非 JSON object → 400;**POST `/api/stop` 免 token、先停車再讀 body 且撤銷舊確認** |
| ✅ | WebUI Camera 頁 | 需影像 topic。切到 Camera tab 才開始輪詢:**topic 下拉選單** + `/api/frame` 每秒一幀 + odom 小格同步刷新。API 端實測(Carter):`/api/frame` 回 1920×1200 真影像(冷啟第一發 503,輪詢自癒);`/api/topics` 供下拉;頁面 UI 於瀏覽器驗 |
| ✅ | WebUI API 頁 | 切到 API tab:ORDS 風格端點目錄(GET/POST 徽章、路徑、說明、auth 註記)+ **即時 ROS topics 清單**(`/api/topics`);指路 MCP 與文件 |
| 🧪 | WebUI 狀態快取 | 多個分頁輪詢共用 probe:doctor 30 秒最多一次、ROS graph 2 秒最多一次;STOP/confirm/frame 不經狀態快取鎖 |
| ✅ | `JenAI mcp` | MCP stdio server 起動,Claude Code/Desktop 可接;預設唯讀,`--allow-actions` 才有 `navigate_to` |
| ✅ | `JenAI daemon --rules <toml>` | 常駐規則引擎;規則觸發時 notify/halt/goto(goto 需 `auto_approve` + nav2) |
| ✅ | `JenAI`(主入口) | 已設定 → 直接進 TUI;未設定 → setup wizard:ASCII banner → 3 步驟(供應商預設選單 local/NVIDIA/OpenAI/custom → 連線細節逐欄附範例 → 地點檔)→ 綠色摘要卡 + 金鑰放置提示 + 下一步指引 |
| ✅ | TUI 吉祥物動畫 | 歡迎面板臘腸狗:待機搖尾巴(0.6s/格)+ 偶爾眨眼;**任務執行中切換跑步步態**(吉祥物即狀態指示);窄版隱藏時不浪費重繪;各姿勢同尺寸不抖動 |
| ✅ | 歡迎面板精簡 metadata | 首頁只顯示 model/provider 與 config path；locations/skills 由 /loc list、/skills 查詢，完整健康狀態由 /doctor 查詢；不以啟動快速檢查冒充完整 doctor |

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
| ✅ | 權限模式(Shift+Tab) | 循環 審批→規劃→自動;底部狀態列顯示 chip;切到自動時時間軸警告記錄 |
| ✅ | 寒暄(任一模式) | 「hi」「你好」→ 免工具直接串流回覆(帶 JenAI persona,不經 specialist/批准卡);對話寫入 session 記憶,下一輪 agent 記得 |
| ✅ | 自然語言(審批模式,預設) | 「帶我去機械系館」→ **交給 /run agent**(要動的先彈批准卡;純問題由 supervisor 回答)—— 不再只是教你打什麼指令 |
| ✅ | 自然語言(規劃模式) | 同句話 → /plan:產出步驟與教學,**零執行**(plan agent 無工具,結構保證) |
| ✅ | 自然語言(自動模式) | 同句話 → agent 執行；僅有界非 host P0/P1 可自動批准並寫進時間軸，HOST_COMMAND/P2 仍逐次詢問且不可 remember；急停/硬限速/Twin Gate 不受影響 |
| ✅ | `/plan 導航到 A 並回報電量` | 產出任務計畫,**不執行任何 side effect** |
| ✅ | `/run 帶我到大廳` | Supervisor handoff 給專職 agent 執行;side-effect 工具一律過批准卡。**前置:Nav2+地點**(無後端時誠實失敗)。實測(Isaac Carter,qwen3.6:35b,v0.36.3):NL「帶我去dock」全鏈 E2E ✅——解析→批准→執行→Nav2 到點 0.06m;模型第一次給壞 JSON 被誠實拒絕後自行重試成功 |
| ✅ | `/why` | 解釋 agent 當前決策原因 |
| ✅ | `/review` | 重新檢視 plan 並給修改建議 |
| ✅ | `/abort` | 中止目前 run |

### Provider / Model

| 狀態 | 指令 | 測法 | 期望輸出 |
|---|---|---|---|
| ✅ | `/provider` / `/provider local` | 顯示/切換(含編號快選) | 即時生效並持久化;重啟後仍是新 provider |
| ✅ | `/providers` | 直接輸入 | 同 CLI providers 表 |
| ✅ | `/model` / `/model qwen3.6:35b` | 直接輸入 | 列出**端點上真實可用**模型(打 ollama API)並切換 chat 綁定 |
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
| ✅ | `ros_state`(`/run` agent 工具,非 slash 指令) | `/run 看一下機器人現在的狀態` | agent 呼叫 `ros_state` 回 **pose(/amcl_pose,latched)** + /odom + /scan 快照;缺哪項誠實回 None。實測(Carter):pose ✅、odom 誠實 None(載具用 /chassis/odom 別名)、scan ✅ |

### Route / 地點

| 狀態 | 指令 | 測法 | 期望輸出 |
|---|---|---|---|
| ✅ | `/route 從應科大樓到機械系館` | 建點 + (Nav2 或 route_adapter=odom) | **兩端已知 → 先去 A 再去 B**(兩段導航);只認得目的地 → 從當前位置去。即時剩餘距離、Esc 真取消。2026-07-19 clean `d942130…855` HIL：Nav2 cancellation acknowledged=true、零速度 software halt，停止後漂移 0.0000 m ✅（模擬） |
| ✅ | `/drive 前進兩秒` | 批准後 | 自然語言 → 速度指令定時發布到 `vehicle.cmd_vel_topic`,結束自動停(⚠️ 實體會動) |
| 🔶 | 局部避障(`[avoidance] enabled=true`,route_adapter=odom) | 需 depth topic + 障礙 | odom 直驅時 depth→走廊判定→stop-and-go detour;depth 超過 `depth_timeout_s` 未更新即歸零並回報 `sensor_unavailable`,不沿用舊畫面盲走 |
| ✅ | `/loc list` | 直接輸入 | 地點表;2026-07-17 正式設定為 `dock` + 地圖四角共 5 點 |
| ✅ | `/loc add here 測試點` | 需 /amcl_pose 或 /odom | 抓當下位置存檔。實測(Isaac Carter,v0.36.3):靜止讀 /amcl_pose 四點入檔 ✅(QoS 修復後) |
| ✅ | `/loc add gps <名> <緯> <經>` | 先在 config 設 `[map_datum]` | 未設基準點 → 誠實拒絕 + 設定教學;設好 → 換算 map 座標存檔(提示:實地驗證第一次導航,基準誤差會整批平移) |
| ✅ | `/loc show <名>` | 建點後 | 座標/別名/tags |
| 🔶 | `/loc move <名>` | 建點後,車移到新位置 | 既有地點更新為當前位姿(座標改、tags/aliases 保留);不存在 → 誠實列出已知名稱 |
| 🔶 | `/loc rename <舊> <新>` | 建點後 | 改名保留座標;撞名/撞 alias → 誠實拒絕;含空白名稱用 `舊 -> 新` |
| 🔶 | `/loc rm <名>` | 建點後 | 精確名稱才刪(alias/模糊不觸發);回報被刪座標;不存在 → 列已知名稱 |

### Skills(任務技能)

| 狀態 | 指令 | 測法 | 期望輸出 |
|---|---|---|---|
| ✅ | `/mission 廚房, drive 左轉, 大廳` | Nav2+地點後測 | 批准一次跑整趟,逐步回報;drive 段可混排。實測(Isaac Carter):兩點 mission 批准一次 → 2/2 succeeded ✅ |
| ✅ | `/patrol A, B x3 photo` | Nav2+地點+RGB 相機後測 | 點位×圈數;photo 時每到達點抓幀→VLM 觀察即時顯示 👁;一點失敗記錄後續行,**統計誠實 n/m**;Esc/`/stop` 可搶佔。實測(Isaac Carter):x1 photo 兩點 → 2/2、每點 👁 場景描述正確、log 自動存 ✅ |
| ✅ | `/dock` | 建 `tags=["dock"]` 或名為 dock 的地點後測 | 導航到 dock 點;無 dock 點時**誠實提示建法**(`/loc add here Dock`)。實測(Isaac Carter):`Arrived at the goal` ✅,與 `/vision` 排隊互不干擾 |
| ✅ | `/report` / `/report list` | 沒 log 時直接輸入;有 log 後再測 | 無 log → `No patrol logs yet`;有 log → 日報(時間/路線/n:m/逐點 ✓✗/👁 觀察)+ LLM 摘要段;provider 離線 → 誠實標示只有確定性內容 |
| ✅ | `/skills` + 自訂技能 | 建 `skills/inspect.toml` 後重啟 | `/skills` 列出技能與載入警告;`/inspect` 出現在 palette、執行時**先過批准卡**再跑 mission;壞檔/保留字/重名 → 警告不炸。2026-07-17 四角 inspect 實跑 `4/4` 成功 |

### Vision / Perception

| 狀態 | 指令 | 測法 | 期望輸出 |
|---|---|---|---|
| ✅ | `/vision image /tmp/scene.jpg` | 準備一張圖 | VLM(qwen3.6)結構化觀察輸出 |
| ✅ | `/vision camera` | 需 RGB topic | 抓一幀→VLM 分析。實測(Isaac Carter,`/front_stereo_camera/left/image_raw`,qwen3.6:35b):正確描述倉庫/堆高機/牆面,含 Objects/Anomalies/Suggested next ✅ |
| ✅ | `/perception start /rgb 1` | 需 RGB topic | 持續迴圈:定頻抓幀→SceneAnalysis(場景/物件/affordances/建議動作);**只觀察不動作**,建議動作標「需批准」;錯誤連發只報一次。實測(Isaac Carter,0.2Hz):`#path_clear #forklift_present` + 建議動作標 needs approval + 信心 95% ✅ |
| ✅ | `/perception stop` / `status` | 迴圈中 | 停止並回報分析幀數 / 顯示迴圈狀態。實測:status 顯示 running·topic·幀數,stop 誠實回報幀數 ✅ |

### System

| 狀態 | 指令 | 測法 | 期望輸出 |
|---|---|---|---|
| ✅ | `/shell ls -la` | 批准後 | 命令輸出進時間軸 |
| ✅ | `!ls -la` | 批准後 | 同 `/shell` |

### Twin Gate(M3)

| 狀態 | 項目 | 期望輸出 |
|---|---|---|
| 🧪 | Gate pipeline(G1–G5 判準、pass/block/refer、refer→daemon 視為 block) | 23 個 Twin Gate 單元測試涵蓋;`[twin]` 預設關閉 = 零行為變化 |
| ✅ | 端到端預演(真孿生) | 隔離部署時每個導航目標先在獨立 ROS domain 預演。2026-07-17 的單一 Isaac Sim 驗收刻意令 `twin.domain_id=0`,預演與執行作用於同一模擬車;此設定只供純模擬展示,不可聲稱通訊隔離通過。G1 contact sensor 未建時誠實 skip/warn;G3/G4/G5 與 audit verdict 仍照常評估 |

### 批准機制(橫切驗收)

| 狀態 | 測法 | 期望輸出 |
|---|---|---|
| 🧪✅ | 任何需批准指令(`/ros pub`、`/drive`、`/route`、`/mission`、`/patrol`、`/dock`、`/shell`、`/run` 內 side-effect) | Claude Code 風格風險感知編號卡；有界非 host P0/P1 可選 session remember，HOST_COMMAND/P2 僅一次性 Yes/No、P2 預選 No，auto/remember 皆不可繞過；`/dock` 與 `/route` 的 remember **互不洩漏** |
| ✅ | 任務執行中打其他輸入 | 自動 FIFO 排隊,底部顯示 `queue N`;`/queue` 查看、`/queue clear` 清除;批准卡未決時暫停。`/stop` 搶佔並清空舊意圖,Esc/`/abort` 只中止目前項目後續跑 |

### 介面對等(WebUI / MCP / daemon)

| 狀態 | 項目 | 測法 | 期望輸出 |
|---|---|---|---|
| ✅ | WebUI STOP | `JenAI web` → 手機開 → 按紅色 STOP | 免確認立即停;冷啟動也走 PoseCache 活 bridge 或即時 fallback |
| ✅ | MCP 唯讀 | Claude Code 接上 `JenAI mcp` | 只有查詢工具 + `stop`;`navigate_to` 需 `--allow-actions` |
| ✅ | daemon 規則 | `JenAI daemon --rules rules.example.toml` | 數值規則(`battery < x → 回充`)與感知規則(`@perception` + affordance + min_confidence)同一套 gating;`halt` 免批准,`goto` 需 `auto_approve`+nav2 |

---

## 尚未完成的實體／跨平台驗證

| 跨平台缺口 | 尚未能宣稱的結論 | 建議補法 |
|---|---|---|
| 實體小型 Ackermann 車 | 不可由模擬成功直接推論物理路徑誤差、煞停距離或碰撞安全 | 後續以相同 high-level API 做低速、空曠場地試驗並量測軌跡誤差 |
| 四足平台 adapter | 不可宣稱已完成不同運動學的通用控制 | 維持 capability schema,只新增 adapter,比較修改量與任務成功率 |
| 使用者對照組 | 不可量化宣稱降低學習曲線或提升效率 | 初學者／熟練者交叉比較原生 ROS2 CLI 與 JenAI 的時間、錯誤率與主觀負荷 |
| 虛實同時上線之通訊隔離 | domain 0 純模擬驗收不代表實體部署安全 | 分離 ROS domain、topic/action 白名單與硬體急停,再做誤送與斷線測試 |
