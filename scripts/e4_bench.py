"""E4 延遲量測:情境快照 → 決策(decide)之端到端延遲分佈。

用法(venv;量測期間場景照常運行 = 營運條件):
    uv run python scripts/e4_bench.py --n 100 --out e4-local.jsonl
    uv run python scripts/e4_bench.py --n 100 --provider nvidia-cloud \
        --model meta/llama-3.3-70b-instruct --out e4-cloud.jsonl

固定一份代表性快照(倉庫巡邏情境)重複 n 次:E4 量的是延遲分佈
(中位數/P95),不是正確率(那是 E1);固定輸入使兩配置可直接對照。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics as st
import time
from datetime import datetime
from pathlib import Path

SNAPSHOT_FIELDS = {
    "pose": "x=0.50 y=-1.50 yaw=0.00 (map)",
    "battery": 0.62,
    "scene": "warehouse aisle clear; one forklift parked left; no people",
    "task": "patrol 2/4 points done",
    "request": "patrol the four waypoints, report anything unusual",
    "locations": ["map_left_up", "map_wall", "map_right_down", "dock"],
}


async def run(config, n: int, out_path: Path) -> list[float]:
    from jenai.tools.decision_core import ContextSnapshot, decide

    snapshot = ContextSnapshot(**SNAPSHOT_FIELDS)
    latencies: list[float] = []
    with out_path.open("a", encoding="utf-8") as fh:
        for i in range(n):
            t0 = time.perf_counter()
            try:
                decision = await decide(config, snapshot)
                dt = time.perf_counter() - t0
                row = {"i": i, "latency_s": round(dt, 3), "action": decision.action,
                       "ts": datetime.now().isoformat(timespec="seconds")}
            except Exception as exc:  # 供應端錯誤誠實記錄,不中斷量測
                dt = time.perf_counter() - t0
                row = {"i": i, "latency_s": round(dt, 3), "action": "error",
                       "error": str(exc)[:120],
                       "ts": datetime.now().isoformat(timespec="seconds")}
            latencies.append(dt)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"[{i+1}/{n}] {dt:6.2f}s {row['action']}")
    return latencies


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--provider", default=None, help="覆寫 active provider(如 nvidia-cloud)")
    ap.add_argument("--model", default=None, help="覆寫 plan 綁定之模型名")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    from jenai.config.store import load_config

    config = load_config()
    if args.provider:
        config = config.model_copy(update={"active_provider": args.provider})
    if args.model and config.model_bindings is not None:
        config = config.model_copy(
            update={"model_bindings": config.model_bindings.model_copy(update={"plan": args.model})}
        )

    out_path = Path(args.out)
    latencies = asyncio.run(run(config, args.n, out_path))
    ls = sorted(latencies)
    n = len(ls)
    print(
        f"\nn={n} 中位={st.median(ls):.2f}s P95={ls[int(n*0.95)-1]:.2f}s "
        f"平均={st.mean(ls):.2f}s min={ls[0]:.2f}s max={ls[-1]:.2f}s"
    )


if __name__ == "__main__":
    main()
