from __future__ import annotations

import asyncio
from pathlib import Path

from jenai.config.store import build_minimal_config
from jenai.providers import ChatResponse
from jenai.schemas import (
    PlanStep,
    RosPubOutput,
    RosSchemaOutput,
    RosTopicsOutput,
    RouteOutput,
    TopicItem,
)
from jenai.tools.ros2_core import Ros2PubValidation
from jenai.tui import JenAITuiApp
from jenai.tui.app import pixel_mark
from jenai.tui.widgets import ApprovalCard


def _app(tmp_path: Path | None = None) -> JenAITuiApp:
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="",
    )
    return JenAITuiApp(config=config, config_path=Path(str(tmp_path or "/tmp")) / "config.toml")


def test_tui_composes_jenai_shell() -> None:
    async def run() -> None:
        app = JenAITuiApp(
            config=build_minimal_config(
                provider_name="test",
                provider="openai",
                default_model="gpt-test",
                api_key_env="",
            ),
            config_path=Path("/tmp/config.toml"),
        )
        async with app.run_test():
            assert app.query_one("#header")
            assert app.query_one("#window")
            welcome = app.query_one("#welcome")
            assert welcome.region.y <= 4
            assert app.query_one("#events")
            assert app.query_one("#composer")
            assert app.export_screenshot().startswith("<svg")

    asyncio.run(run())


def test_tui_handles_local_commands() -> None:
    async def run() -> None:
        app = JenAITuiApp(
            config=build_minimal_config(
                provider_name="test",
                provider="openai",
                default_model="gpt-test",
                api_key_env="",
            ),
            config_path=Path("/tmp/config.toml"),
        )
        async with app.run_test():
            await app.handle_user_text("/help")
            await app.handle_user_text("/status")
            await app.handle_user_text("/models")
            await app.handle_user_text("/clear")

            events = app.query_one("#events")
            assert len(list(events.children)) == 1

    asyncio.run(run())


def test_tui_shows_slash_command_palette() -> None:
    async def run() -> None:
        app = JenAITuiApp(
            config=build_minimal_config(
                provider_name="test",
                provider="openai",
                default_model="gpt-test",
                api_key_env="",
            ),
            config_path=Path("/tmp/config.toml"),
        )
        async with app.run_test() as pilot:
            await pilot.press("/")

            palette = app.query_one("#palette")
            assert palette.display is True
            assert app._command_matches

            await pilot.press("s")
            assert app._command_matches[0].name == "/status"

            await pilot.press("tab")
            assert app.query_one("#composer").value == "/status "
            assert palette.display is False

    asyncio.run(run())


def test_tui_uses_colored_dachshund_mascot() -> None:
    mascot = pixel_mark()
    styles = {str(span.style) for span in mascot.spans}

    assert any("#d98c69" in style for style in styles)
    assert any("#5fb1c0" in style for style in styles)
    assert 4 <= mascot.plain.count("\n") <= 7


def test_tui_sends_natural_language_to_provider(monkeypatch) -> None:
    async def fake_ask_provider(config, prompt):
        calls.append((config.active_provider, prompt))
        return ChatResponse(content="LLM says hi", model="gpt-test", provider="test")

    async def run() -> None:
        app = JenAITuiApp(
            config=build_minimal_config(
                provider_name="test",
                provider="openai",
                default_model="gpt-test",
                api_key_env="",
            ),
            config_path=Path("/tmp/config.toml"),
        )
        async with app.run_test():
            await app.handle_user_text("hi")

    calls = []
    monkeypatch.setattr("jenai.tui.app.ask_provider", fake_ask_provider)

    asyncio.run(run())

    assert calls == [("test", "hi")]


def test_tui_ros_topics_command(monkeypatch) -> None:
    async def fake_ros_topics(config):
        return RosTopicsOutput(topics=[TopicItem(name="/cmd_vel", kind_hint="control")])

    monkeypatch.setattr("jenai.tui.app.ros_topics", fake_ros_topics)

    async def run() -> None:
        app = _app()
        async with app.run_test():
            await app.handle_user_text("/ros topics")
            panels = [w for w in app.query_one("#events").children if hasattr(w, "title")]
            assert any(p.title == "ROS2 topics" for p in panels)

    asyncio.run(run())


