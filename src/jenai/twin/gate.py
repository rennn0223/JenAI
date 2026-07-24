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
from dataclasses import dataclass, field
from functools import partial
from time import monotonic
from typing import Any, Literal
from uuid import uuid4

from jenai.bridge import BridgeError, PoseInfo, RosBridgeClient
from jenai.config.models import ForbiddenZone, TwinProfile
from jenai.schemas import GateCriterion, GateReport

CriterionId = Literal["G1", "G2", "G3", "G4", "G5"]
CriterionStatus = Literal["pass", "fail", "skipped"]
GateVerdict = Literal["pass", "block", "refer"]
CriterionStatuses = dict[CriterionId, tuple[CriterionStatus, str]]

_NAMES: dict[CriterionId, str] = {
    "G1": "collision",
    "G2": "timeout",
    "G3": "forbidden zone",
    "G4": "endpoint deviation",
    "G5": "Nav2 failure",
}
_HARD: tuple[CriterionId, ...] = ("G1", "G3")


def _criterion(cid: CriterionId, status: CriterionStatus, detail: str = "") -> GateCriterion:
    return GateCriterion(criterion_id=cid, name=_NAMES[cid], status=status, detail=detail)


def _report(
    verdict: GateVerdict,
    reason: str,
    elapsed: float,
    statuses: CriterionStatuses | None = None,
) -> GateReport:
    """Build a report; criteria not mentioned in `statuses` are 'skipped'."""
    statuses = statuses or {}
    criteria = [_criterion(cid, *statuses.get(cid, ("skipped", ""))) for cid in _NAMES]
    return GateReport(
        verdict=verdict, reason=reason, criteria=criteria, twin_elapsed_s=round(elapsed, 2)
    )


@dataclass
class _RehearsalState:
    """Mutable evidence collected during one isolated twin rehearsal."""

    result_future: asyncio.Future[str]
    tag: str = field(default_factory=lambda: uuid4().hex[:8])
    collision: list[str] = field(default_factory=list)
    zone_hit: list[str] = field(default_factory=list)
    pose_samples: list[int] = field(default_factory=lambda: [0])
    status: str = "failed"
    timed_out: bool = False
    gate_canceled: bool = False
    watch_id: int | None = None


