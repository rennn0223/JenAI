# mcp_server — 對外的 MCP stdio 服務

`JenAI mcp` 把機器人工具以 [MCP](https://modelcontextprotocol.io) 開放給
Claude Code / Claude Desktop 等 client。`server.py` 用 FastMCP 實作。

安全語意:**預設唯讀**(查 topics/pose/地點/相機描述)+ `stop`(唯讀模式也
提供 —— 停車永遠安全);`--allow-actions` 才註冊 `navigate_to`(單飛鎖:同時
只准一個導航)。工具簽名是 public surface(`docs/VERSIONING.md`);信任邊界
見 `docs/THREAT_MODEL.md`(stdio 無網路面,信任宿主行程)。
