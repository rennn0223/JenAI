"""Shared approval policy for TUI direct commands and agent interruptions.

The widget decides how a prompt looks; this module decides whether an action
may bypass a prompt. Keeping that rule in one place prevents the same shell
command from receiving different protection depending on whether it came from
/shell or from the natural-language agent.
"""

from __future__ import annotations

from jenai.schemas import ApprovalRequest, EffectScope, RiskLevel


def requires_explicit_approval(approval: ApprovalRequest) -> bool:
    """Return whether every occurrence must receive a fresh operator decision.

    P2 actions and host commands never inherit auto mode or a remembered
    decision. A host command can change risk when its text changes, so
    remembering the broad tool name would let a harmless command authorize a
    later destructive one.
    """

    return str(approval.risk_level) == str(RiskLevel.P2) or str(approval.effect_scope) == str(
        EffectScope.HOST_COMMAND
    )


def can_remember_approval(approval: ApprovalRequest) -> bool:
    """Only bounded, non-host P0/P1 capabilities may be remembered."""

    return not requires_explicit_approval(approval)


def should_default_to_reject(approval: ApprovalRequest) -> bool:
    """Keep Enter fail-safe for high-risk, host, and robot-control prompts."""
    return (
        str(approval.risk_level) == str(RiskLevel.P2)
        or str(approval.effect_scope) == str(EffectScope.HOST_COMMAND)
        or str(approval.effect_scope) == str(EffectScope.SIM_CONTROL)
    )


def may_auto_approve(
    approval: ApprovalRequest,
    *,
    auto_mode: bool,
    remembered: bool,
) -> bool:
    """Apply the same auto/remember boundary to every TUI approval source."""

    return can_remember_approval(approval) and (auto_mode or remembered)