def test_tui_ros_schema_command(monkeypatch) -> None:
    async def fake_ros_schema(config, topic):
        assert topic == "/cmd_vel"
        return RosSchemaOutput(
            topic=topic, message_type="geometry_msgs/msg/Twist", raw_interface="..."
        )

    monkeypatch.setattr("jenai.tui.app.ros_schema", fake_ros_schema)

    async def run() -> None:
        app = _app()
        async with app.run_test():
            await app.handle_user_text("/ros schema /cmd_vel")
            panels = [w for w in app.query_one("#events").children if hasattr(w, "title")]
            assert any("Schema" in p.title for p in panels)

    asyncio.run(run())


def test_tui_ros_pub_shows_card_and_resolves_on_approve(monkeypatch) -> None:
    async def fake_validate(topic, payload):
        return Ros2PubValidation(
            ok=True, message_type="geometry_msgs/msg/Twist", payload_preview=payload
        )

    async def fake_execute(topic, message_type, payload):
        return RosPubOutput(
            topic=topic,
            message_type=message_type,
            execution_status="succeeded",
            result_message="published",
        )

    monkeypatch.setattr("jenai.tui.app.ros_pub_validate", fake_validate)
    monkeypatch.setattr("jenai.tui.app.ros_pub_execute", fake_execute)

    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            await app.handle_user_text('/ros pub /cmd_vel {"linear": {"x": 0.5}}')
            cards = list(app.query(ApprovalCard))
            assert len(cards) == 1
            assert cards[0].approval.risk_level == "p1"

            await pilot.press("enter")
            await pilot.pause()
            assert list(app.query(ApprovalCard)) == []

    asyncio.run(run())


def test_tui_ros_pub_rejects_on_escape(monkeypatch) -> None:
    async def fake_validate(topic, payload):
        return Ros2PubValidation(
            ok=True, message_type="geometry_msgs/msg/Twist", payload_preview=payload
        )

    executed = []

    async def fake_execute(topic, message_type, payload):
        executed.append(topic)
        return RosPubOutput(topic=topic, message_type=message_type)

    monkeypatch.setattr("jenai.tui.app.ros_pub_validate", fake_validate)
    monkeypatch.setattr("jenai.tui.app.ros_pub_execute", fake_execute)

    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            await app.handle_user_text('/ros pub /cmd_vel {"linear": {"x": 0.5}}')
            await pilot.press("escape")
            await pilot.pause()
            assert list(app.query(ApprovalCard)) == []

    asyncio.run(run())

    assert executed == []


def test_tui_ros_pub_invalid_json_shows_error(monkeypatch) -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test():
            await app.handle_user_text("/ros pub /cmd_vel not-json")
            assert list(app.query(ApprovalCard)) == []

    asyncio.run(run())


def test_tui_route_shows_card_and_resolves(monkeypatch, tmp_path) -> None:
    from jenai.schemas import Location, Pose2D

    start = Location(name="A", frame_id="map", pose=Pose2D(x=0, y=0, yaw=0))
    goal = Location(name="B", frame_id="map", pose=Pose2D(x=1, y=1, yaw=0))

    async def fake_route_preview(config, locations, text):
        return RouteOutput(
            input_text=text,
            resolved_start=start,
            resolved_goal=goal,
            route_preview="Route from A to B.",
            outgoing_action={"start": "A", "goal": "B"},
        )

    async def fake_route_execute(config, outgoing_action):
        return RouteOutput(
            input_text="", outgoing_action=outgoing_action, execution_status="sent (stub)"
        )

    monkeypatch.setattr("jenai.tui.app.route_preview", fake_route_preview)
    monkeypatch.setattr("jenai.tui.app.route_execute", fake_route_execute)

    async def run() -> None:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await app.handle_user_text("/route from A to B")
            cards = list(app.query(ApprovalCard))
            assert len(cards) == 1

            await pilot.press("enter")
            await pilot.pause()
            assert list(app.query(ApprovalCard)) == []

    asyncio.run(run())


def test_tui_plan_command(monkeypatch) -> None:
    async def fake_run_plan(ctx, task):
        ctx.run.task_summary = "Patrol area A"
        ctx.run.plan_steps = [PlanStep(title="Move to A", description="drive", reason="asked")]
        ctx.run.final_output = "Patrol report"
        ctx.run.status = "completed"
        return ctx.run

    monkeypatch.setattr("jenai.tui.app.run_plan", fake_run_plan)

    async def run() -> None:
        app = _app()
        async with app.run_test():
            await app.handle_user_text("/plan patrol area A")
            events = list(app.query_one("#events").children)
            assert any(getattr(w, "title_text", "").startswith("Plan:") for w in events)

    asyncio.run(run())


