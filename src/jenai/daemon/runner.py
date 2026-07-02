from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from jenai.adapters.locations import LocationNotFoundError, find_location, load_locations
from jenai.bridge import BridgeError, RosBridgeClient
from jenai.config.models import AppConfig
from jenai.daemon.engine import Decision, Rule, RuleEngine
from jenai.tools.nav_live import navigate_live


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
    await bridge.start()
    on_status(f"bridge up · watching {len(rules)} rule(s)")

    loop = asyncio.get_running_loop()
    nav_busy = asyncio.Lock()
    queue: asyncio.Queue[tuple[Rule, dict]] = asyncio.Queue()

    for rule in rules:
        def _make_handler(r: Rule) -> Callable[[dict], None]:
            # Bridge events arrive on the reader task; hop through a queue so
            # rule handling (and navigation) happens in this task, in order.
            return lambda data: queue.put_nowait((r, data))

        await bridge.watch(rule.topic, rule.msg_type, _make_handler(rule), throttle=rule.throttle_s)
        on_status(f"watching {rule.topic} ({rule.fld}) for '{rule.name}'")

    locations_path = config.resolved_locations_path(config_path)

    async def _navigate(decision: Decision) -> None:
        if locations_path is None or not locations_path.exists():
            on_status(f"'{decision.rule.name}': no locations file — cannot navigate")
            return
        try:
            location = find_location(load_locations(locations_path), decision.navigate_to or "")
        except LocationNotFoundError:
            on_status(f"'{decision.rule.name}': unknown location '{decision.navigate_to}'")
            return
        async with nav_busy:
            on_status(f"'{decision.rule.name}': navigating to {location.name}")
            output = await navigate_live(
                bridge, {"goal": location.model_dump(mode="json")}
            )
            on_status(f"'{decision.rule.name}': {output.execution_status} — {output.route_preview}")

    try:
        while True:
            rule, data = await queue.get()
            decision = engine.handle_event(rule, data)
            if decision.fired:
                on_decision(decision)
            if decision.navigate_to:
                if nav_busy.locked():
                    on_status(f"'{rule.name}': navigation already in progress — skipped")
                else:
                    loop.create_task(_navigate(decision))
    except asyncio.CancelledError:
        raise
    finally:
        try:
            await bridge.stop()
        except BridgeError:
            pass
