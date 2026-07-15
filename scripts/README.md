# scripts — 開發/驗證用腳本(不進 wheel、不屬 public surface)

| 檔案 | 用途 |
|---|---|
| `soak.py` | 24h 穩定性測試(V1_GATE A6):啟動 daemon、樹狀 RSS 採樣、warmup 校正、PASS/WARN 判定。`python3 scripts/soak.py --rules rules.example.toml`;短跑自檢:`--minutes 5 --interval 5 --warmup 60`。輸出 `soak-*/{rss.csv, report.md, daemon.log}` |
| `e2_ablation.py` | E2 Twin Gate 消融(論文 5.4):自佔位圖採樣五類目標、逐趟預演記 JSONL。`uv run python scripts/e2_ablation.py --per-class 20 --out e2-<date>/`(跑前停 B4 掛機) |
| `e4_bench.py` | E4 決策延遲量測:固定快照重複 decide(),量中位/P95。`uv run python scripts/e4_bench.py --n 100 --out e4-local.jsonl` |
| `b4_driver.sh` | B4 模擬里程掛機:對 tmux 裡的真 TUI(auto 模式)送 patrol 圈,log 到 `/tmp/b4_mileage.log`。`bash scripts/b4_driver.sh [session] [log]` |

指令細節與數據回填對照見 [docs/EXPERIMENTS.md](../docs/EXPERIMENTS.md)。
soak/e2/e4 為 stdlib+repo 內依賴;b4_driver 只需 tmux。
