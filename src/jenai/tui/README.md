# tui — Textual 終端介面(主介面)

Claude Code 風格的 TUI:時間軸 bullet、slash palette(Tab 補完)、
風險感知編號批准卡(HOST_COMMAND/P2 不可 auto/remember)、Esc 真取消 subprocess/Nav2、`/stop` 任何時刻搶佔且先 reap publisher、
**權限三模式(Shift+Tab:審批/規劃/自動,`/mode` 為鍵盤備援)**——裸自然語言依模式路由。
Agent 執行時即時更新階段與 ToolBlock，但只呈現可驗證的過程摘要，不顯示模型私密 chain-of-thought；
純狀態查詢的最終數字由工具紀錄確定性渲染。

| 檔案 | 職責 |
|---|---|
| `app.py` | App 殼:輸入分發(裸語言依權限模式路由 / `!` shell / slash)、串流、spinner、active task 與共用掛載／狀態列生命週期 |
| `approval_policy.py` / `approval_flow.py` | 統一 agent/direct 的 auto/remember 邊界；HOST_COMMAND/P2 逐次批准；決策、恢復與取消收尾 |
| `direct_execution.py` | 已批准的 ROS pub/drive/route/mission/patrol/explore/shell 執行、稽核狀態與結果渲染 |
| `catalog.py` | 穩定的 `SLASH_COMMANDS` palette 資料源、standalone greeting 判斷與 Claude Code 風格 responsive CSS |
| `robot_commands.py` | Mixin:`/stop` `/ros` `/route` `/mission` `/patrol` `/explore` `/dock` `/drive` `/vision` `/perception` `/report` + bridge 生命週期(watchdog 佈署) |
| `location_commands.py` | Mixin:`/loc list/add/show/move/rename/rm`、AMCL/odom 位姿讀取、GPS→map 儲存；不負責導航執行 |
| `info_commands.py` | Mixin:`/help` `/status` `/doctor` `/model` `/provider` `/config` 等資訊類 |
| `panels.py` | 純視覺:雙欄 WelcomePanel(窄螢幕折疊)、TimelineItem、OutputPanel、無卡片框 palette |
| `help_content.py` | `/help` 的分組指令內容 |
| `widgets/` | ApprovalCard 等互動元件(見該目錄 README) |

測試手法:`app.run_test()` + `handle_user_text()`;monkeypatch 打在 handler
所在模組。UI 美學基準:TUI 像 Claude Code —— 動它之前看 `docs/UX.md`。
