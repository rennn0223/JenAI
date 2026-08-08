"""Process-local approval binding for one Golden Path navigation plan."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jenai.schemas import TaskOutcome
from jenai.workflows.execution_engine import ExecutionReport
from jenai.workflows.patrol_mission import ExecutionPlan, render_plan_preview


class NavigationApprovalMismatchError(ValueError):
    """An approval cannot authorize this plan in the current process scope."""


class ApprovalChoice(StrEnum):
    YES = "yes"
    AUTO = "auto"
    NO = "no"


class _ApprovalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _required_text(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("text must not be blank")
    return stripped


class PendingNavigationApproval(_ApprovalModel):
    request_id: str
    session_id: str
    approval_generation: int = Field(ge=1)
    mission_id: str
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview: str
    requires_operator_input: bool

    _normalize_text = field_validator(
        "request_id",
        "session_id",
        "mission_id",
        "preview",
    )(_required_text)


class NavigationApprovalResult(_ApprovalModel):
    outcome: TaskOutcome
    automatic: bool
    execution_report: ExecutionReport | None = None


ExecuteApprovedPlan = Callable[[ExecutionPlan], Awaitable[ExecutionReport]]


class NavigationApprovalScope:
    """Own one process-local approval generation and exact-plan Auto memory."""

    def __init__(self, *, session_id: str) -> None:
        self._session_id = _required_text(session_id)
        self._approval_generation = 1
        self._auto_plan_digest: str | None = None
        self._consumed_request_ids: set[str] = set()
        self._issued_requests: dict[str, PendingNavigationApproval] = {}

    @property
    def approval_generation(self) -> int:
        return self._approval_generation

    def prepare(self, plan: ExecutionPlan) -> PendingNavigationApproval:
        detached = ExecutionPlan.model_validate(plan.model_dump(mode="json"))
        pending = PendingNavigationApproval(
            request_id=uuid4().hex,
            session_id=self._session_id,
            approval_generation=self._approval_generation,
            mission_id=detached.mission.mission_id,
            plan_digest=detached.plan_digest,
            preview=render_plan_preview(detached),
            requires_operator_input=self._auto_plan_digest != detached.plan_digest,
        )
        self._issued_requests[pending.request_id] = pending
        return pending

    def advance_generation(self, reason: str) -> int:
        """Invalidate every outstanding Yes/Auto authority after a safety boundary."""

        _required_text(reason)
        self._approval_generation += 1
        self._auto_plan_digest = None
        return self._approval_generation

    async def resolve(
        self,
        pending: PendingNavigationApproval,
        plan: ExecutionPlan,
        choice: ApprovalChoice | None,
        execute: ExecuteApprovedPlan,
    ) -> NavigationApprovalResult:
        detached_pending = PendingNavigationApproval.model_validate(pending.model_dump(mode="json"))
        detached_plan = ExecutionPlan.model_validate(plan.model_dump(mode="json"))
        issued = self._issued_requests.pop(detached_pending.request_id, None)
        if issued is None:
            if detached_pending.request_id in self._consumed_request_ids:
                raise NavigationApprovalMismatchError("approval request was already consumed")
            raise NavigationApprovalMismatchError("approval request was not issued by this scope")
        self._consumed_request_ids.add(detached_pending.request_id)
        if issued != detached_pending:
            raise NavigationApprovalMismatchError("approval request content was modified")
        self._validate_pending(detached_pending, detached_plan)

        automatic = self._auto_plan_digest == detached_plan.plan_digest
        if automatic:
            if choice is not None:
                raise NavigationApprovalMismatchError(
                    "an Auto-matched request must not inject a new approval choice"
                )
        elif choice is None:
            raise NavigationApprovalMismatchError("operator approval is required")

        if choice is ApprovalChoice.NO:
            return NavigationApprovalResult(
                outcome=TaskOutcome.BLOCKED,
                automatic=False,
            )
        report = await execute(detached_plan)
        detached_report = ExecutionReport.model_validate(report.model_dump(mode="json"))
        if detached_report.plan_digest != detached_plan.plan_digest:
            raise NavigationApprovalMismatchError(
                "execution report plan digest differs from the approved plan"
            )
        self._update_auto_authorization(
            choice=choice,
            pending=detached_pending,
            plan=detached_plan,
            report=detached_report,
        )
        return NavigationApprovalResult(
            outcome=detached_report.outcome,
            automatic=automatic,
            execution_report=detached_report,
        )

    def _update_auto_authorization(
        self,
        *,
        choice: ApprovalChoice | None,
        pending: PendingNavigationApproval,
        plan: ExecutionPlan,
        report: ExecutionReport,
    ) -> None:
        if report.outcome is TaskOutcome.CANCELLED:
            self._auto_plan_digest = None
        elif (
            choice is ApprovalChoice.AUTO
            and pending.approval_generation == self._approval_generation
        ):
            self._auto_plan_digest = plan.plan_digest

    def _validate_pending(
        self,
        pending: PendingNavigationApproval,
        plan: ExecutionPlan,
    ) -> None:
        if pending.session_id != self._session_id:
            raise NavigationApprovalMismatchError("approval belongs to a different session")
        if pending.approval_generation != self._approval_generation:
            raise NavigationApprovalMismatchError("approval generation is stale")
        if pending.mission_id != plan.mission.mission_id or pending.plan_digest != plan.plan_digest:
            raise NavigationApprovalMismatchError("approval plan binding does not match")
        if pending.preview != render_plan_preview(plan):
            raise NavigationApprovalMismatchError("approval preview does not match the exact plan")
