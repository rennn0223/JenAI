"""Agent-tool wrappers around shell_core."""

from __future__ import annotations

from agents import RunContextWrapper, function_tool

from jenai.agent.context import JenAIRunContext
from jenai.schemas import EffectScope, RiskLevel, ToolCallCategory
from jenai.tools import shell_core
from jenai.tools.registry import ToolRiskInfo, register_tool
from jenai.tools.tracking import finish_tool_call, record_tool_call

_SHELL_RUN_INFO = ToolRiskInfo(
    risk_level=RiskLevel.P2,
    effect_scope=EffectScope.HOST_COMMAND,
    needs_approval=True,
    description="Run a host shell command.",
)


@function_tool(needs_approval=True)
async def shell_run_tool(
    ctx: RunContextWrapper[JenAIRunContext], command: str, cwd: str = ""
) -> dict:
    """Run a shell command on the host. Requires human approval. Use only when a task cannot
    be completed with the dedicated ROS2 / route tools."""
    call = record_tool_call(
        ctx, "shell_run_tool", ToolCallCategory.SHELL, command, _SHELL_RUN_INFO
    )
    output = await shell_core.run_shell(command, cwd=cwd or None)
    finish_tool_call(
        ctx, call, ok=output.exit_code == 0, summary=f"exit {output.exit_code}"
    )
    return output.model_dump()


register_tool("shell_run_tool", _SHELL_RUN_INFO)
