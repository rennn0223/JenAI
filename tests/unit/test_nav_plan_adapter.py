from __future__ import annotations

from types import SimpleNamespace

import pytest

from jenai.bridge._nav_plan import path_plan_payload


def _position(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(pose=SimpleNamespace(position=SimpleNamespace(x=x, y=y)))


def _wrapped_result(
    *, error_code: int = 0, poses: list[SimpleNamespace] | None = None
) -> SimpleNamespace:
    result = SimpleNamespace(
        path=SimpleNamespace(poses=poses if poses is not None else [_position(0.0, 0.0)]),
        planning_time=SimpleNamespace(sec=1, nanosec=250_000_000),
        error_code=error_code,
        error_msg="planner detail",
    )
    return SimpleNamespace(result=result)


def test_path_plan_payload_reports_feasible_path_geometry() -> None:
    payload = path_plan_payload(_wrapped_result(poses=[_position(0.0, 0.0), _position(3.0, 4.0)]))

    assert payload == {
        "feasible": True,
        "pose_count": 2,
        "path_length_m": pytest.approx(5.0),
        "planning_time_s": pytest.approx(1.25),
        "error_code": 0,
        "error_name": "NONE",
        "error_message": "planner detail",
    }


def test_path_plan_payload_preserves_known_and_future_error_codes() -> None:
    blocked = path_plan_payload(_wrapped_result(error_code=208, poses=[]))
    future = path_plan_payload(_wrapped_result(error_code=999, poses=[]))

    assert not blocked["feasible"] and blocked["error_name"] == "NO_VALID_PATH"
    assert not future["feasible"] and future["error_name"] == "ERROR_999"
