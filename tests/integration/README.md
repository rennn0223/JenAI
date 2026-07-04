# tests/integration — CLI 端到端

`test_cli.py` 以 subprocess 跑真的 `JenAI` 命令(version/doctor/config…),
驗證進入點、退出碼與輸出形狀 —— 抓 unit 層看不到的打包/import 問題
(CI 的 wheel 冒煙測試是它的近親)。跑法同 unit:`env -u PYTHONPATH uv run pytest`。
