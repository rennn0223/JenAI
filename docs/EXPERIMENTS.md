# EXPERIMENTS — 實驗 runbook(照抄就能跑)

> 對象:客戶自跑、或任何接手的 session。每個實驗:指令 → 輸出在哪 → 數據回填到哪。
> 環境鐵則(CLAUDE.md):跑 app/實驗的 shell 先 `source /opt/ros/jazzy/setup.bash`
> 且**保留** PYTHONPATH;只有單元測試才 `env -u PYTHONPATH uv run pytest`。

## 前置:場景要活著(E2/B4 需要;E1/E4 不碰 ROS,E3 使用隔離 fixture)

1. 先以 `ps -eo pid,etime,cmd | rg 'b4_driver|patrol|e3_agent_bench|ros2 topic pub'` 清查背景任務；要操作 domain 0 前必須停止舊 B4 driver，不能只相信上一個終端畫面的完成訊息。
2. Isaac Sim 開 Carter 場景(照 [TWIN_SETUP](TWIN_SETUP.md) / [ISAAC_NAV2_SETUP](ISAAC_NAV2_SETUP.md)),Nav2 起好
3. 健檢:`uv run JenAI doctor` —— twin 三檢查要全綠

## E1|決策腦 eval(論文 5.3)

```bash
uv run JenAI eval scenarios.e1.toml                          # 正式庫(每族 ≥15 條)
uv run JenAI eval scenarios.e1.toml -k 3                     # 輸出穩定度採樣
uv run JenAI eval scenarios.e1.toml --json > e1-$(date +%Y%m%d).json   # 留原始數據
```

- 種子 8 條:`scenarios.example.toml`(schema 說明在檔頭);正式庫:`scenarios.e1.toml`
- 標註支援 `action:target` 細分(如 `expected = ["dock", "navigate_to:Dock"]`),
  避免「navigate_to(充電站) 被記成 unsafe」的標註歧義
- `-k > 1` 時以每個情境的多數決計算 accuracy/unsafe/refer；原始重複輸出仍保留，另報 agreement_rate 與 tie_rate
- 看五個數:accuracy、**unsafe_rate(安全論文最重要的數)**、refer_rate、agreement_rate、tie_rate
- 回填:THESIS_DRAFT 5.3 正式結果

## E2|Twin Gate 消融(論文 5.4;V1_GATE B5)

```bash
pkill -f b4_driver.sh
OUT=e2-$(date +%Y%m%d)-paired
python3 scripts/e2_ablation.py --sample-only --per-class 1 --out "$OUT"   # 冒煙採樣
uv run python scripts/e2_ablation.py --per-class 1 --conditions A,B,C --out "$OUT"

OUT=e2-$(date +%Y%m%d)-paired-formal
python3 scripts/e2_ablation.py --sample-only --per-class 20 --out "$OUT"  # 正式採樣
uv run python scripts/e2_ablation.py --per-class 20 --conditions A,B,C --out "$OUT"
```

- A=`no_gate`、B=`rules_only`、C=`full_twin`;三組共用同一批 target ID,只有 C 驅動 Nav2
- C 每趟先回 HOME,最多重試三次;失敗記為 `invalid` 且不進分母。重新執行同一目錄會續跑有效樣本
- 輸出:`trials.jsonl`(逐趟)、`targets.json`(配對目標)、`run.json`(run ID/seed/git revision/環境)
- `ROS_DOMAIN_ID=0` 是本機 Isaac Sim/Nav2 共圖開發模式;不得在同一 graph 接入實體車
- `e2-20260715c/` 是 v1 舊協議的單一 full-twin 結果,可作前導資料,不可稱為 A/B/C 消融
- 回填:THESIS_DRAFT 5.4、V1_GATE B5、SAFETY_CASE(H2/完稿條件)

