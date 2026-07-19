"""Agent layer: planning/review/run agents, session wiring, guardrails."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jenai.agent.context import JenAIRunContext
    from jenai.agent.plan_agent import review_plan, run_plan
    from jenai.agent.run_agent import build_run_agent, run_task
    from jenai.agent.runtime import build_model, build_plan_agent, build_review_agent

__all__ = [
    "JenAIRunContext",
    "build_model",
    "build_plan_agent",
    "build_review_agent",
    "build_run_agent",
    "review_plan",
    "run_plan",
    "run_task",
]

_LAZY_EXPORTS = {
    "JenAIRunContext": ("jenai.agent.context", "JenAIRunContext"),
    "build_model": ("jenai.agent.runtime", "build_model"),
    "build_plan_agent": ("jenai.agent.runtime", "build_plan_agent"),
    "build_review_agent": ("jenai.agent.runtime", "build_review_agent"),
    "build_run_agent": ("jenai.agent.run_agent", "build_run_agent"),
    "review_plan": ("jenai.agent.plan_agent", "review_plan"),
    "run_plan": ("jenai.agent.plan_agent", "run_plan"),
    "run_task": ("jenai.agent.run_agent", "run_task"),
}


def __getattr__(name: str) -> Any:
    """Load public Agent APIs on demand instead of importing the full graph.

    Tool modules import the lightweight run context. Eagerly importing
    run_agent here pulled those same tools back through specialists and made
    clean, standalone imports order-dependent.
    """

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
