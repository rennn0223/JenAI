# scripts — 開發/驗證用腳本(不進 wheel、不屬 public surface)

| 檔案 | 用途 |
|---|---|
| `soak.py` | 24h 穩定性測試(V1_GATE A6):啟動 daemon、樹狀 RSS 採樣、warmup 校正、PASS/WARN 判定。`python3 scripts/soak.py --rules rules.example.toml`;短跑自檢:`--minutes 5 --interval 5 --warmup 60`。輸出 `soak-*/{rss.csv, report.md, daemon.log}` |
| `e2_ablation.py` | E2 配對消融(論文 5.4):同一批五類目標比較 A/no-gate、B/rules-only、C/full-twin；C 回 HOME 失敗即排除。先以系統 Python `--sample-only`,再以 `uv run` 執行同一 `--out` 目錄。 |
| `e3_mock_ugv.py` | E3 隔離 ROS2 fixture：整合 `/cmd_vel` 至 `/odom`，支援重置、停用回授及第一次動作後故障注入。固定使用非生產 ROS domain。 |
| `e3_agent_bench.py` | E3 自然語言閉環工具實驗：八題分別檢查 live graph 發現、單次有界致動、動作後觀察，以及無回授時不盲目重送。 |
| `e4_bench.py` | E4 決策延遲量測:固定快照重複 decide(),量中位/P95。`uv run python scripts/e4_bench.py --n 100 --out e4-local.jsonl` |
| `b4_driver.sh` | B4 模擬巡航:對 tmux 裡的真 TUI(auto 模式)送目前四角地點 patrol 圈,log 到 `/tmp/b4_mileage.log`;只有 N/N 記 `completed`，其餘記 `partial`。預設 102 圈／72000 秒上限、單實例鎖及 EXIT `/stop`。`bash scripts/b4_driver.sh [session] [log]` |

指令細節與數據回填對照見 [docs/EXPERIMENTS.md](../docs/EXPERIMENTS.md)。
soak/e2/e4 為 stdlib+repo 內依賴;b4_driver 只需 tmux。