## E3|自然語言 ROS2 發現—執行—驗證閉環

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42
python3 scripts/e3_mock_ugv.py
# 另一個 shell 使用同一 domain:
uv run python scripts/e3_agent_bench.py --out e3-agent-boundary-$(date +%Y%m%d).jsonl
```

- 八題分為介面發現、有限時閉環動作與失效邊界；每題使用全新 Agent session。
- 動作題必須使用一次 `ros_drive_verified_tool`：同一工具擷取基準 `/odom`、等待控制訂閱者，並由同一個 rclpy publisher 依指定 rate/duration 發布有限時命令及連續 zero-stop pulses，再擷取後驗 `/odom`。基準缺席不致動；後驗缺席回傳 `unverified`，不會重送。
- 第一個複合致動可批准，第二次一律拒絕；評分直接檢查工具的 `verified`／`unverified` 裁決，不以 Agent 的文字宣稱代替回授。
- fixture 在 domain 42 提供 `/cmd_vel`、`/odom`、重置與「第一次動作後中斷回授」故障注入。fixture 與 benchmark 在 `ROS_DOMAIN_ID` 不是 42 時均拒絕啟動，不碰 domain 0 的 Isaac Sim/Nav2。
- 輸出 JSONL 保存工具序列、工具摘要、延遲、批准輪次、最終回報與逐項通過條件；旁檔 `.meta.json` 保存模型與 run ID。
- 2026-07-17 的失效導向紀錄依序保留原始基準、turn regression、compound initial、compound regression 與 compound final；不得以修正後結果覆蓋先前失敗。
- 2026-07-18 的 v1.1.1 完整批次為 7/8、無重複致動 8/8；唯一失敗是模型在致動前
  呼叫不存在工具。獨立的三項 D2 定向重跑為 3/3，兩份 JSONL 必須分開保存與報告，
  不可以定向重跑覆蓋完整批次失敗。


## E4|本地 vs 雲端 LLM 延遲(論文 5.6 素材;選配)

```bash
uv run python scripts/e4_bench.py --n 100 --out e4-local-$(date +%Y%m%d).jsonl
uv run python scripts/e4_bench.py --n 100 --provider nvidia-cloud \
    --model meta/llama-3.3-70b-instruct --out e4-cloud-$(date +%Y%m%d).jsonl
```

- 固定同一份快照重複 n 次,量「決策層端到端」延遲分佈(中位/P95),不代表整體任務完成時間
- 僅成功呼叫進 latency 分母；錯誤另報 error count/rate，輸出旁有 `.meta.json` 保存 provider/model/run ID

## B4|模擬里程掛機(V1_GATE B4)

```bash
tmux new-session -d -s jenai-b4 -x 200 -y 50
tmux send-keys -t jenai-b4 'source /opt/ros/jazzy/setup.bash && uv run JenAI' Enter
sleep 15 && tmux send-keys -t jenai-b4 BTab        # Shift+Tab 切 auto 模式
B4_MAX_LAPS=102 B4_MAX_SECONDS=72000 \
  nohup bash scripts/b4_driver.sh >/dev/null 2>&1 &  # 有界執行(log: /tmp/b4_mileage.log)
```

統計(隨時可查):

```bash
ls ~/.config/jenai/reports/patrol-*.json | wc -l   # 任務數
awk '/ lap=/{for(i=1;i<=NF;i++) if($i ~ /^elapsed_s=/){sub(/^elapsed_s=/,"",$i); t+=$i}} \
    END{printf "%.1f h\n", t/3600}' /tmp/b4_mileage.log    # 累計任務時數
grep ' lap=' /tmp/b4_mileage.log | grep -v 'status=completed' # partial / timeout
```

- 每次 driver 啟動產生 `run_id`;每圈只接受啟動後新增的 `Patrol finished`,避免把舊畫面誤認為新完成。只有 N/N 才記 `completed`，其餘記 `partial`
- driver 預設最多 102 圈或 72000 秒,路線可用 `B4_PATROL_ROUTE` 覆寫；同一 log 以
  `flock` 保證單實例;正常結束或收到 HUP/INT/TERM 時會向同一 TUI 送 `/stop`
- `SIGKILL`、主機故障或外部程序仍可能略過清理;每次 Isaac Sim Play 前都要重新清查 `b4_driver`、活動 Nav2 goal 與非零 `/cmd_vel`,不能只依賴 EXIT trap
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
| E3 | `e3-agent-boundary-*.jsonl` | THESIS_DRAFT 5.3 |
| E4 | `e4-*.jsonl` | THESIS_DRAFT 5.6 素材 |
| B4 | `/tmp/b4_mileage.log` + reports | V1_GATE B4、SAFETY_CASE 事件表 |
| A6 | `soak-*/report.md` | V1_GATE A6 |

> 實驗輸出目錄(`e2-*/`、`e4-*.jsonl`、`soak-*/`)不入版控(.gitignore);
> 結論數字入文件,原始檔留本機。
