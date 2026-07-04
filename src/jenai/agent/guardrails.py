"""Input/output guardrails for the /run agent."""

from __future__ import annotations

from typing import Any

from agents import GuardrailFunctionOutput, RunContextWrapper, input_guardrail

# Phrases that indicate the user is trying to *disable* a safety mechanism. This
# is the openai-agents *input guardrail* layer (a fast tripwire that runs before
# the agent acts); the deterministic velocity clamp in ros2_core is the hard
# floor beneath it.
#
# Deliberately narrow: it targets "turn safety off / ignore obstacles" intent,
# NOT speed adjectives. Phrases like "full speed" / "max speed" are left out —
# they appear in benign requests and questions ("what is its max speed?") and are
# bounded by the clamp regardless, so tripping the whole /run on them was a
# false positive.
_UNSAFE_PHRASES = (
    "disable safety",
    "no safety",
    "turn off safety",
    "override safety",
    "ignore obstacle",
    "ignore obstacles",
    "ignore the obstacle",
    "disable the clamp",
    "remove the speed limit",
    "解除安全",
    "關掉安全",
    "關閉安全",
    "無視障礙",
    "忽略障礙",
)


@input_guardrail
async def unsafe_command_guardrail(
    ctx: RunContextWrapper[Any], agent: Any, agent_input: Any
) -> GuardrailFunctionOutput:
    """Trip when a request tries to disable safety or force unsafe motion.

    Returning ``tripwire_triggered=True`` makes the SDK raise
    ``InputGuardrailTripwireTriggered`` before the agent runs any tool.
    """
    text = agent_input if isinstance(agent_input, str) else str(agent_input)
    lowered = text.lower()
    matched = next((phrase for phrase in _UNSAFE_PHRASES if phrase in lowered), None)
    return GuardrailFunctionOutput(
        output_info={"matched": matched},
        tripwire_triggered=matched is not None,
    )
