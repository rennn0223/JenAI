from __future__ import annotations

import asyncio
from pathlib import Path

from jenai.config.store import build_minimal_config
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
from jenai.tui.panels import TimelineItem, pixel_mark
from jenai.tui.widgets import ApprovalCard


def test_tui_uses_colored_dachshund_mascot() -> None:
    mascot = pixel_mark()
    styles = {str(span.style) for span in mascot.spans}

    assert any("#d98c69" in style for style in styles)
    assert any("#5fb1c0" in style for style in styles)
    assert 4 <= mascot.plain.count("\n") <= 7


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


def test_tui_ros_topics_command(monkeypatch) -> None:
    async def fake_ros_topics(config):
        return RosTopicsOutput(topics=[TopicItem(name="/cmd_vel", kind_hint="control")])

    monkeypatch.setattr("jenai.tui.robot_commands.ros_topics", fake_ros_topics)

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

    monkeypatch.setattr("jenai.tui.robot_commands.ros_schema", fake_ros_schema)

    async def run() -> None:
        app = _app()
        async with app.run_test():
            await app.handle_user_text("/ros schema /cmd_vel")
            panels = [w for w in app.query_one("#events").children if hasattr(w, "title")]
            assert any("Schema" in p.title for p in panels)

    asyncio.run(run())


def test_tui_ros_topic_info_command(monkeypatch) -> None:
    from jenai.schemas import RosTopicInfoOutput

    async def fake_topic_info(config, topic):
        assert topic == "/cmd_vel"
        return RosTopicInfoOutput(
            name=topic, message_type="geometry_msgs/msg/Twist", publisher_count=1
        )

    monkeypatch.setattr("jenai.tui.robot_commands.ros_topic_info", fake_topic_info)

    async def run() -> None:
        app = _app()
        async with app.run_test():
            await app.handle_user_text("/ros topic-info /cmd_vel")
            panels = [w for w in app.query_one("#events").children if hasattr(w, "title")]
            assert any("Topic info" in p.title for p in panels)

    asyncio.run(run())


def test_tui_drive_natural_language_shows_card_and_executes(monkeypatch) -> None:
    from jenai.schemas import RosPubOutput

    executed = {}

    async def fake_drive(topic, message_type, payload, *, duration_s=1.0, **limits):
        executed.update(payload=payload, duration=duration_s)
        return RosPubOutput(
            topic=topic, message_type=message_type,
            execution_status="succeeded", result_message="drove then stopped",
        )

    monkeypatch.setattr("jenai.tui.app.ros_drive", fake_drive)

    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            await app.handle_user_text("/drive 前進兩秒")  # regex path, no LLM needed
            cards = list(app.query(ApprovalCard))
            assert len(cards) == 1
            assert "forward" in cards[0].approval.title

            await pilot.press("enter")
            await pilot.pause()
            assert executed["duration"] == 2.0
            assert executed["payload"]["linear"]["x"] > 0

    asyncio.run(run())


def test_tui_ros_drive_shows_card_and_executes_on_approve(monkeypatch) -> None:
    from jenai.schemas import RosPubOutput
    from jenai.tools.ros2_core import Ros2PubValidation

    executed = {}

    async def fake_validate(topic, payload):
        return Ros2PubValidation(ok=True, message_type="geometry_msgs/msg/Twist")

    async def fake_drive(topic, message_type, payload, *, duration_s=1.0, **limits):
        executed.update(topic=topic, duration=duration_s)
        return RosPubOutput(
            topic=topic, message_type=message_type,
            execution_status="succeeded", result_message="drove then stopped",
        )

    monkeypatch.setattr("jenai.tui.robot_commands.ros_pub_validate", fake_validate)
    monkeypatch.setattr("jenai.tui.app.ros_drive", fake_drive)

    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            await app.handle_user_text('/ros drive /cmd_vel {"linear": {"x": 0.2}} 2')
            cards = list(app.query(ApprovalCard))
            assert len(cards) == 1
            assert "for 2" in cards[0].approval.title

            await pilot.press("enter")
            await pilot.pause()
            assert executed == {"topic": "/cmd_vel", "duration": 2.0}
            assert list(app.query(ApprovalCard)) == []

    asyncio.run(run())