def test_tui_plan_requires_argument() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test():
            await app.handle_user_text("/plan")
            events = list(app.query_one("#events").children)
            assert any("Usage: /plan" in getattr(w, "body", "") for w in events)

    asyncio.run(run())


def test_tui_permissions_and_provider_commands() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test():
            await app.handle_user_text("/permissions")
            await app.handle_user_text("/provider")
            panels = [w for w in app.query_one("#events").children if hasattr(w, "title")]
            assert any(p.title == "Permissions" for p in panels)
            assert any(p.title == "Provider" for p in panels)

    asyncio.run(run())


def test_tui_why_with_no_active_run() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test():
            await app.handle_user_text("/why")
            events = list(app.query_one("#events").children)
            assert any("No active run yet" in getattr(w, "body", "") for w in events)

    asyncio.run(run())


def test_tui_abort_with_no_active_run() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test():
            await app.handle_user_text("/abort")
            events = list(app.query_one("#events").children)
            assert any("No active run to abort" in getattr(w, "body", "") for w in events)

    asyncio.run(run())


def test_tui_run_command_full_approval_cycle(monkeypatch) -> None:
    import json

    from agents import Runner

    class _FakeApprovalItem:
        def __init__(self, tool_name, call_id, arguments):
            self.tool_name = tool_name
            self.call_id = call_id
            self.arguments = json.dumps(arguments)

    class _FakeState:
        def __init__(self, interruptions):
            self._interruptions = interruptions

        def get_interruptions(self):
            return self._interruptions

        def approve(self, item, always_approve=False):
            pass

        def reject(self, item, always_reject=False, *, rejection_message=None):
            pass

    class _FakeResult:
        def __init__(self, state, final_output=""):
            self._state = state
            self.final_output = final_output

        def to_state(self):
            return self._state

    call_count = {"n": 0}

    async def fake_run(agent, task_input, *, context=None, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            item = _FakeApprovalItem("ros_pub_execute_tool", "call_1", {"topic": "/cmd_vel"})
            return _FakeResult(_FakeState([item]))
        return _FakeResult(_FakeState([]), final_output="Published via agent run.")

    monkeypatch.setattr(Runner, "run", fake_run)
    monkeypatch.setenv("JENAI_TEST_KEY", "secret")

    async def run() -> None:
        config = build_minimal_config(
            provider_name="test",
            provider="openai",
            default_model="gpt-test",
            api_key_env="JENAI_TEST_KEY",
        )
        app = JenAITuiApp(config=config, config_path=Path("/tmp/config.toml"))
        async with app.run_test() as pilot:
            await app.handle_user_text("/run publish forward velocity")
            cards = list(app.query(ApprovalCard))
            assert len(cards) == 1

            await pilot.press("enter")
            await pilot.pause()
            assert list(app.query(ApprovalCard)) == []
            assert call_count["n"] == 2

    asyncio.run(run())


def test_tui_input_history_navigation() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            await app.handle_user_text("/status")
            await app.handle_user_text("/help")

            composer = app.query_one("#composer")
            composer.focus()

            await pilot.press("up")
            assert composer.value == "/help"

            await pilot.press("up")
            assert composer.value == "/status"

            await pilot.press("down")
            assert composer.value == "/help"

            await pilot.press("down")
            assert composer.value == ""

    asyncio.run(run())


def test_tui_typing_resets_history_cursor() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            await app.handle_user_text("/status")
            await app.handle_user_text("/help")

            composer = app.query_one("#composer")
            composer.focus()

            await pilot.press("up")
            assert composer.value == "/help"
            await pilot.press("up")
            assert composer.value == "/status"

            composer.value = ""
            await pilot.press("x")

            await pilot.press("up")
            assert composer.value == "/help"

    asyncio.run(run())


def test_tui_palette_completes_parameter_template() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            await pilot.press("/")
            for ch in "ros pub":
                await pilot.press(ch)

            assert app._command_matches[0].name == "/ros pub"

            await pilot.press("tab")

            composer = app.query_one("#composer")
            assert composer.value == "/ros pub <topic> <payload>"
            assert composer.cursor_position == composer.value.index("<")

    asyncio.run(run())


def test_tui_palette_completes_bare_command_with_trailing_space() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            await pilot.press("/")
            await pilot.press("s")
            await pilot.press("tab")

            composer = app.query_one("#composer")
            assert composer.value == "/status "
            assert composer.cursor_position == len(composer.value)

    asyncio.run(run())
