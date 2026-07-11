"""Daemon wiring: bridge watch → queue → engine → (gated) action."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path

from jenai.adapters.locations import LocationNotFoundError, find_location, load_locations
from jenai.bridge import BridgeError, RosBridgeClient
from jenai.config.models import AppConfig
from jenai.daemon.engine import Decision, Rule, RuleEngine
from jenai.state.audit import AuditStore
from jenai.tools.navigation_gateway import NavigationGateway
from jenai.tools.perception import PerceptionLoop
from jenai.tools.safety import arm_watchdog, halt_robot

PERCEPTION_TOPIC = "@perception"  # rule.topic sentinel: trigger on camera VLM analyses


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
    navigation = NavigationGateway(
        config,
        get_bridge=_get_bridge,
        audit_store=audit_store,
    )

    loop = asyncio.get_running_loop()
    # One navigation at a time. The consumer loop is the only place that
    # creates this task, so checking it right before create_task is race-free —
    # unlike a lock's .locked(), which can miss a task that hasn't started yet.
    nav_task: asyncio.Task | None = None
    queue: asyncio.Queue[tuple[Rule, dict]] = asyncio.Queue()

    topic_rules = [rule for rule in rules if rule.topic != PERCEPTION_TOPIC]
    perception_rules = [rule for rule in rules if rule.topic == PERCEPTION_TOPIC]

    for rule in topic_rules:
        def _make_handler(r: Rule) -> Callable[[dict], None]:
            # Bridge events arrive on the reader task; hop through a queue so
            # rule handling (and navigation) happens in this task, in order.
            return lambda data: queue.put_nowait((r, data))

        await bridge.watch(rule.topic, rule.msg_type, _make_handler(rule), throttle=rule.throttle_s)
        on_status(f"watching {rule.topic} ({rule.fld}) for '{rule.name}'")

    perception: PerceptionLoop | None = None
    if perception_rules:
        # One camera loop feeds every @perception rule; each SceneAnalysis is
        # queued per rule and evaluated by the SAME engine (same cooldowns,
        # same action gating) as numeric threshold rules — perception never
        # gets a shortcut around the approval machinery.
        async def _on_analysis(analysis) -> None:
            data = analysis.model_dump(mode="json")
            for rule in perception_rules:
                queue.put_nowait((rule, data))

        async def _perception_status(message: str) -> None:
            on_status(message)

        tick_s = min(rule.throttle_s for rule in perception_rules)
        perception = PerceptionLoop(
            config,
            bridge,
            hz=1.0 / max(0.1, tick_s),
            on_analysis=_on_analysis,
            on_status=_perception_status,
        )
        await perception.start()
        names = ", ".join(f"'{rule.name}'" for rule in perception_rules)
        on_status(
            f"perception loop up · {perception.topic} @ {1.0 / max(0.1, tick_s):.1f}Hz for {names}"
        )

    locations_path = config.resolved_locations_path(config_path)

    async def _navigate(decision: Decision) -> None:
        # Fire-and-forget task: nothing awaits it, so every failure must be
        # reported here — an uncaught exception would kill the navigation
        # silently (the rule fires, the robot never moves, nobody learns why).
        try:
            if locations_path is None or not locations_path.exists():
                on_status(f"'{decision.rule.name}': no locations file — cannot navigate")
                return
            try:
                location = find_location(
                    load_locations(locations_path), decision.navigate_to or ""
                )
            except LocationNotFoundError:
                on_status(f"'{decision.rule.name}': unknown location '{decision.navigate_to}'")
                return
            goal_action = {"goal": location.model_dump(mode="json")}
            on_status(f"'{decision.rule.name}': navigating to {location.name}")
            output = await navigation.execute(goal_action, on_gate=on_status)
            on_status(
                f"'{decision.rule.name}': {output.execution_status} — {output.route_preview}"
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            on_status(f"'{decision.rule.name}': navigation failed — {exc}")

    try:
        while True:
            rule, data = await queue.get()
            decision = engine.handle_event(rule, data)
            if decision.fired:
                on_decision(decision)
            if decision.halt:
                # Emergency stop outranks everything: kill any in-flight
                # navigation task, then send the halt itself.
                if nav_task is not None and not nav_task.done():
                    nav_task.cancel()
                try:
                    on_status(f"'{rule.name}': {await halt_robot(config, bridge)}")
                except BridgeError as exc:
                    on_status(f"'{rule.name}': halt failed — {exc}")
                continue
            if decision.navigate_to:
                if nav_task is not None and not nav_task.done():
                    on_status(f"'{rule.name}': navigation already in progress — skipped")
                else:
                    nav_task = loop.create_task(_navigate(decision))
    except asyncio.CancelledError:
        raise
    finally:
        if perception is not None:
            await perception.stop()
        # Stop an in-flight navigation first (cancels the Nav2 goal, so the
        # robot actually halts), then tear down the bridge.
        if nav_task is not None and not nav_task.done():
            nav_task.cancel()
            # Suppress *anything* the dying task raises (a dead bridge pipe
            # surfaces as BrokenPipeError, not BridgeError) — bridge.stop()
            # below must always run or the rclpy subprocess is leaked.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await nav_task
        try:
            await bridge.stop()
        except BridgeError:
            pass
