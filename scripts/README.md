# scripts — 開發/驗證用腳本(不進 wheel、不屬 public surface)

| 檔案 | 用途 |
|---|---|
| `soak.py` | 24h 穩定性測試(V1_GATE A6):啟動 daemon、樹狀 RSS 採樣、warmup 校正、PASS/WARN 判定。`python3 scripts/soak.py --rules rules.example.toml`;短跑自檢:`--minutes 5 --interval 5 --warmup 60`。輸出 `soak-*/{rss.csv, report.md, daemon.log}` |
| `e2_ablation.py` | E2 配對消融(論文 5.4):同一批五類目標比較 A/no-gate、B/rules-only、C/full-twin；C 回 HOME 失敗即排除。先以系統 Python `--sample-only`,再以 `uv run` 執行同一 `--out` 目錄。 |
| `e3_mock_ugv.py` | E3 隔離 ROS2 fixture：整合 `/cmd_vel` 至 `/odom`，支援重置、停用回授及第一次動作後故障注入。固定使用非生產 ROS domain。 |
| `e3_agent_bench.py` | E3 自然語言閉環工具實驗：八題分別檢查 live graph 發現、單次有界致動、動作後觀察，以及無回授時不盲目重送。 |
| `e4_bench.py` | E4 決策延遲量測:固定快照重複 decide(),量中位/P95。`uv run python scripts/e4_bench.py --n 100 --out e4-local.jsonl` |
| `b4_driver.sh` | B4 模擬巡航:對 tmux 裡的真 TUI(auto 模式)送目前四角地點 patrol 圈,log 到 `/tmp/b4_mileage.log`;只有 N/N 記 `completed`，其餘記 `partial`。預設 102 圈／72000 秒上限、單實例鎖及 EXIT `/stop`。`bash scripts/b4_driver.sh [session] [log]` |
| `usability_study.py` | 以隨機化六序列 Williams 區塊產生平衡受試順序、計時並彙整手動 ROS2／Slash／自然語言三種條件；拒絕不完整六人區塊，只存匿名量化欄位，不收原始 prompt 或終端內容。流程見 [USABILITY_STUDY](../docs/validation/USABILITY_STUDY.md)。 |
| `isaac_hil_acceptance.py` | 唯讀 preflight 或明確批准的 Isaac Sim live 驗收；經正式 NavigationGateway 跑 route、cancel、hard stop 與可選 Twin verdict，輸出不可覆寫 JSON。見 [ISAAC_HIL_ACCEPTANCE](../docs/validation/ISAAC_HIL_ACCEPTANCE.md)。 |

指令細節與數據回填對照見 [docs/validation/EXPERIMENTS.md](../docs/validation/EXPERIMENTS.md)。
soak/e2/e4/usability 為 stdlib+repo 內依賴;b4_driver 只需 tmux。

## `scripts/jenai` 與 release wheel

`scripts/jenai` 是 source checkout 的開發便利 wrapper：必要時 source ROS2，再以 `uv run`
啟動目前工作樹。它不會進 wheel，也不是公開安裝介面。只有在**沒有**用 `uv tool install`
安裝 JenAI 時，才可選擇建立 repo symlink：

```bash
mkdir -p ~/.local/bin
ln -s "$PWD/scripts/jenai" ~/.local/bin/jenai
```

release wheel 會自行建立 `JenAI` 與 `jenai` 兩個 entry point，不需要任何 symlink。上述
repo symlink 與 wheel 的小寫 `jenai` 使用同一路徑，兩者不能共存。切換前先執行
`ls -l ~/.local/bin/jenai`；只有確認它指向此 repo 的 `scripts/jenai` 後才移除。不要用
`ln -sf`
覆寫 uv 管理的 command。
