"""Twin-Gated Execution: rehearse a navigation goal in the digital twin.

The gate layer sits between the LLM's decision and the real robot: the goal is
first executed in the Isaac Sim twin scene (an isolated ROS_DOMAIN_ID running
the same Nav2 stack) and judged against five criteria:

    G1 collision           — the twin's contact sensor fired
    G2 timeout             — the rehearsal did not finish in time
    G3 forbidden zone      — the twin trajectory entered a configured zone
    G4 endpoint deviation  — the twin arrived, but too far from the goal
    G5 Nav2 failure        — the twin's Nav2 aborted or rejected the goal

Verdict policy: G1/G3 are hard safety violations → block. G2/G4/G5 mean the
rehearsal was infeasible or inconclusive → refer to a human (the twin scene
may simply be stale). An unreachable twin also refers — while the gate is
enabled it never silently passes.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from time import monotonic
from uuid import uuid4

from jenai.bridge import BridgeError, RosBridgeClient
from jenai.config.models import ForbiddenZone, TwinProfile
from jenai.schemas import GateCriterion, GateReport

_NAMES = {
    "G1": "collision",
    "G2": "timeout",
    "G3": "forbidden zone",
    "G4": "endpoint deviation",
    "G5": "Nav2 failure",
}
_HARD = ("G1", "G3")  # failures that block outright; the rest refer


def _criterion(cid: str, status: str, detail: str = "") -> GateCriterion:
    return GateCriterion(criterion_id=cid, name=_NAMES[cid], status=status, detail=detail)


def _report(
    verdict: str,
    reason: str,
    elapsed: float,
    statuses: dict[str, tuple[str, str]] | None = None,
) -> GateReport:
    """Build a report; criteria not mentioned in `statuses` are 'skipped'."""
    statuses = statuses or {}
    criteria = [
        _criterion(cid, *statuses.get(cid, ("skipped", ""))) for cid in _NAMES
    ]
    return GateReport(
        verdict=verdict, reason=reason, criteria=criteria, twin_elapsed_s=round(elapsed, 2)
    )


class TwinGate:
    """Rehearses one goal at a time in the twin and issues pass/block/refer."""

    def __init__(self, twin: TwinProfile, bridge: RosBridgeClient | None = None) -> None:
        self._twin = twin
        self._bridge = bridge if bridge is not None else RosBridgeClient(domain_id=twin.domain_id)

    async def stop(self) -> None:
        await self._bridge.stop()

    async def rehearse(
        self,
        outgoing_action: dict,
        *,
        on_status: Callable[[str], None] | None = None,
    ) -> GateReport:
        goal = outgoing_action.get("goal") or {}
        pose = goal.get("pose") or {}
        gx, gy = float(pose.get("x", 0.0)), float(pose.get("y", 0.0))

        def _say(text: str) -> None:
            if on_status is not None:
                on_status(text)

        # A goal inside a forbidden zone needs no simulation to be judged.
        zone = self._zone_at(gx, gy)
        if zone is not None:
            detail = f"goal ({gx:.2f}, {gy:.2f}) is inside '{zone.name}'"
            _say(f"twin gate: BLOCK — {detail}")
            return _report("block", detail, 0.0, {"G3": ("fail", detail)})

        started = monotonic()
        try:
            await self._bridge.start()
        except BridgeError as exc:
            reason = f"twin unreachable: {exc}"
            _say(f"twin gate: REFER — {reason}")
            return _report("refer", reason, monotonic() - started)

        _say(f"twin gate: rehearsing goal ({gx:.2f}, {gy:.2f}) in the twin…")
        try:
            outcome = await self._run_rehearsal(goal, gx, gy)
        except BridgeError as exc:
            reason = f"twin bridge failed mid-rehearsal: {exc}"
            _say(f"twin gate: REFER — {reason}")
            return _report("refer", reason, monotonic() - started)
        elapsed = monotonic() - started

        report = self._judge(outcome, elapsed)
        _say(f"twin gate: {report.verdict.upper()}"
             + (f" — {report.reason}" if report.reason else f" (twin took {elapsed:.0f}s)"))
        return report

    async def _run_rehearsal(self, goal: dict, gx: float, gy: float) -> dict:
        """Drive the twin's Nav2 and collect raw observations for judging."""
        loop = asyncio.get_running_loop()
        result_future: asyncio.Future[str] = loop.create_future()
        tag = uuid4().hex[:8]
        collision: list[str] = []
        zone_hit: list[str] = []
        pose_samples = [0]

        def _on_result(event: dict) -> None:
            if event.get("tag", "") in ("", tag) and not result_future.done():
                result_future.set_result(str(event.get("status", "failed")))

        def _on_contact(data: dict) -> None:
            if data.get("data") and not collision:
                collision.append(f"contact reported on {self._twin.collision_topic}")

        self._bridge.on_event("nav_result", _on_result)
        watch_id: int | None = None
        try:
            watch_id = await self._bridge.watch(
                self._twin.collision_topic, "std_msgs/msg/Bool", _on_contact, throttle=0.1
            )
        except BridgeError:
            pass  # no contact sensor in the scene: G1 will be reported as skipped

        sampler = asyncio.create_task(self._sample_trajectory(zone_hit, pose_samples))
        status, timed_out, we_canceled = "failed", False, False
        try:
            await self._bridge.nav_send(
                x=gx,
                y=gy,
                yaw=float((goal.get("pose") or {}).get("yaw", 0.0)),
                frame_id=goal.get("frame_id", "map"),
                tag=tag,
            )
            deadline = monotonic() + self._twin.nav_timeout_s
            while not result_future.done():
                if collision or zone_hit:
                    we_canceled = True
                    await self._cancel_quietly()
                    break
                if monotonic() > deadline:
                    timed_out, we_canceled = True, True
                    await self._cancel_quietly()
                    break
                await asyncio.sleep(0.2)
            if result_future.done():
                status = result_future.result()
        except asyncio.CancelledError:
            # The caller gave up (TUI Esc): stop the twin too, then unwind.
            await self._cancel_quietly()
            raise
        finally:
            sampler.cancel()
            self._bridge.off_event("nav_result", _on_result)
            if watch_id is not None:
                try:
                    await self._bridge.unwatch(watch_id)
                except BridgeError:
                    pass

        deviation: float | None = None
        if status == "succeeded":
            try:
                p = await self._bridge.get_pose(timeout=2.0)
                deviation = math.hypot(p.x - gx, p.y - gy)
            except BridgeError:
                pass  # twin pose unavailable: G4 stays skipped
        return {
            "status": status,
            "timed_out": timed_out,
            "we_canceled": we_canceled,
            "collision": collision[0] if collision else None,
            "zone_hit": zone_hit[0] if zone_hit else None,
            "pose_samples": pose_samples[0],
            "deviation": deviation,
            "watched_contact": watch_id is not None,
        }

    def _judge(self, o: dict, elapsed: float) -> GateReport:
        statuses: dict[str, tuple[str, str]] = {}
        if o["watched_contact"]:
            statuses["G1"] = ("fail", o["collision"]) if o["collision"] else ("pass", "")
        statuses["G2"] = (
            ("fail", f"twin exceeded {self._twin.nav_timeout_s:.0f}s")
            if o["timed_out"]
            else ("pass", "")
        )
        g3_inconclusive = bool(
            self._twin.forbidden_zones and not o["zone_hit"] and o["pose_samples"] == 0
        )
        if o["zone_hit"]:
            statuses["G3"] = ("fail", o["zone_hit"])
        elif g3_inconclusive:
            statuses["G3"] = ("skipped", "no twin pose samples; forbidden zones were not checked")
        else:
            statuses["G3"] = ("pass", "")
        if o["deviation"] is not None:
            ok = o["deviation"] <= self._twin.goal_tolerance_m
            statuses["G4"] = (
                ("pass", "")
                if ok
                else ("fail", f"stopped {o['deviation']:.2f} m from the goal "
                              f"(tolerance {self._twin.goal_tolerance_m:.2f} m)")
            )
        elif o["status"] == "succeeded":
            statuses["G4"] = ("fail", "twin final pose unavailable; endpoint not verified")
        if o["status"] == "succeeded":
            statuses["G5"] = ("pass", "")
        elif o["we_canceled"]:
            pass  # we interrupted Nav2 ourselves; its verdict is meaningless
        else:
            statuses["G5"] = ("fail", f"twin Nav2 ended with '{o['status']}'")

        failed = {cid for cid, (st, _) in statuses.items() if st == "fail"}
        hard_failed = failed & set(_HARD)
        if hard_failed:
            verdict = "block"
        elif g3_inconclusive:
            verdict = "refer"
        elif failed:
            verdict = "refer"
        else:
            verdict = "pass"
        reason = ""
        for cid in _NAMES:
            if cid in hard_failed:
                reason = statuses[cid][1]
                break
        if not reason and g3_inconclusive:
            reason = statuses["G3"][1]
        if not reason:
            for cid in _NAMES:
                if cid in failed:
                    reason = statuses[cid][1]
                    break
        return _report(verdict, reason, elapsed, statuses)

    async def _sample_trajectory(self, zone_hit: list[str], pose_samples: list[int]) -> None:
        """Watch the twin's pose and record the first forbidden-zone entry."""
        if not self._twin.forbidden_zones:
            return
        while not zone_hit:
            try:
                p = await self._bridge.get_pose(timeout=max(self._twin.pose_sample_s, 1.0))
            except BridgeError:
                pass  # transient (no pose yet / bridge respawning) — keep sampling
            else:
                # Only a finite pose counts as G3 evidence. NaN/inf never falls
                # inside any zone (contains() comparisons are False), so counting
                # it would mark the trajectory "checked" while it is unknown —
                # a broken twin localization must land in the inconclusive path.
                if math.isfinite(p.x) and math.isfinite(p.y):
                    pose_samples[0] += 1
                    zone = self._zone_at(p.x, p.y)
                    if zone is not None:
                        zone_hit.append(f"twin entered '{zone.name}' at ({p.x:.2f}, {p.y:.2f})")
                        return
            await asyncio.sleep(self._twin.pose_sample_s)

    def _zone_at(self, x: float, y: float) -> ForbiddenZone | None:
        return next((z for z in self._twin.forbidden_zones if z.contains(x, y)), None)

    async def _cancel_quietly(self) -> None:
        try:
            await asyncio.shield(self._bridge.nav_cancel())
        except (BridgeError, asyncio.CancelledError):
            pass


async def rehearse_goal(
    twin: TwinProfile,
    outgoing_action: dict,
    *,
    on_status: Callable[[str], None] | None = None,
    bridge: RosBridgeClient | None = None,
) -> GateReport:
    """One-shot rehearsal: create a gate, judge the goal, release the twin bridge.

    Callers that rehearse repeatedly can hold a TwinGate instead to keep the
    twin bridge warm; this helper never leaks the process it spawned.
    """
    gate = TwinGate(twin, bridge=bridge)
    try:
        return await gate.rehearse(outgoing_action, on_status=on_status)
    finally:
        if bridge is None:  # we own the bridge we created
            await gate.stop()