def test_tui_ros_echo_command(monkeypatch) -> None:
    from jenai.schemas import RosEchoOutput

    async def fake_echo(config, topic, *, limit=1):
        assert topic == "/chatter"
        return RosEchoOutput(topic=topic, messages=[{"raw": "data: hi"}], summary="1 message")

    monkeypatch.setattr("jenai.tui.robot_commands.ros_echo", fake_echo)

    async def run() -> None:
        app = _app()
        async with app.run_test():
            await app.handle_user_text("/ros echo /chatter")
            panels = [w for w in app.query_one("#events").children if hasattr(w, "title")]
            assert any("Echo" in p.title for p in panels)

    asyncio.run(run())


def test_tui_vision_command(monkeypatch) -> None:
    from jenai.schemas import VisionOutput

    async def fake_analyze(config, path, *, task_context=""):
        assert path == "/tmp/frame.png"
        return VisionOutput(source=path, summary="a robot", objects=["robot"])

    monkeypatch.setattr("jenai.tui.robot_commands.analyze_image", fake_analyze)

    async def run() -> None:
        app = _app()
        async with app.run_test():
            await app.handle_user_text("/vision image /tmp/frame.png")
            panels = [w for w in app.query_one("#events").children if hasattr(w, "title")]
            assert any("Vision" in p.title for p in panels)

    asyncio.run(run())


def _awaiting_run(app, tool_name: str, call_id: str = "c1"):
    from jenai.agent.context import JenAIRunContext
    from jenai.schemas import ApprovalRequest, EffectScope, RiskLevel

    run_rec = app.run_store.create_run(app.session.session_id, "drive forward")
    run_rec.status = "awaiting_approval"
    run_rec.interruptions.append(
        ApprovalRequest(
            run_id=run_rec.run_id,
            tool_call_id=call_id,
            tool_name=tool_name,
            title="Publish to /cmd_vel",
            summary="Send a Twist.",
            raw_action="ros2 topic pub ...",
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
            justification="requested",
        )
    )
    ctx = JenAIRunContext(
        config=app.config,
        config_path=app.config_path,
        session=app.session,
        run=run_rec,
        run_store=app.run_store,
    )
    return ctx, run_rec


def test_tui_run_approval_remember_auto_approves(monkeypatch) -> None:
    from jenai.schemas import RunRecord

    async def run() -> None:
        app = _app()
        async with app.run_test():
            captured: dict = {}

            async def fake_resume(agent, ctx, decisions, **kw):
                captured["decisions"] = dict(decisions)
                return RunRecord(
                    session_id=app.session.session_id,
                    user_input="x",
                    status="completed",
                    final_output="done",
                )

            monkeypatch.setattr(
                "jenai.tui.app.orchestrator.resume_with_approvals", fake_resume
            )
            ctx, run_rec = _awaiting_run(app, "ros_pub_execute_tool")
            await app._render_run_update(ctx, run_rec, agent=object())
            assert len(list(app.query(ApprovalCard))) == 1  # first time asks

            # Option 2 = approve + don't ask again.
            await app.on_approval_card_decision(
                ApprovalCard.Decision("c1", True, remember=True)
            )
            assert "ros_pub_execute_tool" in app._auto_approved
            assert captured["decisions"] == {"c1": True}

    asyncio.run(run())


def test_tui_run_remembered_tool_skips_card(monkeypatch) -> None:
    from jenai.schemas import RunRecord

    async def run() -> None:
        app = _app()
        async with app.run_test():
            app._auto_approved.add("ros_pub_execute_tool")
            captured: dict = {}

            async def fake_resume(agent, ctx, decisions, **kw):
                captured["decisions"] = dict(decisions)
                return RunRecord(
                    session_id=app.session.session_id, user_input="x", status="completed"
                )

            monkeypatch.setattr(
                "jenai.tui.app.orchestrator.resume_with_approvals", fake_resume
            )
            ctx, run_rec = _awaiting_run(app, "ros_pub_execute_tool")
            await app._render_run_update(ctx, run_rec, agent=object())

            # No card shown — the remembered tool auto-approves and resumes.
            assert list(app.query(ApprovalCard)) == []
            assert captured["decisions"] == {"c1": True}

    asyncio.run(run())


