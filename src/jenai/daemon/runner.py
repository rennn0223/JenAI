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
from jenai.capabilities import has_registered_capability
from jenai.config.models import AppConfig
from jenai.daemon.engine import Decision, Rule, RuleEngine
from jenai.state.audit import AuditStore
from jenai.task_results import navigation_output_result, navigation_receipt_text
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
            task_result = navigation_output_result(output)
            self._finish(decision, task_result.run_status.value, navigation_receipt_text(output))
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


class _DaemonRuntime:
    """Own event processing and every resource created after bridge startup."""

    def __init__(
        self,
        config: AppConfig,
        config_path: Path,
        rules: list[Rule],
        bridge: RosBridgeClient,
        on_decision: DecisionCallback,
        on_status: StatusCallback,
    ) -> None:
        self._config = config
        self._rules = rules
        self._bridge = bridge
        self._on_decision = on_decision
        self._on_status = on_status
        self._engine = RuleEngine(
            rules,
            nav_allowed=config.route_adapter == "nav2"
            and has_registered_capability(config, "navigate"),
        )
        audit_store = AuditStore.best_effort(config_path.parent / "audit.sqlite3")
        self._audit = _DecisionAudit(audit_store)
        self._navigation = NavigationGateway(
            config,
            get_bridge=self._get_bridge,
            config_path=config_path,
            audit_store=audit_store,
        )
        self._worker = _NavigationWorker(
            config.resolved_locations_path(config_path),
            self._navigation,
            on_status,
            self._audit,
        )
        self._queue: EventQueue = asyncio.Queue()
        self._perception: PerceptionLoop | None = None

    async def _get_bridge(self) -> RosBridgeClient:
        return self._bridge

    async def run(self) -> None:
        topic_rules = [rule for rule in self._rules if rule.topic != PERCEPTION_TOPIC]
        perception_rules = [rule for rule in self._rules if rule.topic == PERCEPTION_TOPIC]
        try:
            await _register_topic_watches(self._bridge, topic_rules, self._queue, self._on_status)
            self._perception = await _start_perception(
                self._config,
                self._bridge,
                perception_rules,
                self._queue,
                self._on_status,
            )
            await self._event_loop()
        except asyncio.CancelledError:
            raise
        finally:
            await self._close()

    async def _event_loop(self) -> None:
        while True:
            rule, data = await self._queue.get()
            await self._handle_event(rule, data)

    async def _handle_event(self, rule: Rule, data: EventData) -> None:
        decision = self._engine.handle_event(rule, data)
        self._record_trigger(decision)
        if decision.halt:
            await _execute_halt(
                self._config,
                self._bridge,
                decision,
                self._worker,
                self._on_status,
                self._audit,
            )
            return
        if decision.navigate_to:
            self._start_navigation(rule, decision)

    def _record_trigger(self, decision: Decision) -> None:
        if not decision.fired:
            return
        self._on_decision(decision)
        self._audit.record("event_triggered", decision, status="fired", summary=decision.reason)
        if not decision.halt and decision.navigate_to is None:
            outcome = "notified" if decision.rule.action == "notify" else "blocked"
            self._audit.record(
                "event_action_finished",
                decision,
                status=outcome,
                summary=decision.reason,
            )

    def _start_navigation(self, rule: Rule, decision: Decision) -> None:
        if not self._worker.active:
            self._worker.start(decision)
            return
        summary = "navigation already in progress — skipped"
        self._on_status(f"'{rule.name}': {summary}")
        self._audit.record("event_action_finished", decision, status="busy", summary=summary)

    async def _close(self) -> None:
        if self._perception is not None:
            await self._perception.stop()
        # Stop in-flight navigation first so cancellation reaches Nav2 before
        # the shared bridge transport is torn down.
        await self._worker.close()
        await self._navigation.close()
        try:
            await self._bridge.stop()
        except BridgeError:
            pass


async def run_daemon(
    config: AppConfig,
    config_path: Path,
    rules: list[Rule],
    *,
    on_decision: DecisionCallback,
    on_status: StatusCallback = lambda _s: None,
) -> None:
    """Watch every rule's topic through the bridge and act on decisions.

    Runs until cancelled. Decisions stream to `on_decision`; navigation rules
    send one robot goal at a time, while triggers received during an active
    goal are reported but never stacked.
    """
    bridge = RosBridgeClient()
    # Registered before start: every (re)spawn arms the watchdog, so a dead
    # daemon can never leave the robot driving — even after a bridge crash.
    await arm_watchdog(config, bridge)
    await bridge.start()
    on_status(f"bridge up · watching {len(rules)} rule(s)")
    runtime = _DaemonRuntime(config, config_path, rules, bridge, on_decision, on_status)
    await runtime.run()
