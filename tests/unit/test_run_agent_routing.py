from __future__ import annotations

from jenai.agent.intent_routing import RunAgentRoute, route_run_request
from jenai.agent.run_agent import build_run_agent
from jenai.config.store import build_minimal_config


def _config():
    return build_minimal_config(
        provider_name="local",
        provider="ollama",
        default_model="qwen3.6:35b",
        api_key_env="",
    )


def test_explicit_site_coverage_routes_to_one_high_level_workflow() -> None:
    task = "請巡邏目前 Site Profile 的所有必巡區域，每個區域都保存觀察證據，完成後回到 dock。"

    assert route_run_request(task) is RunAgentRoute.AREA_PATROL

    agent = build_run_agent(_config(), task)

    assert agent.name == "Area Patrol Workflow Selector"
    assert [tool.name for tool in agent.tools] == ["area_patrol_workflow_tool"]
    assert agent.handoffs == []
    assert agent.model_settings.tool_choice == "required"
    assert agent.model_settings.parallel_tool_calls is False
    assert agent.model_settings.reasoning is not None
    assert agent.model_settings.reasoning.effort == "none"
    assert agent.tool_use_behavior == "stop_on_first_tool"


def test_ambiguous_patrol_stays_with_general_supervisor() -> None:
    assert route_run_request("幫我巡邏一下") is RunAgentRoute.GENERAL
    assert build_run_agent(_config(), "幫我巡邏一下").name == "JenAI"


def test_ordered_waypoints_are_not_misrouted_to_area_coverage() -> None:
    task = "依序巡邏所有指定區域：先去 A，然後去 B，最後回 C。"

    assert route_run_request(task) is RunAgentRoute.GENERAL


def test_state_and_named_navigation_requests_are_not_area_patrols() -> None:
    assert route_run_request("檢查目前機器人位置和 Nav2 狀態") is RunAgentRoute.GENERAL
    assert route_run_request("navigate to the laboratory") is RunAgentRoute.GENERAL