def test_tui_mission_shows_card_and_runs(monkeypatch) -> None:
    from jenai.tools.mission_core import MissionReport, StepResult

    ran = {}

    async def fake_run_mission(config, locations, steps, *, on_step=None, navigate=None):
        ran["steps"] = len(steps)
        result = StepResult("goto", "kitchen", "succeeded", "arrived")
        if on_step:
            await on_step(result)
        return MissionReport([result])

    monkeypatch.setattr("jenai.tui.app.run_mission", fake_run_mission)

    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            await app.handle_user_text("/mission kitchen, lobby")
            cards = list(app.query(ApprovalCard))
            assert len(cards) == 1
            assert "2 steps" in cards[0].approval.title

            await pilot.press("enter")
            await pilot.pause()
            # Approved actions now run as the cancellable active task.
            if app._active_task is not None:
                await app._active_task
            assert ran["steps"] == 2
            assert list(app.query(ApprovalCard)) == []

    asyncio.run(run())


def test_tui_bang_prefix_enters_shell_mode(monkeypatch) -> None:
    from jenai.schemas import ShellOutput

    async def fake_run_shell(command, *, cwd=None, timeout=30.0):
        return ShellOutput(command=command, exit_code=0)

    monkeypatch.setattr("jenai.tui.app.run_shell", fake_run_shell)

    async def run() -> None:
        app = _app()
        async with app.run_test():
            await app.handle_user_text("!ls -la")
            cards = list(app.query(ApprovalCard))
            assert len(cards) == 1
            assert cards[0].approval.raw_action == "ls -la"

    asyncio.run(run())


def test_tui_shell_remember_auto_approves_subsequent(monkeypatch) -> None:
    from jenai.schemas import ShellOutput

    executed: list[str] = []

    async def fake_run_shell(command, *, cwd=None, timeout=30.0):
        executed.append(command)
        return ShellOutput(command=command, exit_code=0, stdout_summary="ok")

    monkeypatch.setattr("jenai.tui.app.run_shell", fake_run_shell)

    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            await app.handle_user_text("/shell echo one")
            assert len(list(app.query(ApprovalCard))) == 1
            # Option 2 = "Yes, and don't ask again this session"
            await pilot.press("2")
            await pilot.pause()
            assert "shell" in app._auto_approved
            assert executed == ["echo one"]

            # A second /shell must run without showing a card.
            await app.handle_user_text("/shell echo two")
            await pilot.pause()
            assert list(app.query(ApprovalCard)) == []
            assert executed == ["echo one", "echo two"]

    asyncio.run(run())


def test_tui_status_line_shows_provider_and_model() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test():
            line = app._status_line()
            assert "openai" in line
            assert "gpt-test" in line
            assert app.query_one("#statusbar")

    asyncio.run(run())


def test_tui_escape_interrupts_active_task(monkeypatch) -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:

            async def blocking(_value: str) -> None:
                await asyncio.sleep(10)

            app.handle_user_text = blocking  # type: ignore[method-assign]
            app._active_task = asyncio.create_task(app._run_user_text("/run x"))
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            await pilot.pause()

            bodies = [getattr(w, "body", "") for w in app.query_one("#events").children]
            assert "Interrupted." in bodies
            assert app._active_task is None

    asyncio.run(run())


def test_tui_shell_shows_card_and_executes_on_approve(monkeypatch) -> None:
    from jenai.schemas import ShellOutput

    executed = []

    async def fake_run_shell(command, *, cwd=None, timeout=30.0):
        executed.append(command)
        return ShellOutput(command=command, exit_code=0, stdout_summary="ok")

    monkeypatch.setattr("jenai.tui.app.run_shell", fake_run_shell)

    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            await app.handle_user_text("/shell echo hi")
            cards = list(app.query(ApprovalCard))
            assert len(cards) == 1
            assert cards[0].approval.effect_scope == "host_command"

            await pilot.press("enter")
            await pilot.pause()
            assert executed == ["echo hi"]

    asyncio.run(run())


