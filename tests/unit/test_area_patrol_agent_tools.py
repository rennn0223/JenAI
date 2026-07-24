from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jenai.agent.context import JenAIRunContext
from jenai.config.store import build_minimal_config
from jenai.schemas import Location, Pose2D, RouteOutput, SessionState, VisionOutput
from jenai.secure_files import atomic_write_bytes
from jenai.state.runs import RunStore
from jenai.tools.area_patrol_service import (
    AgentAreaPatrolRuntime,
    _normalize_patrol_target,
    _step_verdict,
)
from jenai.tools.nav_live import NavProgress
from jenai.workflows.area_patrol import (
    InspectionPoint,
    InspectionVerdict,
    StepVerdict,
)


def _context(tmp_path: Path) -> JenAIRunContext:
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="model",
        api_key_env="",
    )
    assert config.model_bindings is not None
    session = SessionState(
        provider_profile="test",
        model_bindings=config.model_bindings,
        working_directory=str(tmp_path),
    )
    store = RunStore()
    run = store.create_run(session.session_id, "inspect the whole laboratory")
    return JenAIRunContext(
        config=config,
        config_path=tmp_path / "config.toml",
        session=session,
        run=run,
        run_store=store,
    )


@pytest.mark.parametrize(
    ("adapter_status", "expected"),
    [
        ("succeeded", StepVerdict.SUCCEEDED),
        ("blocked", StepVerdict.BLOCKED),
        ("referred", StepVerdict.BLOCKED),
        ("unavailable", StepVerdict.RETRYABLE_FAILURE),
        ("cancelled", StepVerdict.CANCELLED),
        ("endpoint_mismatch", StepVerdict.RETRYABLE_FAILURE),
        ("failed", StepVerdict.RETRYABLE_FAILURE),
        ("unknown", StepVerdict.FAILED),
    ],
)
def test_nav2_adapter_statuses_are_classified(
    adapter_status: str,
    expected: StepVerdict,
) -> None:
    output = RouteOutput(
        input_text="",
        route_preview=adapter_status,
        execution_status=adapter_status,
    )

    assert _step_verdict(output) is expected


@pytest.mark.parametrize(
    "alias",
    [
        "all",
        "Site Profile",
        "current Site Profile",
        "all required areas",
        "所有必巡區域",
    ],
)
def test_whole_site_model_aliases_are_normalized(alias: str) -> None:
    assert _normalize_patrol_target(alias) == "all"


def test_named_semantic_area_is_not_rewritten() -> None:
    assert _normalize_patrol_target("equipment") == "equipment"


def test_navigation_progress_is_forwarded_to_workflow_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    updates: list[str] = []
    context = _context(tmp_path)
    locations = [
        Location(name="Dock", pose=Pose2D(x=1.0, y=2.0, yaw=0.0)),
    ]

    async def fake_execute_navigation(config, action, **kwargs) -> RouteOutput:
        on_progress = kwargs["on_progress"]
        on_progress(NavProgress(distance_remaining=1.2, recoveries=0, elapsed=3.0))
        return RouteOutput(
            input_text="",
            route_preview="Arrived at the goal.",
            execution_status="succeeded",
        )

    monkeypatch.setattr(
        "jenai.tools.area_patrol_service.execute_navigation",
        fake_execute_navigation,
    )
    runtime = AgentAreaPatrolRuntime(
        context,
        locations,
        on_status=updates.append,
    )

    result = asyncio.run(runtime.navigate(InspectionPoint("Dock")))

    assert result.verdict is StepVerdict.SUCCEEDED
    assert updates[0] == "Navigating to Dock"
    assert any("1.2 m remaining" in update for update in updates)
    assert updates[-1] == "Reached Dock · succeeded"


def test_inspection_preserves_image_evidence_and_requires_review_for_anomaly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _context(tmp_path)
    bridge_stopped = False

    class FakeBridge:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            nonlocal bridge_stopped
            bridge_stopped = True

    async def fake_capture(
        config,
        bridge,
        topic,
        *,
        timeout=5.0,
        on_captured=None,
        preserve_to=None,
        task_context="",
    ) -> VisionOutput:
        assert preserve_to is not None
        atomic_write_bytes(preserve_to, b"inspection-evidence")
        return VisionOutput(
            source=str(preserve_to),
            summary="Unexpected package in the aisle.",
            anomalies=["unexpected package"],
        )

    monkeypatch.setattr(
        "jenai.tools.area_patrol_service.RosBridgeClient",
        FakeBridge,
    )
    monkeypatch.setattr(
        "jenai.tools.area_patrol_service.capture_and_analyze",
        fake_capture,
    )
    runtime = AgentAreaPatrolRuntime(context, [])

    async def run():
        result = await runtime.inspect(InspectionPoint("Equipment"))
        second = await runtime.inspect(InspectionPoint("Equipment"))
        await runtime.close()
        await runtime.close()
        return result, second

    result, second_result = asyncio.run(run())

    assert result.verdict is InspectionVerdict.REQUIRES_REVIEW
    assert "unexpected package" in result.detail
    assert len(result.evidence) == 1
    evidence = Path(result.evidence[0])
    assert evidence.read_bytes() == b"inspection-evidence"
    assert evidence.parent == tmp_path / "reports"
    assert evidence.name.startswith("evidence-")
    assert second_result.evidence != result.evidence
    assert Path(second_result.evidence[0]).exists()
    assert bridge_stopped is True


def test_inspection_failure_never_claims_verified(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _context(tmp_path)

    class FailingBridge:
        async def start(self) -> None:
            raise RuntimeError("camera offline")

    monkeypatch.setattr(
        "jenai.tools.area_patrol_service.RosBridgeClient",
        FailingBridge,
    )
    runtime = AgentAreaPatrolRuntime(context, [])

    result = asyncio.run(runtime.inspect(InspectionPoint("Equipment")))

    assert result.verdict is InspectionVerdict.REQUIRES_REVIEW
    assert result.evidence == ()
    assert "camera offline" in result.detail
