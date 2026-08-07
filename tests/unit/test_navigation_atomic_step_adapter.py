from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jenai.adapters.locations import save_locations
from jenai.config.models import AppConfig, SiteProfile, VehicleProfile
from jenai.runtime import (
    AuthorityContext,
    CapabilityExecutionReport,
    EvidenceTimestampStatus,
    ExecutionContext,
    ExecutionDisposition,
    ExecutorEvidence,
    ExecutorStopResult,
    PreparedCapabilityStep,
    SourceAssurance,
    StopContext,
    StopTrigger,
    TransportSecurity,
    TypedCapabilityStep,
)
from jenai.schemas import Location, NavigationAttemptEvidence, Pose2D, RouteOutput
from jenai.schemas.models import TaskOutcome
from jenai.site_assets import bind_patrol_mission, fingerprint_locations_file
from jenai.tools.navigation_atomic_step_adapter import (
    EffectfulMissionBlockedError,
    NavigationAtomicStepAdapter,
    _capability_report,
    build_navigation_capability_executor,
)
from jenai.tools.safety import HaltReceipt, NavigationCancelStatus
from jenai.workflows.execution_engine import (
    CancellationView,
    DispatchContext,
    ExecutionEngine,
    StepDisposition,
)
from jenai.workflows.patrol_mission import (
    BoundLocation,
    MissionDraft,
    NavigateStep,
    PatrolMissionPolicy,
    PatrolMissionSpec,
    build_navigation_mission_spec,
    compile_patrol_mission,
    compile_single_navigation,
)


class _NoEvents:
    async def publish(self, _event) -> None:
        return None


class _RecordingExecutor:
    def __init__(
        self,
        report: CapabilityExecutionReport,
        *,
        stop_results: list[ExecutorStopResult] | None = None,
    ) -> None:
        self.report = report
        self.stop_results = list(stop_results or [])
        self.prepared_steps: list[TypedCapabilityStep] = []
        self.execution_contexts: list[ExecutionContext] = []

    async def snapshot(self, _request, _context):
        raise AssertionError("snapshot is not part of atomic execution")

    async def prepare(
        self,
        step: TypedCapabilityStep,
        context: ExecutionContext,
    ) -> PreparedCapabilityStep:
        self.prepared_steps.append(step)
        self.execution_contexts.append(context)
        return PreparedCapabilityStep(
            step=step,
            context=context,
            binding_sha256="a" * 64,
        )

    async def execute(self, prepared, context, _events, *, is_cancelled=None):
        assert prepared.context == context
        return self.report

    async def stop(self, _context, _events) -> ExecutorStopResult:
        if not self.stop_results:
            raise AssertionError("no scripted stop result remains")
        return self.stop_results.pop(0)


def _mission() -> PatrolMissionSpec:
    return PatrolMissionSpec(
        mission_id="mission-1",
        site_id="site-1",
        site_version="1",
        site_profile_digest="1" * 64,
        robot_id="robot-1",
        vehicle_profile_digest="2" * 64,
        locations_sha256="3" * 64,
        ordered_locations=(
            BoundLocation(location_id="a", location_name="A"),
            BoundLocation(location_id="b", location_name="B"),
            BoundLocation(location_id="c", location_name="C"),
        ),
        home_location=BoundLocation(location_id="dock", location_name="Dock"),
        policy=PatrolMissionPolicy(),
    )


def _evidence(kind: str, payload: dict[str, object]) -> ExecutorEvidence:
    return ExecutorEvidence(
        kind=kind,
        source="navigation_gateway",
        source_timestamp_status=EvidenceTimestampStatus.UNAVAILABLE,
        transport_security=TransportSecurity.UNKNOWN,
        source_assurance=SourceAssurance.RUNTIME_OBSERVED,
        payload_schema_version="1",
        payload=payload,
    )


def _dispatch() -> DispatchContext:
    return DispatchContext(
        dispatch_id="dispatch-1",
        step_index=0,
        attempt=1,
        cancellation=CancellationView(
            is_cancelled=lambda: False,
            wait_cancelled=lambda: asyncio.sleep(0, result=False),
        ),
    )