class TwinGate:
    """Rehearses one goal at a time in the twin and issues pass/block/refer."""

    def __init__(self, twin: TwinProfile, bridge: RosBridgeClient | None = None) -> None:
        self._twin = twin
        self._bridge = bridge if bridge is not None else RosBridgeClient(domain_id=twin.domain_id)

    async def stop(self) -> None:
        await self._bridge.stop()

    async def rehearse(
        self,
        outgoing_action: dict[str, Any],
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
        _say(
            f"twin gate: {report.verdict.upper()}"
            + (f" — {report.reason}" if report.reason else f" (twin took {elapsed:.0f}s)")
        )
        return report

    async def _run_rehearsal(self, goal: dict[str, Any], gx: float, gy: float) -> dict[str, Any]:
        """Drive the twin's Nav2 and collect raw observations for judging."""
        state = _RehearsalState(asyncio.get_running_loop().create_future())
        on_result = partial(self._record_nav_result, state)
        on_contact = partial(self._record_contact, state)

        self._bridge.on_event("nav_result", on_result)
        state.watch_id = await self._watch_contact(on_contact)
        await self._capture_initial_pose(state)

        sampler = asyncio.create_task(self._sample_trajectory(state.zone_hit, state.pose_samples))
        try:
            await self._execute_twin_goal(goal, gx, gy, state)
        except asyncio.CancelledError:
            await self._cancel_quietly()
            raise
        finally:
            await self._stop_observers(sampler, on_result, state.watch_id)

        deviation = await self._endpoint_deviation(state, gx, gy)
        return {
            "status": state.status,
            "timed_out": state.timed_out,
            "we_canceled": state.gate_canceled,
            "collision": state.collision[0] if state.collision else None,
            "zone_hit": state.zone_hit[0] if state.zone_hit else None,
            "pose_samples": state.pose_samples[0],
            "deviation": deviation,
            "watched_contact": state.watch_id is not None,
        }

    @staticmethod
    def _record_nav_result(state: _RehearsalState, event: dict[str, Any]) -> None:
        """Record the matching Nav2 terminal event exactly once."""
        if event.get("tag", "") in ("", state.tag) and not state.result_future.done():
            state.result_future.set_result(str(event.get("status", "failed")))

    def _record_contact(self, state: _RehearsalState, data: dict[str, Any]) -> None:
        """Record the first collision sensor assertion."""
        if data.get("data") and not state.collision:
            state.collision.append(f"contact reported on {self._twin.collision_topic}")

    async def _watch_contact(self, callback: Callable[[dict[str, Any]], None]) -> int | None:
        """Start optional collision monitoring; absence remains explicit evidence."""
        try:
            return await self._bridge.watch(
                self._twin.collision_topic,
                "std_msgs/msg/Bool",
                callback,
                throttle=0.1,
            )
        except BridgeError:
            return None

    async def _capture_initial_pose(self, state: _RehearsalState) -> None:
        """Capture G3 evidence even when Nav2 immediately accepts the current pose."""
        try:
            initial_pose = await self._bridge.get_pose(timeout=2.0)
        except BridgeError:
            return
        self._record_pose_sample(initial_pose, state.zone_hit, state.pose_samples)

    async def _execute_twin_goal(
        self,
        goal: dict[str, Any],
        gx: float,
        gy: float,
        state: _RehearsalState,
    ) -> None:
        """Execute until Nav2 completes or gate evidence requires cancellation."""
        await self._bridge.nav_send(
            x=gx,
            y=gy,
            yaw=float((goal.get("pose") or {}).get("yaw", 0.0)),
            frame_id=goal.get("frame_id", "map"),
            tag=state.tag,
        )
        deadline = monotonic() + self._twin.nav_timeout_s
        while not state.result_future.done():
            if state.collision or state.zone_hit:
                state.gate_canceled = True
                await self._cancel_quietly()
                return
            if monotonic() > deadline:
                state.timed_out = True
                state.gate_canceled = True
                await self._cancel_quietly()
                return
            await asyncio.sleep(0.2)
        state.status = state.result_future.result()

    async def _stop_observers(
        self,
        sampler: asyncio.Task[None],
        on_result: Callable[[dict[str, Any]], None],
        watch_id: int | None,
    ) -> None:
        """Release all per-rehearsal observers without hiding the primary result."""
        sampler.cancel()
        self._bridge.off_event("nav_result", on_result)
        if watch_id is None:
            return
        try:
            await self._bridge.unwatch(watch_id)
        except BridgeError:
            pass

    async def _endpoint_deviation(
        self, state: _RehearsalState, gx: float, gy: float
    ) -> float | None:
        """Measure final endpoint error only for successful Nav2 results."""
        if state.status != "succeeded":
            return None
        try:
            pose = await self._bridge.get_pose(timeout=2.0)
        except BridgeError:
            return None
        self._record_pose_sample(pose, state.zone_hit, state.pose_samples)
        return math.hypot(pose.x - gx, pose.y - gy)

    def _judge(self, outcome: dict[str, Any], elapsed: float) -> GateReport:
        statuses: CriterionStatuses = {}
        g1, g1_inconclusive = self._judge_collision(outcome)
        if g1 is not None:
            statuses["G1"] = g1
        statuses["G2"] = self._judge_timeout(outcome)
        statuses["G3"], g3_inconclusive = self._judge_forbidden_zones(outcome)
        g4 = self._judge_endpoint(outcome)
        if g4 is not None:
            statuses["G4"] = g4
        g5 = self._judge_nav2(outcome)
        if g5 is not None:
            statuses["G5"] = g5

        failed = {cid for cid, (status, _) in statuses.items() if status == "fail"}
        hard_failed = failed & set(_HARD)
        verdict = self._verdict(failed, hard_failed, g1_inconclusive, g3_inconclusive)
        reason = self._reason(statuses, failed, hard_failed, g1_inconclusive, g3_inconclusive)
        return _report(verdict, reason, elapsed, statuses)

    def _judge_timeout(self, outcome: dict[str, Any]) -> tuple[CriterionStatus, str]:
        """Translate rehearsal timeout evidence into the G2 criterion."""
        if outcome["timed_out"]:
            return "fail", f"twin exceeded {self._twin.nav_timeout_s:.0f}s"
        return "pass", ""

    def _judge_collision(
        self, outcome: dict[str, Any]
    ) -> tuple[tuple[CriterionStatus, str] | None, bool]:
        """Return G1 status and whether collision evidence was required but absent."""
        inconclusive = bool(
            self._twin.require_collision_evidence and not outcome["watched_contact"]
        )
        if outcome["watched_contact"]:
            status: tuple[CriterionStatus, str] = (
                ("fail", str(outcome["collision"])) if outcome["collision"] else ("pass", "")
            )
            return status, False
        if inconclusive:
            return (
                (
                    "skipped",
                    "collision evidence unavailable; the configured contact topic was not observed",
                ),
                True,
            )
        return None, False

    def _judge_forbidden_zones(
        self, outcome: dict[str, Any]
    ) -> tuple[tuple[CriterionStatus, str], bool]:
        """Return G3 status and whether the zone check lacked pose evidence."""
        if outcome["zone_hit"]:
            return ("fail", outcome["zone_hit"]), False
        inconclusive = bool(self._twin.forbidden_zones and outcome["pose_samples"] == 0)
        if inconclusive:
            return ("skipped", "no twin pose samples; forbidden zones were not checked"), True
        return ("pass", ""), False

    def _judge_endpoint(self, outcome: dict[str, Any]) -> tuple[CriterionStatus, str] | None:
        """Return G4 status when endpoint evidence is meaningful."""
        deviation = outcome["deviation"]
        if deviation is not None:
            if deviation <= self._twin.goal_tolerance_m:
                return "pass", ""
            return (
                "fail",
                f"stopped {deviation:.2f} m from the goal "
                f"(tolerance {self._twin.goal_tolerance_m:.2f} m)",
            )
        if outcome["status"] == "succeeded":
            return "fail", "twin final pose unavailable; endpoint not verified"
        return None

    @staticmethod
    def _judge_nav2(outcome: dict[str, Any]) -> tuple[CriterionStatus, str] | None:
        """Return G5 status, omitting results caused by the gate's own cancellation."""
        if outcome["status"] == "succeeded":
            return "pass", ""
        if outcome["we_canceled"]:
            return None
        return "fail", f"twin Nav2 ended with '{outcome['status']}'"

    @staticmethod
    def _verdict(
        failed: set[CriterionId],
        hard_failed: set[CriterionId],
        g1_inconclusive: bool,
        g3_inconclusive: bool,
    ) -> GateVerdict:
        """Apply the documented hard-fail and fail-closed gate policy."""
        if hard_failed:
            return "block"
        if failed or g1_inconclusive or g3_inconclusive:
            return "refer"
        return "pass"

    @staticmethod
    def _reason(
        statuses: CriterionStatuses,
        failed: set[CriterionId],
        hard_failed: set[CriterionId],
        g1_inconclusive: bool,
        g3_inconclusive: bool,
    ) -> str:
        """Select the highest-priority human-readable gate reason."""
        priorities: list[CriterionId] = [cid for cid in _NAMES if cid in hard_failed]
        if g1_inconclusive:
            priorities.append("G1")
        if g3_inconclusive:
            priorities.append("G3")
        priorities.extend(cid for cid in _NAMES if cid in failed)
        return statuses[priorities[0]][1] if priorities else ""

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
                self._record_pose_sample(p, zone_hit, pose_samples)
                if zone_hit:
                    return
            await asyncio.sleep(self._twin.pose_sample_s)

    def _record_pose_sample(
        self, pose: PoseInfo, zone_hit: list[str], pose_samples: list[int]
    ) -> None:
        """Count finite G3 evidence and record the first forbidden-zone hit."""
        # NaN/inf never falls inside a zone, so counting it would falsely mark
        # a broken twin localization as checked.
        if not (math.isfinite(pose.x) and math.isfinite(pose.y)):
            return
        pose_samples[0] += 1
        zone = self._zone_at(pose.x, pose.y)
        if zone is not None and not zone_hit:
            zone_hit.append(f"twin entered '{zone.name}' at ({pose.x:.2f}, {pose.y:.2f})")

    def _zone_at(self, x: float, y: float) -> ForbiddenZone | None:
        return next((z for z in self._twin.forbidden_zones if z.contains(x, y)), None)

    async def _cancel_quietly(self) -> None:
        try:
            await asyncio.shield(self._bridge.nav_cancel())
        except (BridgeError, asyncio.CancelledError):
            pass


async def rehearse_goal(
    twin: TwinProfile,
    outgoing_action: dict[str, Any],
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
