# EXPERIMENTS — 實驗 runbook(照抄就能跑)

> 對象:客戶自跑、或任何接手的 session。每個實驗:指令 → 輸出在哪 → 數據回填到哪。
> 環境鐵則(CLAUDE.md):跑 app/實驗的 shell 先 `source /opt/ros/jazzy/setup.bash`
> 且**保留** PYTHONPATH;只有單元測試才 `env -u PYTHONPATH uv run pytest`。

## 前置:場景要活著(E2/E3/B4 需要;E1/E4 不碰 ROS 可免)

1. Isaac Sim 開 Carter 場景(照 [TWIN_SETUP](TWIN_SETUP.md) / [ISAAC_NAV2_SETUP](ISAAC_NAV2_SETUP.md)),Nav2 起好
2. 健檢:`uv run JenAI doctor` —— twin 三檢查要全綠

## E1|決策腦 eval(論文 5.3)

```bash
uv run JenAI eval scenarios.e1.toml                          # 正式庫(每族 ≥15 條)
uv run JenAI eval scenarios.e1.toml -k 3                     # 輸出穩定度採樣
uv run JenAI eval scenarios.e1.toml --json > e1-$(date +%Y%m%d).json   # 留原始數據
```

- 種子 8 條:`scenarios.example.toml`(schema 說明在檔頭);正式庫:`scenarios.e1.toml`
- 標註支援 `action:target` 細分(如 `expected = ["dock", "navigate_to:Dock"]`),
  避免「navigate_to(充電站) 被記成 unsafe」的標註歧義
- 看三個數:accuracy、**unsafe_rate(安全論文最重要的數)**、refer_rate
- 回填:THESIS_DRAFT 5.3 正式結果

## E2|Twin Gate 消融(論文 5.4;V1_GATE B5)

```bash
pkill -f b4_driver.sh          # 1) 先停 B4 掛機 —— 兩者搶同一台 Nav2
uv run python scripts/e2_ablation.py --per-class 1 --smoke        # 2) 冒煙:每類 1 發
uv run python scripts/e2_ablation.py --per-class 20 --out e2-$(date +%Y%m%d)/   # 3) 正式
```

- 輸出:`e2-<date>/trials.jsonl`(逐趟)+ `run.log`(末尾有摘要表)+ `targets.json`
- 五類目標與判準對應見 `scripts/e2_ablation.py` 檔頭
- 回填:THESIS_DRAFT 5.4、V1_GATE B5、SAFETY_CASE(H2/完稿條件)
- ✅ 已完成:`e2-20260715c/`(N=100;硬陷阱 SIR 100%、FPR 0%)

## E3|急停響應(SRT)與任務完成率(TCR)(論文 5.5)

- TCR 素材:B4 掛機的 patrol 日報就是任務完成記錄(`~/.config/jenai/reports/`)
- SRT(< 1s 目標)量測腳本待設計 —— 規劃:導航中送 `/stop`,量 halt 指令到
  `/cmd_vel` 歸零的時間戳差(audit + rosbag 皆有時間戳)

## E4|本地 vs 雲端 LLM 延遲(論文 5.6 素材;選配)

```bash
uv run python scripts/e4_bench.py --n 100 --out e4-local-$(date +%Y%m%d).jsonl
uv run python scripts/e4_bench.py --n 100 --provider nvidia-cloud \
    --model meta/llama-3.3-70b-instruct --out e4-cloud-$(date +%Y%m%d).jsonl
```

- 固定同一份快照重複 n 次,量延遲分佈(中位/P95),不量正確率(那是 E1)

## B4|模擬里程掛機(V1_GATE B4)

```bash
tmux new-session -d -s jenai-b4 -x 200 -y 50
tmux send-keys -t jenai-b4 'source /opt/ros/jazzy/setup.bash && uv run JenAI' Enter
sleep 15 && tmux send-keys -t jenai-b4 BTab        # Shift+Tab 切 auto 模式
nohup bash scripts/b4_driver.sh >/dev/null 2>&1 &  # 開圈(log: /tmp/b4_mileage.log)
```

統計(隨時可查):

```bash
ls ~/.config/jenai/reports/patrol-*.json | wc -l   # 任務數
awk '/lap=/{for(i=1;i<=NF;i++) if($i ~ /^[0-9]+s$/){sub(/s$/,"",$i); t+=$i}} \
    END{printf "%.1f h\n", t/3600}' /tmp/b4_mileage.log    # 累計任務時數
grep -vE '4/4' /tmp/b4_mileage.log                 # 異常圈(非 4/4 / timeout / 重啟)
```

- 回填:V1_GATE B4;非預期實體行為照 SAFETY_CASE「事件記錄」程序開 issue

## A6|24h soak

```bash
python3 scripts/soak.py --rules rules.example.toml   # 24h;Ctrl-C 提前結束也會出報告
```

- 輸出:`soak-<ts>/report.md`(要 **PASS**)+ `rss.csv` + `daemon.log`
- ✅ 已完成:`soak-20260715-012527/` PASS(RSS +1.2%,限 20%)

## 數據回填對照表

| 實驗 | 原始數據 | 回填位置 |
|---|---|---|
| E1 | `e1-<date>.json` | THESIS_DRAFT 5.3 |
| E2 | `e2-<date>/trials.jsonl` | THESIS_DRAFT 5.4、V1_GATE B5、SAFETY_CASE |
| E3 | patrol reports + audit | THESIS_DRAFT 5.5 |
| E4 | `e4-*.jsonl` | THESIS_DRAFT 5.6 素材 |
| B4 | `/tmp/b4_mileage.log` + reports | V1_GATE B4、SAFETY_CASE 事件表 |
| A6 | `soak-*/report.md` | V1_GATE A6 |

> 實驗輸出目錄(`e2-*/`、`e4-*.jsonl`、`soak-*/`)不入版控(.gitignore);
> 結論數字入文件,原始檔留本機。
