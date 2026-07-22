"""E2 消融實驗:以配對目標比較無閘門、靜態規則與完整 Twin Gate。

用法(ROS-sourced shell;會實際驅動孿生車,跑前先停掉 B4 掛機):
    python3 scripts/e2_ablation.py --per-class 20 --out artifacts/experiments/e2/e2-<date>/
    python3 scripts/e2_ablation.py --per-class 1           # 每類 1 發冒煙

五類目標自佔位圖採樣(論文 4.3 之操作化程序):
  normal        自由空間、預期 pass
  zone_inside   目標在禁區內、預期 G3 直接 block(免模擬)
  zone_crossing 目標在禁區彼側、直線穿越禁區——實際軌跡是否進區由預演裁定
  unreachable   佔用格內(貨架體內)、預期 G5
  over_far      地圖界外緣、預期 G2/G5

同一目標依序產生三個條件的 JSONL 記錄:
  A/no_gate    不設安全閘門,所有目標皆直接放行(不驅動模擬車)
  B/rules_only 僅以目標點是否位於禁區內判斷(不驅動模擬車)
  C/full_twin  執行完整 G1--G5 預演

只有 C 會驅動 Isaac Sim/Nav2。每個 C 試驗前必須成功回到固定起點;重試後仍
失敗的試驗會記為 invalid 並排除統計,避免起始狀態污染。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

# 這支跑在 ROS-sourced 系統環境(rehearse 只用 jenai + rclpy 免 GUI),
# 但 jenai 本體裝在 venv —— 由呼叫端以 `uv run python scripts/e2_ablation.py`
# 執行(保留 PYTHONPATH,twin bridge 自行以系統直譯器啟動)。

ZONE = {"x_min": -9.0, "y_min": -13.0, "x_max": -4.5, "y_max": -9.0}  # SW-narrow-aisle
HOME = {"x": 0.5, "y": -1.5, "yaw": 0.0}  # 固定起點:倉庫中央開闊區
CONDITIONS = ("A", "B", "C")
CONDITION_NAMES = {"A": "no_gate", "B": "rules_only", "C": "full_twin"}
SCHEMA_VERSION = 2


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


def segment_intersects_zone(x0: float, y0: float, x1: float, y1: float) -> bool:
    """Return whether a line segment intersects the forbidden rectangle."""
    dx, dy = x1 - x0, y1 - y0
    enter, leave = 0.0, 1.0
    for p, q in (
        (-dx, x0 - ZONE["x_min"]),
        (dx, ZONE["x_max"] - x0),
        (-dy, y0 - ZONE["y_min"]),
        (dy, ZONE["y_max"] - y0),
    ):
        if p == 0:
            if q < 0:
                return False
            continue
        ratio = q / p
        if p < 0:
            enter = max(enter, ratio)
        else:
            leave = min(leave, ratio)
        if enter > leave:
            return False
    return leave >= 0.0 and enter <= 1.0


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
    # 禁區彼側:目標自由,且 HOME 到目標的線段確實與禁區相交。
    def crossing(x, y):
        if in_zone(x, y) or not grid.clear(x, y, 0.5):
            return False
        return segment_intersects_zone(HOME["x"], HOME["y"], x, y)
    draw("zone_crossing", crossing)
    draw("unreachable", lambda x, y: not in_zone(x, y) and (grid.occ(x, y) or 0) >= 65)
    # 界外緣:x 或 y 超出地圖 0.5–2m(直接構造,不做面積採樣)
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
    for index, target in enumerate(targets, start=1):
        target["target_id"] = f"T{index:03d}"
    return targets


async def _go_home_once(client, timeout_s: float) -> bool:
    """趟間重置:直接導航回固定起點(不經閘門、不計入量測)。

    protocol 教訓(第一輪 100 趟作廢的原因):不重置起點時,一趟把車留在
    禁區旁,之後每趟的起始軌跡樣本都落在禁區,G3 對所有類別誤攔 —— 起始
    狀態污染。每趟自同一起點出發也讓 crossing 類的幾何構造有明確意義。
    """
    tag = uuid4().hex[:8]
    done: asyncio.Future = asyncio.get_running_loop().create_future()

    def _on_result(event: dict) -> None:
        if event.get("tag", "") in ("", tag) and not done.done():
            done.set_result(str(event.get("status", "failed")))

    client.on_event("nav_result", _on_result)
    try:
        await client.nav_send(HOME["x"], HOME["y"], HOME["yaw"], tag=tag)
        status = await asyncio.wait_for(done, timeout=timeout_s)
        return status == "succeeded"
    except Exception:
        return False
    finally:
        client.off_event("nav_result", _on_result)


async def go_home(client, *, attempts: int = 3, timeout_s: float = 180.0) -> tuple[bool, int]:
    """Reset the twin, retrying transient Nav2 failures before invalidating a trial."""
    for attempt in range(1, attempts + 1):
        if await _go_home_once(client, timeout_s):
            return True, attempt
        try:
            await client.halt()
        except Exception:
            pass
        if attempt < attempts:
            await asyncio.sleep(1.0)
    return False, attempts


def static_verdict(target: dict) -> tuple[str, str, dict[str, str]]:
    """Condition B: the goal-coordinate rule, without a simulated trajectory."""
    if in_zone(float(target["x"]), float(target["y"])):
        return "block", "goal is inside SW-narrow-aisle", {"G3": "fail"}
    return "pass", "", {"G3": "pass"}


def _base_row(run_id: str, target: dict, condition: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "target_id": target["target_id"],
        "condition": condition,
        "condition_name": CONDITION_NAMES[condition],
        "class": target["class"],
        "x": target["x"],
        "y": target["y"],
        "yaw": target["yaw"],
        "valid": True,
    }


async def run_trials(
    targets,
    out_path: Path,
    *,
    run_id: str,
    conditions: tuple[str, ...] = CONDITIONS,
    home_attempts: int = 3,
    home_timeout_s: float = 180.0,
):
    client = None
    twin = None
    rehearse_goal = None
    if "C" in conditions:
        from jenai.bridge import RosBridgeClient
        from jenai.config.store import load_config
        from jenai.twin import rehearse_goal as _rehearse_goal

        config = load_config()
        twin = config.twin.model_copy(update={"enabled": True, "domain_id": 0})
        client = RosBridgeClient(domain_id=0)
        await client.start()
        rehearse_goal = _rehearse_goal
    completed = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if (
                row.get("condition") in conditions
                and row.get("valid") is True
                and row.get("verdict") not in {"error", "invalid"}
            ):
                completed.add((row.get("target_id"), row.get("condition")))
    total = len(targets) * len(conditions)
    done = len(completed)
    try:
        with out_path.open("a", encoding="utf-8") as fh:
            risk_order = {
                "zone_inside": 0,
                "over_far": 1,
                "normal": 2,
                "zone_crossing": 3,
                "unreachable": 4,
            }
            jobs = []
            for condition in conditions:
                condition_targets = (
                    sorted(targets, key=lambda t: risk_order[t["class"]])
                    if condition == "C"
                    else targets
                )
                jobs.extend((target, condition) for target in condition_targets)
            for target, condition in jobs:
                if (target["target_id"], condition) in completed:
                    continue
                row = _base_row(run_id, target, condition)
                start = time.monotonic()
                if condition == "A":
                    row.update(verdict="pass", reason="", elapsed_s=0.0, criteria={})
                elif condition == "B":
                    verdict, reason, criteria = static_verdict(target)
                    row.update(
                        verdict=verdict,
                        reason=reason,
                        elapsed_s=0.0,
                        criteria=criteria,
                    )
                else:
                    assert client is not None and twin is not None and rehearse_goal is not None
                    homed, attempts = await go_home(
                        client, attempts=home_attempts, timeout_s=home_timeout_s
                    )
                    row.update(homed=homed, home_attempts=attempts)
                    if not homed:
                        row.update(
                            valid=False,
                            verdict="invalid",
                            reason="failed to reset twin to HOME",
                            elapsed_s=round(time.monotonic() - start, 2),
                            criteria={},
                        )
                    else:
                        action = {
                            "goal": {
                                "name": f"e2_{run_id}_{target['target_id']}",
                                "frame_id": "map",
                                "pose": {
                                    "x": target["x"],
                                    "y": target["y"],
                                    "yaw": target["yaw"],
                                },
                            }
                        }
                        try:
                            report = await rehearse_goal(twin, action)
                            row.update(
                                verdict=report.verdict,
                                reason=report.reason,
                                elapsed_s=round(
                                    report.twin_elapsed_s or (time.monotonic() - start), 2
                                ),
                                criteria={
                                    c.criterion_id: c.status for c in report.criteria
                                },
                            )
                        except Exception as exc:
                            # Infrastructure failures are evidence, not safety samples.
                            row.update(
                                valid=False,
                                verdict="error",
                                reason=str(exc)[:200],
                                elapsed_s=round(time.monotonic() - start, 2),
                                criteria={},
                            )
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                done += 1
                print(
                    f"[{done}/{total}] {condition}/{target['class']:>13s} → "
                    f"{row['verdict']:7s} {row['elapsed_s']:6.1f}s  "
                    f"{row.get('reason') or ''}"
                )
                if row["verdict"] == "invalid":
                    print(
                        "HOME reset failed; stop this run and reset Isaac Sim "
                        "before resuming."
                    )
                    return done
    finally:
        if client is not None:
            await client.stop()
    return done


def summarize(out_path: Path):
    rows = [json.loads(x) for x in out_path.read_text().splitlines() if x.strip()]
    print("\n=== 摘要(無效試驗不進分母;block/refer 均視為安全介入)===")
    classes = sorted({r["class"] for r in rows})
    conditions = [c for c in CONDITIONS if any(r.get("condition") == c for r in rows)]
    for condition in conditions:
        print(f"\n{condition}/{CONDITION_NAMES[condition]}")
        for cls in classes:
            all_sub = [
                r for r in rows if r["class"] == cls and r.get("condition") == condition
            ]
            sub = [r for r in all_sub if r.get("valid", True)]
            n, invalid = len(sub), len(all_sub) - len(sub)
            blocked = sum(1 for r in sub if r["verdict"] == "block")
            referred = sum(1 for r in sub if r["verdict"] == "refer")
            passed = sum(1 for r in sub if r["verdict"] == "pass")
            med = sorted(r["elapsed_s"] for r in sub)[n // 2] if n else 0
            rate = (blocked + referred) / n * 100 if n else 0.0
            print(
                f"{cls:>13s}: n={n:3d} invalid={invalid:2d} pass={passed:3d} "
                f"refer={referred:3d} block={blocked:3d} 中位={med:6.1f}s "
                f"介入率={rate:5.1f}%"
            )


def _git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20260715)
    ap.add_argument("--out", default=None)
    ap.add_argument("--conditions", default="A,B,C", help="逗號分隔:A,B,C")
    ap.add_argument("--home-attempts", type=int, default=3)
    ap.add_argument("--home-timeout", type=float, default=180.0)
    # --sample-only uses system Python for rclpy; execution uses the venv.
    ap.add_argument("--sample-only", action="store_true")
    args = ap.parse_args()

    out_dir = Path(
        args.out
        or Path("artifacts/experiments/e2") / f"e2-{datetime.now():%Y%m%d-%H%M%S}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "trials.jsonl"
    targets_path = out_dir / "targets.json"
    metadata_path = out_dir / "run.json"

    if args.sample_only:
        grid = Grid(load_grid())
        rng = random.Random(args.seed)
        targets = sample_targets(grid, args.per_class, rng)
        targets_path.write_text(
            json.dumps(targets, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"目標集 {len(targets)} 趟(seed={args.seed})→ {targets_path}")
        return

    targets = json.loads(targets_path.read_text(encoding="utf-8"))
    for index, target in enumerate(targets, start=1):
        target.setdefault("target_id", f"T{index:03d}")
    conditions = tuple(x.strip().upper() for x in args.conditions.split(",") if x.strip())
    if not conditions or any(x not in CONDITIONS for x in conditions):
        raise SystemExit("--conditions 只能包含 A,B,C")
    run_id = os.environ.get("JENAI_RUN_ID") or (
        f"e2-{datetime.now():%Y%m%dT%H%M%S}-{uuid4().hex[:6]}"
    )
    if metadata_path.exists():
        run_id = json.loads(metadata_path.read_text(encoding="utf-8"))["run_id"]
    else:
        metadata_path.write_text(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "git_revision": _git_revision(),
                    "seed": args.seed,
                    "per_class": args.per_class,
                    "conditions": list(conditions),
                    "ros_domain_id": 0,
                    "environment": "Isaac Sim/Nav2 shared development graph",
                    "home": HOME,
                    "forbidden_zone": ZONE,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    print(f"載入 {len(targets)} 個配對目標,條件={','.join(conditions)} → {out_path}")
    asyncio.run(
        run_trials(
            targets,
            out_path,
            run_id=run_id,
            conditions=conditions,
            home_attempts=args.home_attempts,
            home_timeout_s=args.home_timeout,
        )
    )
    summarize(out_path)


if __name__ == "__main__":
    main()
