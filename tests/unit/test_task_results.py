from __future__ import annotations

import pytest

from jenai.schemas import (
    ApprovalRequest,
    EffectScope,
    RiskLevel,
    RouteOutput,
    RunRecord,
    RunStatus,
    TaskOutcome,
    ToolCallCategory,
    ToolCallRecord,
)
from jenai.task_results import (
    agent_completion_outcome,
    aggregate_step_outcome,
    navigation_output_result,
    navigation_receipt_text,
    navigation_result,
    run_status_for_outcome,
)


@pytest.mark.parametrize(
    ("execution_status", "run_status", "outcome"),
    [
        ("succeeded", RunStatus.COMPLETED, TaskOutcome.SUCCEEDED),
        ("endpoint_mismatch", RunStatus.COMPLETED, TaskOutcome.ENDPOINT_MISMATCH),
        ("blocked", RunStatus.BLOCKED, TaskOutcome.BLOCKED),
        ("referred", RunStatus.BLOCKED, TaskOutcome.BLOCKED),
        ("unavailable", RunStatus.FAILED, TaskOutcome.UNAVAILABLE),
        ("failed", RunStatus.FAILED, TaskOutcome.FAILED),
        ("cancelled", RunStatus.INTERRUPTED, TaskOutcome.CANCELLED),
    ],
)
def test_navigation_result_has_one_lifecycle_and_outcome_contract(
    execution_status: str,
    run_status: RunStatus,
    outcome: TaskOutcome,
) -> None:
    result = navigation_result(execution_status)

    assert result.run_status == run_status
    assert result.outcome == outcome


def test_agent_without_completion_evidence_is_partial() -> None:
    run = RunRecord(
        session_id="session-1",
        user_input="Do the task",
    )

    assert agent_completion_outcome(run) == TaskOutcome.PARTIAL


def test_agent_rejected_by_operator_is_blocked() -> None:
    run = RunRecord(
        session_id="session-1",
        user_input="Move the robot",
    )
    run.interruptions.append(
        ApprovalRequest(
            run_id=run.run_id,
            tool_call_id="call-1",
            title="Move",
            summary="Move the robot",
            raw_action="navigate",
            risk_level=RiskLevel.P1,
            effect_scope=EffectScope.SIM_CONTROL,
            justification="requested",
            status="rejected",
        )
    )

    assert agent_completion_outcome(run) == TaskOutcome.BLOCKED


def test_agent_failed_dependency_is_unavailable() -> None:
    run = RunRecord(
        session_id="session-1",
        user_input="Navigate",
        tool_calls=[
            ToolCallRecord(
                tool_name="route_execute_tool",
                category=ToolCallCategory.ROUTE,
                input_summary="navigate",
                status="failed",
                output_summary="Nav2 is unavailable",
            )
        ],
    )

    assert agent_completion_outcome(run) == TaskOutcome.UNAVAILABLE


def test_navigation_output_derives_dock_outcome_from_canonical_action() -> None:
    output = RouteOutput(
        input_text="dock",
        execution_status="succeeded",
        route_preview="Arrived at the configured dock pose.",
        outgoing_action={
            "goal": {"name": "Dock"},
            "capability_id": "dock_approach",
        },
    )

    result = navigation_output_result(output)

    assert result.run_status == RunStatus.COMPLETED
    assert result.outcome == TaskOutcome.ARRIVED_UNVERIFIED


def test_navigation_output_keeps_non_dock_success_succeeded() -> None:
    output = RouteOutput(
        input_text="corner",
        execution_status="succeeded",
        route_preview="Arrived at the goal.",
        outgoing_action={"goal": {"name": "Corner"}},
    )

    result = navigation_output_result(output)

    assert result.run_status == RunStatus.COMPLETED
    assert result.outcome == TaskOutcome.SUCCEEDED


def test_navigation_receipt_exposes_lifecycle_outcome_adapter_status_and_evidence() -> None:
    output = RouteOutput(
        input_text="dock",
        execution_status="succeeded",
        route_preview="Arrived at the configured dock pose.",
        outgoing_action={"capability_id": "dock_approach"},
    )

    receipt = navigation_receipt_text(output)

    assert receipt.startswith(
        "status=completed; outcome=arrived_unverified; adapter_status=succeeded"
    )
    assert receipt.endswith("Arrived at the configured dock pose.")


@pytest.mark.parametrize(
    ("statuses", "outcome"),
    [
        (["succeeded", "succeeded"], TaskOutcome.SUCCEEDED),
        (["succeeded", "partial"], TaskOutcome.PARTIAL),
        (["succeeded", "failed"], TaskOutcome.PARTIAL),
        (["blocked"], TaskOutcome.BLOCKED),
        (["referred"], TaskOutcome.BLOCKED),
        (["unavailable"], TaskOutcome.UNAVAILABLE),
        (["failed"], TaskOutcome.FAILED),
    ],
)
def test_multi_step_outcome_is_aggregated_from_observed_step_statuses(
    statuses: list[str], outcome: TaskOutcome
) -> None:
    assert aggregate_step_outcome(statuses) == outcome


@pytest.mark.parametrize(
    ("outcome", "run_status"),
    [
        (TaskOutcome.SUCCEEDED, RunStatus.COMPLETED),
        (TaskOutcome.PARTIAL, RunStatus.COMPLETED),
        (TaskOutcome.ARRIVED_UNVERIFIED, RunStatus.COMPLETED),
        (TaskOutcome.ENDPOINT_MISMATCH, RunStatus.COMPLETED),
        (TaskOutcome.BLOCKED, RunStatus.BLOCKED),
        (TaskOutcome.UNAVAILABLE, RunStatus.FAILED),
        (TaskOutcome.FAILED, RunStatus.FAILED),
        (TaskOutcome.CANCELLED, RunStatus.INTERRUPTED),
    ],
)
def test_product_outcome_determines_terminal_lifecycle(
    outcome: TaskOutcome,
    run_status: RunStatus,
) -> None:
    assert run_status_for_outcome(outcome) == run_status
