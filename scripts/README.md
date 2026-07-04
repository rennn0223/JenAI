# scripts — 開發/驗證用腳本(不進 wheel、不屬 public surface)

| 檔案 | 用途 |
|---|---|
| `soak.py` | 24h 穩定性測試(V1_GATE A6):啟動 daemon、樹狀 RSS 採樣、warmup 校正、PASS/WARN 判定。`python3 scripts/soak.py --rules rules.example.toml`;短跑自檢:`--minutes 5 --interval 5 --warmup 60`。輸出 `soak-*/{rss.csv, report.md, daemon.log}` |

stdlib only —— 任何機器免安裝可跑。
