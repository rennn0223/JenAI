#!/usr/bin/env python3
"""24h soak harness for the JenAI daemon (V1_GATE A6). Stdlib only.

Launches the daemon (or any --command), samples the RSS of the whole process
tree on an interval, and writes a CSV + verdict report. The pass criterion is
memory stability: WARN when steady-state RSS grew more than --growth-limit
(default 20%) over the baseline — the signature of a leaking loop.

Typical runs (from a ROS-sourced shell):
    python3 scripts/soak.py --rules rules.example.toml              # 24h
    python3 scripts/soak.py --rules my.toml --minutes 60            # 1h spot
Ctrl-C ends the run early but still writes the report from collected data.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import os
import shlex
import signal
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


def _tree_pids(root: int) -> list[int]:
    pids, stack = [], [root]
    while stack:
        pid = stack.pop()
        pids.append(pid)
        for task in Path(f"/proc/{pid}/task").glob("*/children"):
            with contextlib.suppress(OSError, ValueError):
                stack.extend(int(c) for c in task.read_text().split())
    return pids


def _rss_kb(pid: int) -> int:
    total = 0
    for p in _tree_pids(pid):
        with contextlib.suppress(OSError):
            for line in Path(f"/proc/{p}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
                    break
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", help="rules.toml for `JenAI daemon --rules …`")
    parser.add_argument(
        "--command",
        help="full command to soak instead of the daemon (overrides --rules)",
    )
    parser.add_argument("--minutes", type=float, default=1440.0)
    parser.add_argument("--interval", type=float, default=30.0, help="sample seconds")
    parser.add_argument("--growth-limit", type=float, default=20.0, help="WARN above this %%")
    parser.add_argument(
        "--warmup",
        type=float,
        default=120.0,
        help="seconds excluded from the baseline (startup ramp: imports, uv sync, bridge spawn)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="output dir (default artifacts/experiments/soak/soak-<stamp>/)",
    )
    args = parser.parse_args()

    if args.command:
        command = args.command
    elif args.rules:
        command = f"uv run JenAI daemon --rules {shlex.quote(args.rules)}"
    else:
        parser.error("need --rules or --command")

    out = Path(
        args.out
        or Path("artifacts/experiments/soak")
        / f"soak-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    )
    out.mkdir(parents=True, exist_ok=True)
    log_file = (out / "daemon.log").open("w", encoding="utf-8")
    # New session: one killpg tears down the daemon AND its bridge sidecar.
    proc = subprocess.Popen(
        shlex.split(command), stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True
    )

    samples: list[tuple[float, int]] = []
    started = time.monotonic()
    deadline = started + args.minutes * 60
    print(f"soak: pid {proc.pid} · {args.minutes:g} min · every {args.interval:g}s → {out}")
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                print(f"soak: process exited early (code {proc.returncode})", file=sys.stderr)
                break
            samples.append((round(time.monotonic() - started, 1), _rss_kb(proc.pid)))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("soak: interrupted — reporting what we have")
    finally:
        # killpg races the tree's own exit — already-gone is success here.
        if proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(5)
            if proc.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
        log_file.close()

    with (out / "rss.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["elapsed_s", "rss_kb"])
        writer.writerows(samples)

    if len(samples) < 2:
        (out / "report.md").write_text("# Soak report\n\nToo few samples — run longer.\n")
        print("soak: too few samples for a verdict")
        return 1

    # Median of the head/tail windows — robust to startup spikes and GC noise.
    # Baseline starts after --warmup so the import/uv/bridge ramp doesn't
    # masquerade as a leak (falls back to all samples on very short runs).
    steady = [s for s in samples if s[0] >= args.warmup] or samples
    window = max(1, len(steady) // 10)
    baseline = statistics.median(rss for _, rss in steady[:window])
    final = statistics.median(rss for _, rss in steady[-window:])
    growth = (final - baseline) / baseline * 100 if baseline else 0.0
    verdict = "PASS" if growth <= args.growth_limit else "WARN"
    peak = max(rss for _, rss in samples)
    report = (
        "# Soak report\n\n"
        f"- command: `{command}`\n"
        f"- duration: {samples[-1][0] / 60:.1f} min · {len(samples)} samples\n"
        f"- RSS baseline (median first {window}): {baseline} kB\n"
        f"- RSS final (median last {window}): {final} kB\n"
        f"- RSS peak: {peak} kB\n"
        f"- growth: {growth:+.1f}% (limit {args.growth_limit:g}%)\n\n"
        f"**Verdict: {verdict}**\n\n"
        "Daemon output: see daemon.log (rule firings, honest status lines).\n"
    )
    (out / "report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0 if verdict == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