def test_tui_shell_rejects_on_escape(monkeypatch) -> None:
    from jenai.schemas import ShellOutput

    executed = []

    async def fake_run_shell(command, *, cwd=None, timeout=30.0):
        executed.append(command)
        return ShellOutput(command=command, exit_code=0)

    monkeypatch.setattr("jenai.tui.app.run_shell", fake_run_shell)

    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            await app.handle_user_text("/shell rm -rf /tmp/x")
            assert len(list(app.query(ApprovalCard))) == 1
            await pilot.press("escape")
            await pilot.pause()
            assert executed == []

    asyncio.run(run())


def test_tui_ros_pub_shows_card_and_resolves_on_approve(monkeypatch) -> None:
    async def fake_validate(topic, payload):
        return Ros2PubValidation(
            ok=True, message_type="geometry_msgs/msg/Twist", payload_preview=payload
        )

    async def fake_execute(topic, message_type, payload, **limits):
        return RosPubOutput(
            topic=topic,
            message_type=message_type,
            execution_status="succeeded",
            result_message="published",
        )

    monkeypatch.setattr("jenai.tui.robot_commands.ros_pub_validate", fake_validate)
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

    async def fake_execute(topic, message_type, payload, **limits):
        executed.append(topic)
        return RosPubOutput(topic=topic, message_type=message_type)

    monkeypatch.setattr("jenai.tui.robot_commands.ros_pub_validate", fake_validate)
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
            input_text="", outgoing_action=outgoing_action, execution_status="unavailable",
            route_preview="No navigation backend — the goal was NOT sent.",
        )

    monkeypatch.setattr("jenai.tui.robot_commands.route_preview", fake_route_preview)
    # Execution dispatch lives in the shared navigate_with_fallback, which
    # resolves route_execute from route_core at call time.
    monkeypatch.setattr("jenai.tools.route_core.route_execute", fake_route_execute)

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


class FakeHaltBridge:
    """Shared in-process bridge fake for the /stop tests (halt only)."""

    def __init__(self, nav_canceled: bool = False) -> None:
        self.nav_canceled = nav_canceled
        self.halts: list[str] = []

    async def halt(self, cmd_vel_topic="/cmd_vel", stamped=False) -> bool:
        self.halts.append(cmd_vel_topic)
        return self.nav_canceled


def test_tui_stop_command_halts_robot(monkeypatch, tmp_path) -> None:
    fake = FakeHaltBridge(nav_canceled=True)

    async def fake_get_bridge():
        return fake

    async def run() -> None:
        app = _app(tmp_path)
        monkeypatch.setattr(app, "_get_bridge", fake_get_bridge)
        async with app.run_test():
            await app.handle_user_text("/stop")
            bodies = [i.body for i in app.query(TimelineItem)]
            assert any("halted" in b.lower() for b in bodies)

    asyncio.run(run())

    assert fake.halts == ["/cmd_vel"]


def test_tui_stop_preempts_running_task(monkeypatch, tmp_path) -> None:
    """/stop must never queue behind the task it is stopping: the busy gate
    lets it through, cancelling the in-flight task first."""
    from types import SimpleNamespace

    async def fake_get_bridge():
        return FakeHaltBridge()

    async def run() -> None:
        app = _app(tmp_path)
        monkeypatch.setattr(app, "_get_bridge", fake_get_bridge)
        async with app.run_test() as pilot:
            hang_started = asyncio.Event()

            async def hang() -> None:
                hang_started.set()
                await asyncio.sleep(30)

            hang_task = asyncio.create_task(hang())
            app._active_task = hang_task
            await hang_started.wait()

            event = SimpleNamespace(value="/stop", input=SimpleNamespace(value=""))
            await app.on_input_submitted(event)
            stop_task = app._active_task
            assert stop_task is not hang_task  # /stop replaced the hung task
            await stop_task
            await pilot.pause()

            assert hang_task.cancelled()
            bodies = [i.body for i in app.query(TimelineItem)]
            assert any("halted" in b.lower() for b in bodies)

    asyncio.run(run())