def _production_engine(
    tmp_path: Path,
    gateway,
    *,
    single_target: bool = False,
) -> ExecutionEngine:
    locations = [
        Location(id="a", name="A", pose=Pose2D(x=1.0, y=0.0, yaw=0.0)),
        Location(id="b", name="B", pose=Pose2D(x=2.0, y=0.0, yaw=0.0)),
        Location(id="c", name="C", pose=Pose2D(x=3.0, y=0.0, yaw=0.0)),
        Location(id="dock", name="Dock", pose=Pose2D(x=0.0, y=0.0, yaw=0.0)),
    ]
    locations_path = tmp_path / "locations.toml"
    save_locations(locations, locations_path)
    config_path = tmp_path / "config.toml"
    config = AppConfig(
        locations_path="locations.toml",
        route_adapter="nav2",
        vehicle=VehicleProfile(robot_id="robot-1"),
        site=SiteProfile(
            site_id="site-1",
            display_name="Site 1",
            version="1",
            active=True,
            validated=True,
            map_sha256="f" * 64,
            locations_sha256=fingerprint_locations_file(locations_path),
            locations_path="locations.toml",
            validated_routes=["A", "B", "C", "Dock"],
            default_patrol=["A", "B", "C"],
            home_location="Dock",
            dock_location="Dock",
        ),
    )
    if single_target:
        mission = build_navigation_mission_spec(
            mission_id="mission-1",
            site=config.site,
            vehicle=config.vehicle,
            locations_sha256=config.site.locations_sha256 or "",
            target_location=BoundLocation(location_id="a", location_name="A"),
        )
        plan = compile_single_navigation(mission)
    else:
        mission = bind_patrol_mission(
            config,
            config_path,
            MissionDraft(),
            mission_id="mission-1",
        )
        plan = compile_patrol_mission(mission)
    adapter = NavigationAtomicStepAdapter(
        executor=build_navigation_capability_executor(
            config=config,
            config_path=config_path,
            gateway=gateway,
        ),
        authority=AuthorityContext(
            runtime_id="golden-path",
            boot_id="boot-1",
            authority_generation=1,
            safety_epoch=1,
        ),
        mission=mission,
        fencing_token=1,
        events=_NoEvents(),
    )
    return ExecutionEngine(plan, adapter)


def test_navigation_atomic_step_uses_capability_executor_and_returns_typed_completion() -> None:

    async def run() -> None:
        executor = _RecordingExecutor(
            CapabilityExecutionReport(
                disposition="completed",
                summary="Arrived at A",
                evidence=(
                    _evidence(
                        "navigation_terminal",
                        {"evidence_id": "terminal:goal-1", "status": "succeeded"},
                    ),
                    _evidence(
                        "endpoint_pose",
                        {"evidence_id": "endpoint:goal-1", "position_error_m": 0.04},
                    ),
                ),
            )
        )
        adapter = NavigationAtomicStepAdapter(
            executor=executor,
            authority=AuthorityContext(
                runtime_id="golden-path",
                boot_id="boot-1",
                authority_generation=1,
                safety_epoch=1,
            ),
            mission=_mission(),
            fencing_token=1,
            events=_NoEvents(),
        )
        step = NavigateStep(
            location_id="a",
            location_name="A",
            position_tolerance_m=0.15,
        )

        result = await adapter.execute(step, _dispatch())

        assert result.disposition is StepDisposition.SUCCEEDED
        assert result.position_error_m == 0.04
        assert [item.evidence_id for item in result.evidence] == [
            "terminal:goal-1",
            "endpoint:goal-1",
        ]
        assert executor.prepared_steps[0].model_dump(mode="json") == {
            "capability_id": "navigate",
            "input_schema_version": "1",
            "input": {
                "kind": "navigate",
                "location_id": "a",
                "location_name": "A",
                "position_tolerance_m": 0.15,
                "require_yaw": False,
                "site_id": "site-1",
                "site_version": "1",
                "site_profile_digest": "1" * 64,
                "vehicle_profile_digest": "2" * 64,
                "locations_sha256": "3" * 64,
            },
        }
        assert executor.execution_contexts[0].command_id == "dispatch-1"
        assert executor.execution_contexts[0].task_id == "mission-1"

    asyncio.run(run())


