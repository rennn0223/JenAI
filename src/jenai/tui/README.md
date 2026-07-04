# tui — Textual 終端介面(主介面)

Claude Code 風格的 TUI:時間軸 bullet、slash palette(Tab 補完)、
編號批准卡(1/2/3 鍵)、Esc 真取消(Nav2 goal 會停)、`/stop` 任何時刻搶佔。

| 檔案 | 職責 |
|---|---|
| `app.py` | App 殼:輸入分發(chat / `!` shell / slash)、串流聊天渲染、spinner、批准卡流程、mission/patrol 執行與日報存檔、`SLASH_COMMANDS` 表(palette 資料源) |
| `robot_commands.py` | Mixin:`/stop` `/ros` `/route` `/mission` `/patrol` `/dock` `/drive` `/loc` `/vision` `/perception` `/report` + bridge 生命週期(watchdog 佈署) |
| `info_commands.py` | Mixin:`/help` `/status` `/doctor` `/model` `/provider` `/config` 等資訊類 |
| `panels.py` | 純視覺:WelcomePanel(吉祥物)、TimelineItem(variant 決定行距)、OutputPanel、CommandPalette |
| `help_content.py` | `/help` 的分組指令內容 |
| `widgets/` | ApprovalCard 等互動元件(見該目錄 README) |

測試手法:`app.run_test()` + `handle_user_text()`;monkeypatch 打在 handler
所在模組。UI 美學基準:TUI 像 Claude Code —— 動它之前看 `docs/UX.md`。
