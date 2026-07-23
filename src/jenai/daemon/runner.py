"""Daemon wiring: bridge watch → queue → engine → (gated) action."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jenai.adapters.locations import LocationNotFoundError, find_location, load_locations
from jenai.bridge import BridgeError, RosBridgeClient
from jenai.config.models import AppConfig
from jenai.daemon.engine import Decision, Rule, RuleEngine
from jenai.state.audit import AuditStore
from jenai.tools.navigation_gateway import NavigationGateway
from jenai.tools.perception import PerceptionLoop
from jenai.tools.safety import arm_watchdog, halt_robot

PERCEPTION_TOPIC = "@perception"  # rule.topic sentinel: trigger on camera VLM analyses
logger = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]
DecisionCallback = Callable[[Decision], None]
EventData = dict[str, Any]
EventQueue = asyncio.Queue[tuple[Rule, EventData]]


@dataclass(slots=True)
class _DecisionAudit:
    """Best-effort autonomous decision receipts; never part of control flow."""

    store: AuditStore | None

    def record(
        self,
        event_type: str,
        decision: Decision,
        *,
        status: str,
        summary: str | None = None,
    ) -> None:
        if self.store is None:
            return
        try:
            self.store.record(
                event_type,
                entity_id=decision.rule.name,
                status=status,
                summary=summary,
                details={
                    "source": decision.rule.topic,
                    "field": decision.rule.fld,
                    "configured_action": decision.rule.action,
                    "reason": decision.reason,
                },
            )
        except Exception:
            # Observability can never delay a stop or autonomous safety path.
            logger.warning("Autonomous decision audit failed", exc_info=True)


class _NavigationWorker:
    """Own exactly one autonomous navigation task and its cancellation receipt."""

    def __init__(
        self,
        locations_path: Path | None,
        gateway: NavigationGateway,
        on_status: StatusCallback,
        audit: _DecisionAudit,
    ) -> None:
        self._locations_path = locations_path
        self._gateway = gateway
        self._on_status = on_status
        self._audit = audit
        self._task: asyncio.Task[None] | None = None
        self._cancel_summary = "daemon shutdown"

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, decision: Decision) -> None:
        if self.active:
            raise RuntimeError("navigation worker already owns an active task")
        self._cancel_summary = "daemon shutdown"
        self._task = asyncio.create_task(self._run(decision))

    def preempt(self, reason: str) -> None:
        if not self.active or self._task is None:
            return
        self._cancel_summary = reason
        self._task.cancel()

    async def close(self) -> None:
        self.preempt("daemon shutdown")
        if self._task is not None:
            # Bridge teardown must run even when a dying task surfaces an
            # unexpected transport exception.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    async def _run(self, decision: Decision) -> None:
        """Resolve a named goal and contain every fire-and-forget failure."""
        started = False
        try:
            if self._locations_path is None or not self._locations_path.exists():
                self._finish(decision, "blocked", "no locations file — cannot navigate")
                return
            try:
                location = find_location(
                    load_locations(self._locations_path), decision.navigate_to or ""
                )
            except LocationNotFoundError:
                self._finish(decision, "blocked", f"unknown location '{decision.navigate_to}'")
                return

            self._on_status(f"'{decision.rule.name}': navigating to {location.name}")
            self._audit.record(
                "event_action_started",
                decision,
                status="running",
                summary=f"navigate to {location.name}",
            )
            started = True
            output = await self._gateway.execute(
                {"goal": location.model_dump(mode="json")}, on_gate=self._on_status
            )
            self._finish(
                decision,
                output.execution_status,
                f"{output.execution_status} — {output.route_preview}",
            )
        except asyncio.CancelledError:
            if started:
                self._finish(decision, "cancelled", self._cancel_summary, prefix="cancelled — ")
            raise
        except Exception as exc:
            self._finish(decision, "failed", f"navigation failed — {exc}")

    def _finish(
        self,
        decision: Decision,
        status: str,
        summary: str,
        *,
        prefix: str = "",
    ) -> None:
        self._on_status(f"'{decision.rule.name}': {prefix}{summary}")
        self._audit.record("event_action_finished", decision, status=status, summary=summary)


async def _register_topic_watches(
    bridge: RosBridgeClient,
    rules: list[Rule],
    queue: EventQueue,
    on_status: StatusCallback,
) -> None:
    def handler_for(rule: Rule) -> Callable[[EventData], None]:
        # Bridge events arrive on the reader task; hop through a queue so rule
        # handling and navigation remain ordered in the daemon task.
        return lambda data: queue.put_nowait((rule, data))

    for rule in rules:
        await bridge.watch(
            rule.topic,
            rule.msg_type,
            handler_for(rule),
            throttle=rule.throttle_s,
        )
        on_status(f"watching {rule.topic} ({rule.fld}) for '{rule.name}'")


async def _start_perception(
    config: AppConfig,
    bridge: RosBridgeClient,
    rules: list[Rule],
    queue: EventQueue,
    on_status: StatusCallback,
) -> PerceptionLoop | None:
    if not rules:
        return None

    async def on_analysis(analysis: Any) -> None:
        data = analysis.model_dump(mode="json")
        for rule in rules:
            queue.put_nowait((rule, data))

    async def perception_status(message: str) -> None:
        on_status(message)

    tick_s = min(rule.throttle_s for rule in rules)
    perception = PerceptionLoop(
        config,
        bridge,
        hz=1.0 / max(0.1, tick_s),
        on_analysis=on_analysis,
        on_status=perception_status,
    )
    await perception.start()
    names = ", ".join(f"'{rule.name}'" for rule in rules)
    on_status(
        f"perception loop up · {perception.topic} @ {1.0 / max(0.1, tick_s):.1f}Hz for {names}"
    )
    return perception


async def _execute_halt(
    config: AppConfig,
    bridge: RosBridgeClient,
    decision: Decision,
    worker: _NavigationWorker,
    on_status: StatusCallback,
    audit: _DecisionAudit,
) -> None:
    worker.preempt(f"preempted by halt rule '{decision.rule.name}'")
    try:
        summary = await halt_robot(config, bridge)
        status = "succeeded"
    except BridgeError as exc:
        summary = f"halt failed — {exc}"
        status = "failed"
    on_status(f"'{decision.rule.name}': {summary}")
    audit.record("event_action_finished", decision, status=status, summary=summary)


async def run_daemon(
    config: AppConfig,
    config_path: Path,
    rules: list[Rule],
    *,
    on_decision: Callable[[Decision], None],
    on_status: Callable[[str], None] = lambda _s: None,
) -> None:
    """Watch every rule's topic through the bridge and act on decisions.

    Runs until cancelled. Decisions stream to `on_decision` (the CLI prints
    them); a decision with navigate_to set actually sends the robot, one goal
    at a time — a new trigger while navigating is reported but not stacked.
    """
    engine = RuleEngine(rules, nav_allowed=config.route_adapter == "nav2")
    bridge = RosBridgeClient()
    # Registered before start: every (re)spawn arms the watchdog, so a dead
    # daemon can never leave the robot driving — even after a bridge crash.
    await arm_watchdog(config, bridge)
    await bridge.start()
    on_status(f"bridge up · watching {len(rules)} rule(s)")

    async def _get_bridge() -> RosBridgeClient:
        return bridge

    audit_store = AuditStore.best_effort(config_path.parent / "audit.sqlite3")
    audit = _DecisionAudit(audit_store)
    navigation = NavigationGateway(
        config,
        get_bridge=_get_bridge,
        audit_store=audit_store,
    )
    worker = _NavigationWorker(
        config.resolved_locations_path(config_path), navigation, on_status, audit
    )
    queue: EventQueue = asyncio.Queue()

    topic_rules = [rule for rule in rules if rule.topic != PERCEPTION_TOPIC]
    perception_rules = [rule for rule in rules if rule.topic == PERCEPTION_TOPIC]
    perception: PerceptionLoop | None = None
    try:
        # Registration and perception startup belong to the same ownership
        # scope as the steady-state loop. A partial startup must still stop the
        # already-running bridge and any successfully-created resources.
        await _register_topic_watches(bridge, topic_rules, queue, on_status)
        perception = await _start_perception(config, bridge, perception_rules, queue, on_status)
        while True:
            rule, data = await queue.get()
            decision = engine.handle_event(rule, data)
            if decision.fired:
                on_decision(decision)
                audit.record("event_triggered", decision, status="fired", summary=decision.reason)
                if not decision.halt and decision.navigate_to is None:
                    outcome = "notified" if decision.rule.action == "notify" else "blocked"
                    audit.record(
                        "event_action_finished",
                        decision,
                        status=outcome,
                        summary=decision.reason,
                    )
            if decision.halt:
                await _execute_halt(config, bridge, decision, worker, on_status, audit)
                continue
            if decision.navigate_to:
                if worker.active:
                    summary = "navigation already in progress — skipped"
                    on_status(f"'{rule.name}': {summary}")
                    audit.record("event_action_finished", decision, status="busy", summary=summary)
                else:
                    worker.start(decision)
    except asyncio.CancelledError:
        raise
    finally:
        if perception is not None:
            await perception.stop()
        # Stop an in-flight navigation first (cancels the Nav2 goal, so the
        # robot actually halts), then tear down the bridge.
        await worker.close()
        await navigation.close()
        try:
            await bridge.stop()
        except BridgeError:
            pass