def test_production_registration_resolves_bound_location_and_calls_navigation_gateway(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        locations = [
            Location(id="a", name="A", pose=Pose2D(x=1.0, y=0.0, yaw=0.0)),
            Location(id="b", name="B", pose=Pose2D(x=2.0, y=0.0, yaw=0.0)),
            Location(id="c", name="C", pose=Pose2D(x=3.0, y=0.0, yaw=0.0)),
            Location(id="dock", name="Dock", pose=Pose2D(x=0.0, y=0.0, yaw=0.0)),
        ]
        locations_path = tmp_path / "locations.toml"
        save_locations(locations, locations_path)
        config_path = tmp_path / "config.toml"
        config = AppConfig(
            locations_path="locations.toml",
            route_adapter="nav2",
            vehicle=VehicleProfile(robot_id="robot-1"),
            site=SiteProfile(
                site_id="site-1",
                display_name="Site 1",
                version="1",
                active=True,
                validated=True,
                map_sha256="f" * 64,
                locations_sha256=fingerprint_locations_file(locations_path),
                locations_path="locations.toml",
                validated_routes=["A", "B", "C", "Dock"],
                default_patrol=["A", "B", "C"],
                home_location="Dock",
                dock_location="Dock",
            ),
        )
        mission = bind_patrol_mission(
            config,
            config_path,
            MissionDraft(),
            mission_id="mission-1",
        )
        plan = compile_patrol_mission(mission)

        class Gateway:
            def __init__(self) -> None:
                self.kwargs: list[dict[str, object]] = []
                self.actions: list[dict[str, object]] = []

            async def execute(self, action, **_kwargs) -> RouteOutput:
                self.actions.append(action)
                self.kwargs.append(_kwargs)
                return RouteOutput(
                    input_text="",
                    outgoing_action=action,
                    approval_status="approved",
                    execution_status="succeeded",
                    route_preview="Arrived at A",
                    navigation_attempts=[
                        NavigationAttemptEvidence(
                            attempt=1,
                            tag="goal-1",
                            execution_status="succeeded",
                            detail="Arrived at A",
                            terminal_status="succeeded",
                            terminal_observed=True,
                            endpoint_pose_observed=True,
                            position_error_m=0.04,
                        )
                    ],
                )

            async def stop(self):
                raise AssertionError("stop was not requested")

        gateway = Gateway()
        executor = build_navigation_capability_executor(
            config=config,
            config_path=config_path,
            gateway=gateway,
        )
        adapter = NavigationAtomicStepAdapter(
            executor=executor,
            authority=AuthorityContext(
                runtime_id="golden-path",
                boot_id="boot-1",
                authority_generation=1,
                safety_epoch=1,
            ),
            mission=mission,
            fencing_token=1,
            events=_NoEvents(),
        )

        result = await adapter.execute(plan.steps[0], _dispatch())

        assert result.disposition is StepDisposition.SUCCEEDED
        assert result.position_error_m == 0.04
        assert gateway.actions == [
            {
                "capability_id": "navigate",
                "goal": locations[0].model_dump(mode="json"),
            }
        ]
        assert len(gateway.kwargs) == 1
        cancellation = gateway.kwargs[0].pop("is_cancelled")
        assert callable(cancellation)
        assert cancellation() is False
        assert gateway.kwargs == [
            {
                "run_id": "mission-1",
                "session_id": "boot-1",
                "endpoint_retry_limit": 0,
            }
        ]

    asyncio.run(run())


def test_stop_blocks_new_effects_until_nav2_is_reconfirmed_idle() -> None:
    async def run() -> None:
        executor = _RecordingExecutor(
            CapabilityExecutionReport(
                disposition="completed",
                summary="Arrived at A",
                evidence=(
                    _evidence(
                        "navigation_terminal",
                        {"evidence_id": "terminal:goal-1", "status": "succeeded"},
                    ),
                    _evidence(
                        "endpoint_pose",
                        {"evidence_id": "endpoint:goal-1", "position_error_m": 0.04},
                    ),
                ),
            ),
            stop_results=[
                ExecutorStopResult(
                    request_accepted=True,
                    cancel_requested=True,
                    cancel_acknowledged=False,
                    zero_velocity_command_published=True,
                ),
                ExecutorStopResult(
                    request_accepted=True,
                    cancel_requested=False,
                    cancel_acknowledged=None,
                    zero_velocity_command_published=True,
                ),
            ],
        )
        adapter = NavigationAtomicStepAdapter(
            executor=executor,
            authority=AuthorityContext(
                runtime_id="golden-path",
                boot_id="boot-1",
                authority_generation=1,
                safety_epoch=1,
            ),
            mission=_mission(),
            fencing_token=1,
            events=_NoEvents(),
        )
        step = NavigateStep(
            location_id="a",
            location_name="A",
            position_tolerance_m=0.15,
        )

        stopped = await adapter.stop(_dispatch())

        assert stopped.cancel_acknowledged is False
        with pytest.raises(EffectfulMissionBlockedError):
            await adapter.execute(step, _dispatch())
        assert executor.prepared_steps == []

        assert await adapter.reconfirm_robot_state() is True
        result = await adapter.execute(step, _dispatch())
        assert result.disposition is StepDisposition.SUCCEEDED

    asyncio.run(run())


def test_production_executor_maps_gateway_halt_receipt_to_typed_stop_result(
    tmp_path: Path,
) -> None:
    class Gateway:
        async def execute(self, _action, **_kwargs):
            raise AssertionError("navigation was not requested")

        async def stop(self) -> HaltReceipt:
            return HaltReceipt(
                navigation_cancel_status=NavigationCancelStatus.UNCONFIRMED,
                zero_velocity_delivered=True,
                message="Cancellation was not acknowledged.",
            )

    async def run() -> None:
        executor = build_navigation_capability_executor(
            config=AppConfig(),
            config_path=tmp_path / "config.toml",
            gateway=Gateway(),
        )
        result = await executor.stop(
            StopContext(
                authority=AuthorityContext(
                    runtime_id="golden-path",
                    boot_id="boot-1",
                    authority_generation=1,
                    safety_epoch=1,
                ),
                robot_id="reference-ackermann",
                stop_id="stop-1",
                trigger=StopTrigger.OPERATOR,
            ),
            _NoEvents(),
        )

        assert result.request_accepted is True
        assert result.cancel_requested is True
        assert result.cancel_acknowledged is False
        assert result.zero_velocity_command_published is True

    asyncio.run(run())


def test_typed_execution_plan_runs_a_b_c_and_dock_through_production_chain(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        locations = [
            Location(id="a", name="A", pose=Pose2D(x=1.0, y=0.0, yaw=0.0)),
            Location(id="b", name="B", pose=Pose2D(x=2.0, y=0.0, yaw=0.0)),
            Location(id="c", name="C", pose=Pose2D(x=3.0, y=0.0, yaw=0.0)),
            Location(id="dock", name="Dock", pose=Pose2D(x=0.0, y=0.0, yaw=0.0)),
        ]
        locations_path = tmp_path / "locations.toml"
        save_locations(locations, locations_path)
        config_path = tmp_path / "config.toml"
        config = AppConfig(
            locations_path="locations.toml",
            route_adapter="nav2",
            vehicle=VehicleProfile(robot_id="robot-1"),
            site=SiteProfile(
                site_id="site-1",
                display_name="Site 1",
                version="1",
                active=True,
                validated=True,
                map_sha256="f" * 64,
                locations_sha256=fingerprint_locations_file(locations_path),
                locations_path="locations.toml",
                validated_routes=["A", "B", "C", "Dock"],
                default_patrol=["A", "B", "C"],
                home_location="Dock",
                dock_location="Dock",
            ),
        )
        mission = bind_patrol_mission(
            config,
            config_path,
            MissionDraft(),
            mission_id="mission-1",
        )
        plan = compile_patrol_mission(mission)

        class Gateway:
            def __init__(self) -> None:
                self.location_names: list[str] = []

            async def execute(self, action, **_kwargs) -> RouteOutput:
                goal = action["goal"]
                assert isinstance(goal, dict)
                name = goal["name"]
                assert isinstance(name, str)
                self.location_names.append(name)
                attempt = len(self.location_names)
                return RouteOutput(
                    input_text="",
                    outgoing_action=action,
                    approval_status="approved",
                    execution_status="succeeded",
                    route_preview=f"Arrived at {name}",
                    navigation_attempts=[
                        NavigationAttemptEvidence(
                            attempt=1,
                            tag=f"goal-{attempt}",
                            execution_status="succeeded",
                            detail=f"Arrived at {name}",
                            terminal_status="succeeded",
                            terminal_observed=True,
                            endpoint_pose_observed=True,
                            position_error_m=0.04,
                        )
                    ],
                )

            async def stop(self) -> HaltReceipt:
                raise AssertionError("STOP was not requested")

        gateway = Gateway()
        executor = build_navigation_capability_executor(
            config=config,
            config_path=config_path,
            gateway=gateway,
        )
        adapter = NavigationAtomicStepAdapter(
            executor=executor,
            authority=AuthorityContext(
                runtime_id="golden-path",
                boot_id="boot-1",
                authority_generation=1,
                safety_epoch=1,
            ),
            mission=mission,
            fencing_token=1,
            events=_NoEvents(),
        )

        report = await ExecutionEngine(plan, adapter).run()

        assert report.outcome is TaskOutcome.SUCCEEDED
        assert gateway.location_names == ["A", "B", "C", "Dock"]
        assert len(report.step_records) == 4
        assert all(
            record.attempts[-1].result.disposition is StepDisposition.SUCCEEDED
            for record in report.step_records
        )

    asyncio.run(run())


def test_adapter_maps_typed_failure_and_cancel_without_owning_policy() -> None:
    async def observe(report: CapabilityExecutionReport) -> StepDisposition:
        adapter = NavigationAtomicStepAdapter(
            executor=_RecordingExecutor(report),
            authority=AuthorityContext(
                runtime_id="golden-path",
                boot_id="boot-1",
                authority_generation=1,
                safety_epoch=1,
            ),
            mission=_mission(),
            fencing_token=1,
            events=_NoEvents(),
        )
        result = await adapter.execute(
            NavigateStep(
                location_id="a",
                location_name="A",
                position_tolerance_m=0.15,
            ),
            _dispatch(),
        )
        return result.disposition

    local_failure = CapabilityExecutionReport(
        disposition="failed",
        summary="Nav2 found no safe path.",
        evidence=(
            _evidence(
                "navigation_failure",
                {"scope": "waypoint_local"},
            ),
        ),
    )
    cancelled = CapabilityExecutionReport(
        disposition="cancelled",
        summary="Navigation canceled.",
    )

    assert asyncio.run(observe(local_failure)) is StepDisposition.WAYPOINT_LOCAL_FAILURE
    assert asyncio.run(observe(cancelled)) is StepDisposition.CANCELLED


def test_adapter_rejects_authority_that_cannot_use_stop_port(tmp_path: Path) -> None:
    class Gateway:
        def __init__(self) -> None:
            self.execute_calls = 0

        async def execute(self, _action, **_kwargs) -> RouteOutput:
            self.execute_calls += 1
            raise AssertionError("effect reached Gateway with a STOP-incapable authority")

        async def stop(self) -> HaltReceipt:
            raise AssertionError("stop was not requested")

    gateway = Gateway()
    executor = build_navigation_capability_executor(
        config=AppConfig(),
        config_path=tmp_path / "config.toml",
        gateway=gateway,
    )

    with pytest.raises(ValueError, match="STOP-capable authority context"):
        NavigationAtomicStepAdapter(
            executor=executor,
            authority=AuthorityContext(
                runtime_id="golden-path",
                boot_id="boot-1",
                authority_generation=1,
                safety_epoch=0,
            ),
            mission=_mission(),
            fencing_token=1,
            events=_NoEvents(),
        )

    assert gateway.execute_calls == 0


def test_stop_during_prepare_prevents_capability_execute() -> None:
    class PausedPrepareExecutor(_RecordingExecutor):
        def __init__(self) -> None:
            super().__init__(
                CapabilityExecutionReport(
                    disposition="completed",
                    summary="must not execute",
                ),
                stop_results=[
                    ExecutorStopResult(
                        request_accepted=True,
                        cancel_requested=False,
                        cancel_acknowledged=None,
                        zero_velocity_command_published=True,
                    )
                ],
            )
            self.prepare_started = asyncio.Event()
            self.release_prepare = asyncio.Event()
            self.execute_calls = 0

        async def prepare(
            self,
            step: TypedCapabilityStep,
            context: ExecutionContext,
        ) -> PreparedCapabilityStep:
            self.prepare_started.set()
            try:
                await asyncio.shield(self.release_prepare.wait())
            except asyncio.CancelledError:
                await self.release_prepare.wait()
            return await super().prepare(step, context)

        async def execute(self, prepared, context, events, *, is_cancelled=None):
            self.execute_calls += 1
            return await super().execute(prepared, context, events)

    async def run() -> None:
        executor = PausedPrepareExecutor()
        adapter = NavigationAtomicStepAdapter(
            executor=executor,
            authority=AuthorityContext(
                runtime_id="golden-path",
                boot_id="boot-1",
                authority_generation=1,
                safety_epoch=1,
            ),
            mission=_mission(),
            fencing_token=1,
            events=_NoEvents(),
        )
        engine = ExecutionEngine(compile_patrol_mission(_mission()), adapter)
        run_task = asyncio.create_task(engine.run())
        await executor.prepare_started.wait()

        await engine.stop()
        executor.release_prepare.set()
        report = await run_task

        assert report.outcome is TaskOutcome.CANCELLED
        assert executor.execute_calls == 0

    asyncio.run(run())


def test_stop_during_gateway_preflight_prevents_effectful_dispatch(tmp_path: Path) -> None:
    async def run() -> None:
        locations = [
            Location(id="a", name="A", pose=Pose2D(x=1.0, y=0.0, yaw=0.0)),
            Location(id="b", name="B", pose=Pose2D(x=2.0, y=0.0, yaw=0.0)),
            Location(id="c", name="C", pose=Pose2D(x=3.0, y=0.0, yaw=0.0)),
            Location(id="dock", name="Dock", pose=Pose2D(x=0.0, y=0.0, yaw=0.0)),
        ]
        locations_path = tmp_path / "locations.toml"
        save_locations(locations, locations_path)
        config_path = tmp_path / "config.toml"
        config = AppConfig(
            locations_path="locations.toml",
            route_adapter="nav2",
            vehicle=VehicleProfile(robot_id="robot-1"),
            site=SiteProfile(
                site_id="site-1",
                display_name="Site 1",
                version="1",
                active=True,
                validated=True,
                map_sha256="f" * 64,
                locations_sha256=fingerprint_locations_file(locations_path),
                locations_path="locations.toml",
                validated_routes=["A", "B", "C", "Dock"],
                default_patrol=["A", "B", "C"],
                home_location="Dock",
                dock_location="Dock",
            ),
        )
        mission = bind_patrol_mission(
            config,
            config_path,
            MissionDraft(),
            mission_id="mission-1",
        )

        class PausedGateway:
            def __init__(self) -> None:
                self.preflight_started = asyncio.Event()
                self.release_preflight = asyncio.Event()
                self.effect_calls = 0

            async def execute(self, action, *, is_cancelled, **_kwargs) -> RouteOutput:
                self.preflight_started.set()
                try:
                    await asyncio.shield(self.release_preflight.wait())
                except asyncio.CancelledError:
                    await self.release_preflight.wait()
                if is_cancelled():
                    return RouteOutput(
                        input_text="",
                        outgoing_action=action,
                        approval_status="approved",
                        execution_status="cancelled",
                        route_preview="Cancelled during preflight.",
                    )
                self.effect_calls += 1
                raise AssertionError("effectful dispatch ran after STOP")

            async def stop(self) -> HaltReceipt:
                return HaltReceipt(
                    navigation_cancel_status=NavigationCancelStatus.NOT_ACTIVE,
                    zero_velocity_delivered=True,
                    message="No active goal.",
                )

        gateway = PausedGateway()
        adapter = NavigationAtomicStepAdapter(
            executor=build_navigation_capability_executor(
                config=config,
                config_path=config_path,
                gateway=gateway,
            ),
            authority=AuthorityContext(
                runtime_id="golden-path",
                boot_id="boot-1",
                authority_generation=1,
                safety_epoch=1,
            ),
            mission=mission,
            fencing_token=1,
            events=_NoEvents(),
        )
        engine = ExecutionEngine(compile_patrol_mission(mission), adapter)
        run_task = asyncio.create_task(engine.run())
        await gateway.preflight_started.wait()

        await engine.stop()
        gateway.release_preflight.set()
        report = await run_task

        assert report.outcome is TaskOutcome.CANCELLED
        assert gateway.effect_calls == 0

    asyncio.run(run())


@pytest.mark.parametrize("terminal_status", ["aborted", "rejected", "timed_out"])
def test_terminal_failures_default_to_navigation_system(terminal_status: str) -> None:
    output = RouteOutput(
        input_text="",
        execution_status="failed",
        route_preview="Nav2 terminal failure.",
        navigation_attempts=[
            NavigationAttemptEvidence(
                attempt=1,
                tag="goal-1",
                execution_status="failed",
                detail="Nav2 terminal failure.",
                terminal_status=terminal_status,
                terminal_observed=True,
            )
        ],
    )

    report = _capability_report(output)

    assert report.disposition == ExecutionDisposition.FAILED
    assert len(report.evidence) == 1
    assert report.evidence[0].payload["scope"] == "navigation_system"


def test_single_navigation_production_chain_dispatches_exactly_one_goal(
    tmp_path: Path,
) -> None:
    class Gateway:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def execute(self, action, **_kwargs) -> RouteOutput:
            goal = action["goal"]
            assert isinstance(goal, dict)
            self.calls.append(str(goal["name"]))
            return RouteOutput(
                input_text="",
                execution_status="succeeded",
                route_preview="Arrived at A",
                navigation_attempts=[
                    NavigationAttemptEvidence(
                        attempt=1,
                        tag="goal-A",
                        execution_status="succeeded",
                        detail="Arrived at A",
                        terminal_status="succeeded",
                        terminal_observed=True,
                        endpoint_pose_observed=True,
                        position_error_m=0.04,
                    )
                ],
            )

        async def stop(self) -> HaltReceipt:
            raise AssertionError("STOP was not requested")

    gateway = Gateway()
    report = asyncio.run(_production_engine(tmp_path, gateway, single_target=True).run())

    assert report.outcome is TaskOutcome.SUCCEEDED
    assert gateway.calls == ["A"]
    assert len(report.step_records) == 1


def test_production_chain_retries_no_valid_path_once_then_continues(tmp_path: Path) -> None:
    class Gateway:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def execute(self, action, **_kwargs) -> RouteOutput:
            goal = action["goal"]
            assert isinstance(goal, dict)
            name = str(goal["name"])
            self.calls.append(name)
            if name == "A":
                return RouteOutput(
                    input_text="",
                    execution_status="failed",
                    route_preview="NO_VALID_PATH",
                    failure_scope="waypoint_local",
                )
            return RouteOutput(
                input_text="",
                execution_status="succeeded",
                route_preview=f"Arrived at {name}",
                navigation_attempts=[
                    NavigationAttemptEvidence(
                        attempt=1,
                        tag=f"goal-{name}",
                        execution_status="succeeded",
                        detail=f"Arrived at {name}",
                        terminal_status="succeeded",
                        terminal_observed=True,
                        endpoint_pose_observed=True,
                        position_error_m=0.04,
                    )
                ],
            )

        async def stop(self) -> HaltReceipt:
            raise AssertionError("STOP was not requested")

    gateway = Gateway()
    report = asyncio.run(_production_engine(tmp_path, gateway).run())

    assert report.outcome is TaskOutcome.PARTIAL
    assert gateway.calls == ["A", "A", "B", "C", "Dock"]


def test_production_chain_system_plan_failure_aborts_before_next_waypoint(
    tmp_path: Path,
) -> None:
    class Gateway:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def execute(self, action, **_kwargs) -> RouteOutput:
            goal = action["goal"]
            assert isinstance(goal, dict)
            self.calls.append(str(goal["name"]))
            return RouteOutput(
                input_text="",
                execution_status="failed",
                route_preview="TF_ERROR",
                failure_scope="navigation_system",
            )

        async def stop(self) -> HaltReceipt:
            raise AssertionError("STOP was not requested")

    gateway = Gateway()
    report = asyncio.run(_production_engine(tmp_path, gateway).run())

    assert report.outcome is TaskOutcome.FAILED
    assert gateway.calls == ["A"]
