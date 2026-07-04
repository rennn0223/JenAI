# cli — Typer 進入點

`JenAI` / `jenai` 命令的家。`main.py` 定義所有子命令:TUI(預設)、`doctor`、
`web`(含 token 認證啟動)、`mcp`、`daemon`、`route`、`loc`、`config`、
`providers`、`models`、`version`。

慣例:callback 統一載入 `~/.config/jenai/.env`(shell 已 export 者優先);
診斷訊息一律走 stderr(`err_console`)—— 保護 `jenai mcp` 的 stdout 純淨
(MCP 協定跑在上面)。命令清單與行為見 `docs/COMMANDS.md`。
