# tests — 測試套件

**跑法(必背)**:`env -u PYTHONPATH uv run pytest` —— ROS sourced 的
PYTHONPATH 會遮蔽 venv 依賴,不 unset 會炸。全套設計成**無 ROS 也全綠**。

- `unit/` — 絕大多數測試(見該目錄 README)
- `integration/` — CLI 層端到端(subprocess 跑真的 `JenAI` 命令)
- `conftest.py` — 共用 fixtures

CI 同款入口 + 兩道閘:安全鏈覆蓋 `--fail-under=90`、架構鐵律測試。
手動驗收項目與期望輸出見 `docs/TEST.md`。