def test_tui_patrol_shows_card_and_runs(monkeypatch) -> None:
    from jenai.tools.skills import PatrolReport, PatrolStepResult

    ran = {}

    async def fake_run_patrol(config, locations, spec, *, navigate, on_step=None, observe=None):
        ran["spec"] = spec
        ran["observe"] = observe
        result = PatrolStepResult(1, "A", "succeeded", "arrived")
        if on_step:
            await on_step(result)
        return PatrolReport(spec, [result])

    monkeypatch.setattr("jenai.tui.app.run_patrol", fake_run_patrol)

    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            await app.handle_user_text("/patrol A, B x2 photo")
            cards = list(app.query(ApprovalCard))
            assert len(cards) == 1
            assert "4 waypoints" in cards[0].approval.title  # 2 points × 2 loops

            await pilot.press("enter")
            await pilot.pause()
            if app._active_task is not None:
                await app._active_task
            assert ran["spec"].points == ["A", "B"]
            assert ran["spec"].loops == 2
            assert ran["observe"] is not None  # photo flag wired the camera in

    asyncio.run(run())


def test_tui_dock_without_dock_location_warns(tmp_path) -> None:
    async def run() -> None:
        app = _app(tmp_path)  # locations file exists but holds no dock
        async with app.run_test():
            await app.handle_user_text("/dock")
            bodies = [i.body for i in app.query(TimelineItem)]
            assert any("No dock location" in b for b in bodies)
            assert list(app.query(ApprovalCard)) == []  # nothing to approve

    asyncio.run(run())


def test_tui_dock_routes_to_tagged_location(monkeypatch, tmp_path) -> None:
    from jenai.schemas import Location, Pose2D, RouteOutput

    sent = {}

    async def fake_execute_route(action):
        sent["goal"] = action["goal"]["name"]
        return RouteOutput(
            input_text="", outgoing_action=action,
            execution_status="succeeded", route_preview="arrived at dock",
        )

    async def run() -> None:
        app = _app(tmp_path)
        monkeypatch.setattr(
            app,
            "_load_locations",
            lambda: [
                Location(
                    name="Charger", tags=["dock"],
                    frame_id="map", pose=Pose2D(x=9, y=9, yaw=0),
                )
            ],
        )
        monkeypatch.setattr(app, "_execute_route_action", fake_execute_route)
        async with app.run_test() as pilot:
            await app.handle_user_text("/dock")
            cards = list(app.query(ApprovalCard))
            assert len(cards) == 1
            assert "Charger" in cards[0].approval.title

            await pilot.press("enter")
            await pilot.pause()
            if app._active_task is not None:
                await app._active_task
            assert sent["goal"] == "Charger"

    asyncio.run(run())


def test_tui_stop_variants_preempt_and_busy_gives_feedback(monkeypatch, tmp_path) -> None:
    """'/STOP' must preempt like '/stop'; other input while busy must not be
    silently swallowed (review findings)."""
    from types import SimpleNamespace

    async def fake_get_bridge():
        return FakeHaltBridge()

    async def run() -> None:
        app = _app(tmp_path)
        monkeypatch.setattr(app, "_get_bridge", fake_get_bridge)
        async with app.run_test() as pilot:
            hang_started = asyncio.Event()

            async def hang() -> None:
                hang_started.set()
                await asyncio.sleep(30)

            hang_task = asyncio.create_task(hang())
            app._active_task = hang_task
            await hang_started.wait()

            # Non-stop input while busy: visible feedback, task untouched.
            await app.on_input_submitted(
                SimpleNamespace(value="/status", input=SimpleNamespace(value=""))
            )
            await pilot.pause()
            bodies = [i.body for i in app.query(TimelineItem)]
            assert any("Busy" in b for b in bodies)
            assert not hang_task.cancelled()

            # A shouty stop variant still preempts.
            await app.on_input_submitted(
                SimpleNamespace(value="/STOP", input=SimpleNamespace(value=""))
            )
            stop_task = app._active_task
            assert stop_task is not hang_task
            await stop_task
            await pilot.pause()
            assert hang_task.cancelled()
            bodies = [i.body for i in app.query(TimelineItem)]
            assert any("halted" in b.lower() for b in bodies)

    asyncio.run(run())


