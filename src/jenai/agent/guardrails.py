from __future__ import annotations

from typing import Any

from agents import GuardrailFunctionOutput, RunContextWrapper, input_guardrail

# Phrases that indicate the user is trying to bypass safety. This is the
# openai-agents *input guardrail* layer (a fast tripwire that runs before the
# agent acts); the deterministic velocity clamp in ros2_core is the hard floor
# beneath it.
_UNSAFE_PHRASES = (
    "disable safety",
    "no safety",
    "turn off safety",
    "override safety",
    "ignore obstacle",
    "ignore obstacles",
    "ignore the obstacle",
    "full speed",
    "max speed",
    "as fast as possible",
    "解除安全",
    "關掉安全",
    "無視障礙",
    "全速",
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
