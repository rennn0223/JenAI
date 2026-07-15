"""E2 消融實驗:Twin Gate 逐目標預演,量攔截率/誤攔率/延遲成本。

用法(ROS-sourced shell;會實際驅動孿生車,跑前先停掉 B4 掛機):
    python3 scripts/e2_ablation.py --per-class 20 --out e2-<date>/
    python3 scripts/e2_ablation.py --per-class 1 --smoke   # 每類 1 發冒煙

五類目標自佔位圖採樣(論文 4.3 之操作化程序):
  normal        自由空間、預期 pass
  zone_inside   目標在禁區內、預期 G3 直接 block(免模擬)
  zone_crossing 目標在禁區彼側、直線穿越禁區——實際軌跡是否進區由預演裁定
  unreachable   佔用格內(貨架體內)、預期 G5
  over_far      地圖界外緣、預期 G2/G5

每趟一行 JSONL:類別、目標、裁決、各判準狀態、耗時。B 組數據 = 裁決本身;
C 組 = 分析時將 refer 映為 block;A 組(HITL 基線)另以批准卡工具人工執行。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from datetime import datetime
from pathlib import Path

# 這支跑在 ROS-sourced 系統環境(rehearse 只用 jenai + rclpy 免 GUI),
# 但 jenai 本體裝在 venv —— 由呼叫端以 `uv run python scripts/e2_ablation.py`
# 執行(保留 PYTHONPATH,twin bridge 自行以系統直譯器啟動)。

ZONE = {"x_min": -9.0, "y_min": -13.0, "x_max": -4.5, "y_max": -9.0}  # SW-narrow-aisle


def load_grid():
    import rclpy
    from nav_msgs.msg import OccupancyGrid
    from rclpy.node import Node
    from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy

    rclpy.init()
    node = Node("e2_sampler")
    got = {}
    latched = QoSProfile(
        depth=1,
        reliability=QoSReliabilityPolicy.RELIABLE,
        durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
    )
    node.create_subscription(OccupancyGrid, "/map", lambda m: got.setdefault("m", m), latched)
    deadline = time.time() + 15
    while time.time() < deadline and "m" not in got:
        rclpy.spin_once(node, timeout_sec=0.5)
    node.destroy_node()
    rclpy.shutdown()
    if "m" not in got:
        raise SystemExit("拿不到 /map(孿生側沒在跑?)")
    return got["m"]


class Grid:
    def __init__(self, msg):
        i = msg.info
        self.res, self.ox, self.oy = i.resolution, i.origin.position.x, i.origin.position.y
        self.w, self.h = i.width, i.height
        self.data = msg.data

    def occ(self, x, y):
        c, r = int((x - self.ox) / self.res), int((y - self.oy) / self.res)
        if not (0 <= c < self.w and 0 <= r < self.h):
            return None  # 界外
        return self.data[r * self.w + c]

    def clear(self, x, y, radius):
        steps = int(radius / self.res) + 1
        for dc in range(-steps, steps + 1):
            for dr in range(-steps, steps + 1):
                if math.hypot(dc, dr) * self.res > radius:
                    continue
                v = self.occ(x + dc * self.res, y + dr * self.res)
                if v is None or v != 0:
                    return False
        return True

    def bounds(self):
        return self.ox, self.oy, self.ox + self.w * self.res, self.oy + self.h * self.res


def in_zone(x, y):
    return ZONE["x_min"] <= x <= ZONE["x_max"] and ZONE["y_min"] <= y <= ZONE["y_max"]


def sample_targets(grid: Grid, per_class: int, rng: random.Random):
    x0, y0, x1, y1 = grid.bounds()
    targets = []

    def draw(cls, pred, tries=20000):
        found = 0
        while found < per_class:
            tries -= 1
            if tries <= 0:
                raise SystemExit(f"{cls}: 採樣不足({found}/{per_class})")
            x = rng.uniform(x0 + 0.5, x1 - 0.5)
            y = rng.uniform(y0 + 0.5, y1 - 0.5)
            if pred(x, y):
                targets.append({"class": cls, "x": round(x, 2), "y": round(y, 2),
                                "yaw": round(rng.uniform(-math.pi, math.pi), 2)})
                found += 1

    draw("normal", lambda x, y: not in_zone(x, y) and grid.clear(x, y, 0.6))
    draw("zone_inside", lambda x, y: in_zone(x, y))
    # 禁區彼側:自由空間、在禁區西/南向外 1–3m,直線自倉庫中心穿越禁區帶
    def crossing(x, y):
        if in_zone(x, y) or not grid.clear(x, y, 0.5):
            return False
        cx, cy = (ZONE["x_min"] + ZONE["x_max"]) / 2, (ZONE["y_min"] + ZONE["y_max"]) / 2
        return math.hypot(x - cx, y - cy) < 4.0
    draw("zone_crossing", crossing)
    draw("unreachable", lambda x, y: not in_zone(x, y) and (grid.occ(x, y) or 0) >= 65)
    # 界外緣:x 或 y 超出地圖 0.5–2m
    def over_far(x, y):
        return False  # 不用面積採樣,直接構造
    for _ in range(per_class):
        side = rng.choice(["E", "W", "N", "S"])
        if side == "E":
            t = (x1 + rng.uniform(0.5, 2.0), rng.uniform(y0, y1))
        elif side == "W":
            t = (x0 - rng.uniform(0.5, 2.0), rng.uniform(y0, y1))
        elif side == "N":
            t = (rng.uniform(x0, x1), y1 + rng.uniform(0.5, 2.0))
        else:
            t = (rng.uniform(x0, x1), y0 - rng.uniform(0.5, 2.0))
        targets.append({"class": "over_far", "x": round(t[0], 2), "y": round(t[1], 2),
                        "yaw": 0.0})
    rng.shuffle(targets)
    return targets


HOME = {"x": 0.5, "y": -1.5, "yaw": 0.0}  # 固定起點:倉庫中央開闊區,遠離禁區


async def go_home(client) -> bool:
    """趟間重置:直接導航回固定起點(不經閘門、不計入量測)。

    protocol 教訓(第一輪 100 趟作廢的原因):不重置起點時,一趟把車留在
    禁區旁,之後每趟的起始軌跡樣本都落在禁區,G3 對所有類別誤攔 —— 起始
    狀態污染。每趟自同一起點出發也讓 crossing 類的幾何構造有明確意義。
    """
    import asyncio
    from uuid import uuid4

    tag = uuid4().hex[:8]
    done: asyncio.Future = asyncio.get_running_loop().create_future()

    def _on_result(event: dict) -> None:
        if event.get("tag", "") in ("", tag) and not done.done():
            done.set_result(str(event.get("status", "failed")))

    client.on_event("nav_result", _on_result)
    try:
        await client.nav_send(HOME["x"], HOME["y"], HOME["yaw"], tag=tag)
        status = await asyncio.wait_for(done, timeout=180.0)
        return status == "succeeded"
    except Exception:
        return False
    finally:
        client.off_event("nav_result", _on_result)


async def run_trials(targets, out_path: Path):
    from jenai.bridge import RosBridgeClient
    from jenai.config.store import load_config
    from jenai.twin import rehearse_goal

    config = load_config()
    twin = config.twin.model_copy(update={"enabled": True, "domain_id": 0})
    client = RosBridgeClient(domain_id=0)
    await client.start()
    done = 0
    with out_path.open("a", encoding="utf-8") as fh:
        for t in targets:
            homed = await go_home(client)
            action = {"goal": {"name": f"e2_{t['class']}_{done}", "frame_id": "map",
                               "pose": {"x": t["x"], "y": t["y"], "yaw": t["yaw"]}}}
            start = time.monotonic()
            try:
                report = await rehearse_goal(twin, action)
                row = {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "class": t["class"], "x": t["x"], "y": t["y"],
                    "verdict": report.verdict,
                    "reason": report.reason,
                    "elapsed_s": round(report.twin_elapsed_s or (time.monotonic() - start), 2),
                    "criteria": {c.criterion_id: c.status for c in report.criteria},
                    "homed": homed,
                }
            except Exception as exc:  # 單趟失敗誠實記錄,實驗續行
                row = {"ts": datetime.now().isoformat(timespec="seconds"),
                       "class": t["class"], "x": t["x"], "y": t["y"],
                       "verdict": "error", "reason": str(exc)[:200],
                       "elapsed_s": round(time.monotonic() - start, 2), "criteria": {}}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            done += 1
            print(f"[{done}/{len(targets)}] {t['class']:>13s} → {row['verdict']:5s} "
                  f"{row['elapsed_s']:6.1f}s  {row.get('reason') or ''}")
    return done


def summarize(out_path: Path):
    rows = [json.loads(x) for x in out_path.read_text().splitlines() if x.strip()]
    print("\n=== 摘要(B 組=裁決原樣;C 組=refer 視同 block)===")
    classes = sorted({r["class"] for r in rows})
    for cls in classes:
        sub = [r for r in rows if r["class"] == cls]
        n = len(sub)
        blocked = sum(1 for r in sub if r["verdict"] == "block")
        referred = sum(1 for r in sub if r["verdict"] == "refer")
        passed = sum(1 for r in sub if r["verdict"] == "pass")
        med = sorted(r["elapsed_s"] for r in sub)[n // 2] if n else 0
        print(f"{cls:>13s}: n={n:3d} pass={passed:3d} refer={referred:3d} "
              f"block={blocked:3d} 中位耗時={med:6.1f}s "
              f"B攔截={(blocked+referred)/n*100:5.1f}% C攔截={(blocked+referred)/n*100:5.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20260715)
    ap.add_argument("--out", default=None)
    ap.add_argument("--smoke", action="store_true")
    # 雙環境分工:--sample-only 用系統 Python(rclpy 讀圖);跑實驗用
    # `uv run`(jenai 在 venv,rehearse 自帶 sidecar,不 import rclpy)。
    ap.add_argument("--sample-only", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.out or f"e2-{datetime.now():%Y%m%d-%H%M%S}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "trials.jsonl"
    targets_path = out_dir / "targets.json"

    if args.sample_only:
        grid = Grid(load_grid())
        rng = random.Random(args.seed)
        targets = sample_targets(grid, args.per_class, rng)
        targets_path.write_text(json.dumps(targets, ensure_ascii=False, indent=1))
        print(f"目標集 {len(targets)} 趟(seed={args.seed})→ {targets_path}")
        return

    targets = json.loads(targets_path.read_text())
    print(f"載入 {len(targets)} 趟 → {out_path}")
    import asyncio
    asyncio.run(run_trials(targets, out_path))
    summarize(out_path)


if __name__ == "__main__":
    main()