def test_tui_escape_does_not_cancel_the_stop_task(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace

    release = None

    class SlowHaltBridge:
        async def halt(self, cmd_vel_topic="/cmd_vel", stamped=False) -> bool:
            await release.wait()
            return False

    async def fake_get_bridge():
        return SlowHaltBridge()

    async def run() -> None:
        nonlocal release
        release = asyncio.Event()
        app = _app(tmp_path)
        monkeypatch.setattr(app, "_get_bridge", fake_get_bridge)
        async with app.run_test() as pilot:
            await app.on_input_submitted(
                SimpleNamespace(value="/stop", input=SimpleNamespace(value=""))
            )
            stop_task = app._active_task
            assert app._active_task_is_stop

            await pilot.press("escape")  # the reflex must NOT abort the halt
            assert not stop_task.cancelled()

            release.set()
            await stop_task
            bodies = [i.body for i in app.query(TimelineItem)]
            assert any("halted" in b.lower() for b in bodies)

    asyncio.run(run())


def test_tui_dock_and_route_approval_memory_are_isolated(monkeypatch, tmp_path) -> None:
    """Remembering /route must not auto-approve /dock (review finding)."""
    from jenai.schemas import Location, Pose2D

    async def run() -> None:
        app = _app(tmp_path)
        monkeypatch.setattr(
            app,
            "_load_locations",
            lambda: [
                Location(
                    name="Charger", tags=["dock"],
                    frame_id="map", pose=Pose2D(x=9, y=9, yaw=0),
                )
            ],
        )
        async with app.run_test():
            app._auto_approved.add("route")  # user remembered an ordinary /route
            await app.handle_user_text("/dock")
            # /dock must still raise its own card, not silently execute.
            assert len(list(app.query(ApprovalCard))) == 1

    asyncio.run(run())


def test_tui_perception_start_and_stop(monkeypatch, tmp_path) -> None:
    from jenai.schemas import SceneAnalysis

    class FakeBridge:
        async def capture_frame(self, topic, timeout=5.0):
            raise AssertionError("loop is faked; capture must not run")

    async def fake_get_bridge():
        return FakeBridge()

    started = {}

    class FakePerceptionLoop:
        def __init__(self, config, bridge, *, topic=None, hz=1.0, on_analysis=None, on_status=None):
            self.topic = topic or config.vehicle.camera_topic
            self.frames = 0
            self.latest = None
            self._running = False
            self._on_analysis = on_analysis
            started["topic"] = self.topic
            started["hz"] = hz

        @property
        def running(self):
            return self._running

        async def start(self):
            self._running = True
            # Deliver one analysis so the rendering path is exercised.
            self.latest = SceneAnalysis(
                scene_context="hallway [with brackets]",
                affordances=["path_clear"],
                suggested_action="proceed",
                confidence=0.9,
            )
            self.frames = 1
            if self._on_analysis:
                await self._on_analysis(self.latest)

        async def stop(self):
            self._running = False

    monkeypatch.setattr("jenai.tui.robot_commands.PerceptionLoop", FakePerceptionLoop)

    async def run() -> None:
        app = _app(tmp_path)
        monkeypatch.setattr(app, "_get_bridge", fake_get_bridge)
        async with app.run_test():
            await app.handle_user_text("/perception start /rgb 2")
            bodies = [i.body for i in app.query(TimelineItem)]
            assert any("Perception loop started" in b for b in bodies)
            assert any("hallway" in b for b in bodies)  # analysis rendered
            assert any("suggestion only" in b for b in bodies)  # approval note
            assert started == {"topic": "/rgb", "hz": 2.0}

            await app.handle_user_text("/perception stop")
            bodies = [i.body for i in app.query(TimelineItem)]
            assert any("Perception loop stopped" in b for b in bodies)

    asyncio.run(run())


def test_tui_report_command_no_logs_then_latest(monkeypatch, tmp_path: Path) -> None:
    """/report warns when no patrol ran yet; with a log it renders the
    deterministic body and degrades honestly when the LLM digest fails."""
    from datetime import datetime

    from jenai.state.reports import save_patrol_log
    from jenai.tools.skills import PatrolReport, PatrolSpec, PatrolStepResult

    async def run() -> None:
        app = _app(tmp_path)
        async with app.run_test():
            await app.handle_user_text("/report")
            texts = [str(getattr(c, "body", "")) for c in app.query_one("#events").children]
            assert any("No patrol logs yet" in t for t in texts)

            report = PatrolReport(spec=PatrolSpec(points=["A"]))
            report.results = [PatrolStepResult(1, "A", "succeeded", "reached")]
            save_patrol_log(report, app.config_path, now=datetime(2026, 7, 4, 12, 0))

            async def no_digest(config, log):
                return None

            import jenai.tui.robot_commands  # noqa: F401 — target module below

            monkeypatch.setattr("jenai.state.reports.summarize_patrol", no_digest)
            await app.handle_user_text("/report")
            texts = [
                str(getattr(c, "body", "")) + str(getattr(c, "renderable", ""))
                for c in app.query_one("#events").children
            ]
            assert any("1/1 waypoints" in t for t in texts)
            assert any("LLM digest unavailable" in t for t in texts)

    asyncio.run(run())


def test_tui_route_from_a_to_b_runs_ordered_two_stop(monkeypatch, tmp_path) -> None:
    """`/route 從 A 到 B` with both ends known → mission [goto A, goto B],
    visiting A then B in order."""
    from jenai.tools.mission_core import MissionReport, StepResult

    ran = {}

    async def fake_run_mission(config, locations, steps, *, on_step=None, navigate=None):
        ran["steps"] = [(s.kind, s.target) for s in steps]
        return MissionReport([StepResult("goto", s.target, "succeeded", "ok") for s in steps])

    monkeypatch.setattr("jenai.tui.app.run_mission", fake_run_mission)

    # Two known locations so both start and goal resolve.
    locs = (tmp_path / "locations.toml")
    locs.write_text(
        '[[locations]]\nname = "應科大樓"\n[locations.pose]\nx=0.0\ny=0.0\nyaw=0.0\n\n'
        '[[locations]]\nname = "機械系館"\n[locations.pose]\nx=88.413\ny=-184.273\nyaw=0.0\n',
        encoding="utf-8",
    )

    async def run() -> None:
        app = _app(tmp_path)
        app.config.locations_path = "locations.toml"
        async with app.run_test() as pilot:
            await app.handle_user_text("/route 從應科大樓到機械系館")
            cards = list(app.query(ApprovalCard))
            assert len(cards) == 1
            assert "應科大樓 → 機械系館" in cards[0].approval.title
            await pilot.press("enter")
            await pilot.pause()
            if app._active_task is not None:
                await app._active_task
    asyncio.run(run())

    assert ran["steps"] == [("goto", "應科大樓"), ("goto", "機械系館")]


def test_mascot_frames_animate_but_keep_size() -> None:
    """Tail wag / blink / gallop produce different frames, and every pose has
    the same bounding box so the welcome panel never jitters."""
    idle_a, idle_b = pixel_mark(0), pixel_mark(1)
    blink = pixel_mark(6)
    run_a, run_b = pixel_mark(0, running=True), pixel_mark(1, running=True)

    assert idle_a.plain != "" and idle_a.spans
    assert str(idle_a) != str(idle_b) or idle_a.spans != idle_b.spans  # tail moved
    assert blink.spans != idle_a.spans  # eye closed
    assert run_a.spans != run_b.spans  # gallop alternates
    sizes = {(t.plain.count("\n"), max(len(line) for line in t.plain.split("\n")))
             for t in (idle_a, idle_b, blink, run_a, run_b)}
    assert len(sizes) == 1  # identical bounding box across all poses


def test_mascot_animation_tick_updates_widget() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            app.query_one("#pixel-mark")  # mascot mounted
            app._animate_mascot()
            app._animate_mascot()
            await pilot.pause()
            assert app._mascot_frame >= 2  # ticks advanced without crashing

    asyncio.run(run())


def test_mode_cycle_shift_tab_and_status_chip() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test():
            assert app._mode == "approve"  # safe default
            app.action_cycle_mode()
            assert app._mode == "plan"
            app.action_cycle_mode()
            assert app._mode == "auto"
            app.action_cycle_mode()
            assert app._mode == "approve"  # full cycle
            app._mode = "auto"
            assert "auto" in app._status_line()

    asyncio.run(run())


def test_plain_language_routes_by_mode(monkeypatch) -> None:
    """The point of the modes: a bare sentence plans or acts — it no longer
    just chats back 'here is the command you could type'."""
    calls = []

    async def fake_plan(self, arg):
        calls.append(("plan", arg))

    async def fake_run(self, arg):
        calls.append(("run", arg))

    monkeypatch.setattr(JenAITuiApp, "_show_plan", fake_plan)
    monkeypatch.setattr(JenAITuiApp, "_show_run", fake_run)

    async def run() -> None:
        app = _app()
        async with app.run_test():
            await app.handle_user_text("帶我去機械系館")  # approve → agent
            app._mode = "plan"
            await app.handle_user_text("帶我去機械系館")  # plan → planner

    asyncio.run(run())
    assert calls == [("run", "帶我去機械系館"), ("plan", "帶我去機械系館")]


def test_auto_mode_skips_approval_card_but_logs(monkeypatch) -> None:
    from jenai.tools.mission_core import MissionReport, StepResult

    ran = {}

    async def fake_run_mission(config, locations, steps, *, on_step=None, navigate=None):
        ran["steps"] = len(steps)
        return MissionReport([StepResult("goto", "kitchen", "succeeded", "ok")])

    monkeypatch.setattr("jenai.tui.app.run_mission", fake_run_mission)

    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            app._mode = "auto"
            await app.handle_user_text("/mission kitchen, lobby")
            assert list(app.query(ApprovalCard)) == []  # no card in auto mode
            await pilot.pause()
            if app._active_task is not None:
                await app._active_task
            texts = [str(getattr(c, "body", "")) for c in app.query_one("#events").children]
            assert any("自動模式:已批准" in t for t in texts)  # consent is logged

    asyncio.run(run())
    assert ran["steps"] == 2


def test_plan_mode_provider_error_shows_message_not_crash(monkeypatch) -> None:
    """Regression: plain language in plan mode routes to run_plan, which has no
    exception net; a provider/model failure must surface as a clean error, not
    an unhandled task exception (the /plan slash and old chat both caught this)."""

    async def boom_plan(self, arg):
        raise RuntimeError("provider is down")

    monkeypatch.setattr(JenAITuiApp, "_show_plan", boom_plan)

    async def run() -> None:
        app = _app()
        async with app.run_test():
            app._mode = "plan"
            await app.handle_user_text("帶我去機械系館")  # must not raise
            texts = [str(getattr(c, "body", "")) for c in app.query_one("#events").children]
            assert any("provider is down" in t for t in texts)

    asyncio.run(run())


def test_shift_tab_cycles_mode_while_composer_focused() -> None:
    """Regression: without priority=True the Screen's shift+tab (focus_previous)
    and the composer Input's ctrl+c/ctrl+d bindings win the dispatch order, so
    the App-level cycle_mode/quit actions never fire."""

    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            assert app._mode == "approve"
            await pilot.press("shift+tab")
            assert app._mode == "plan"
            await pilot.press("shift+tab")
            assert app._mode == "auto"
            await pilot.press("shift+tab")
            assert app._mode == "approve"

    asyncio.run(run())


def test_ctrl_c_quits_while_composer_focused() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test() as pilot:
            await pilot.press("ctrl+c")
        # exit() stamps return_code; a plain test-harness shutdown leaves it None.
        assert app.return_code is not None

    asyncio.run(run())
