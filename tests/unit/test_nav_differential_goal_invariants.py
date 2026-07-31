from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from jenai.acceptance.nav_differential import CanonicalGoal, compare_goals


def test_direct_validation_canonicalizes_frame_and_quaternion() -> None:
    goal = CanonicalGoal(
        frame_id="  ///map  ",
        x=1.0,
        y=2.0,
        yaw=2.0 * math.pi + 0.4,
        qx=0.0,
        qy=0.0,
        qz=2.0 * math.sin(0.2),
        qw=2.0 * math.cos(0.2),
    )

    assert goal.frame_id == "map"
    assert goal.yaw == pytest.approx(0.4)
    assert math.sqrt(goal.qx**2 + goal.qy**2 + goal.qz**2 + goal.qw**2) == pytest.approx(1.0)


@pytest.mark.parametrize("frame_id", ["", "   ", "/", "  ///  "])
def test_direct_validation_rejects_blank_canonical_frame(frame_id: str) -> None:
    with pytest.raises(ValidationError, match="frame_id must not be blank"):
        CanonicalGoal(
            frame_id=frame_id,
            x=1.0,
            y=2.0,
            yaw=0.0,
            qx=0.0,
            qy=0.0,
            qz=0.0,
            qw=1.0,
        )


def test_direct_validation_rejects_zero_quaternion() -> None:
    with pytest.raises(ValidationError, match="quaternion must have non-zero norm"):
        CanonicalGoal(
            frame_id="map",
            x=1.0,
            y=2.0,
            yaw=0.0,
            qx=0.0,
            qy=0.0,
            qz=0.0,
            qw=0.0,
        )


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_direct_validation_rejects_nonfinite_quaternion(value: float) -> None:
    with pytest.raises(ValidationError):
        CanonicalGoal(
            frame_id="map",
            x=1.0,
            y=2.0,
            yaw=0.0,
            qx=value,
            qy=0.0,
            qz=0.0,
            qw=1.0,
        )


@pytest.mark.parametrize("scale", [1e-300, 1e300])
def test_direct_validation_normalizes_any_finite_nonzero_scale(scale: float) -> None:
    goal = CanonicalGoal(
        frame_id="map",
        x=1.0,
        y=2.0,
        yaw=0.0,
        qx=0.0,
        qy=0.0,
        qz=0.0,
        qw=scale,
    )

    assert (goal.qx, goal.qy, goal.qz, goal.qw) == pytest.approx((0.0, 0.0, 0.0, 1.0))


def test_direct_validation_normalizes_large_finite_planar_components() -> None:
    goal = CanonicalGoal(
        frame_id="map",
        x=1.0,
        y=2.0,
        yaw=math.pi / 2.0,
        qx=0.0,
        qy=0.0,
        qz=1.7e308,
        qw=1.7e308,
    )

    assert (goal.qz, goal.qw) == pytest.approx((math.sqrt(0.5), math.sqrt(0.5)))


def test_model_validate_preserves_quaternion_sign_equivalence() -> None:
    yaw = 0.6
    positive = CanonicalGoal.model_validate(
        {
            "frame_id": "/map",
            "x": 1.0,
            "y": 2.0,
            "yaw": yaw,
            "qx": 0.0,
            "qy": 0.0,
            "qz": 5.0 * math.sin(yaw / 2.0),
            "qw": 5.0 * math.cos(yaw / 2.0),
        }
    )
    negative = CanonicalGoal.model_validate(
        {
            "frame_id": "map",
            "x": 1.0,
            "y": 2.0,
            "yaw": yaw,
            "qx": -positive.qx,
            "qy": -positive.qy,
            "qz": -positive.qz,
            "qw": -positive.qw,
        }
    )

    assert compare_goals(positive, negative).equivalent is True


def test_from_quaternion_accepts_equivalent_orientation_after_many_turns() -> None:
    source_yaw = -999.89

    goal = CanonicalGoal.from_quaternion(
        frame_id="map",
        x=1.0,
        y=2.0,
        qx=0.0,
        qy=0.0,
        qz=math.sin(source_yaw / 2.0),
        qw=math.cos(source_yaw / 2.0),
    )

    assert -math.pi <= goal.yaw <= math.pi
    assert math.hypot(goal.qx, goal.qy, goal.qz, goal.qw) == pytest.approx(1.0)


def test_from_yaw_still_builds_a_valid_canonical_goal() -> None:
    goal = CanonicalGoal.from_yaw(
        frame_id=" /map ",
        x=1.0,
        y=2.0,
        yaw=3.0 * math.pi,
    )

    assert goal.frame_id == "map"
    assert goal.yaw == pytest.approx(math.pi)
    assert math.hypot(goal.qx, goal.qy, goal.qz, goal.qw) == pytest.approx(1.0)


def test_direct_validation_rejects_yaw_quaternion_disagreement() -> None:
    with pytest.raises(
        ValidationError, match="yaw and quaternion must describe the same orientation"
    ):
        CanonicalGoal(
            frame_id="map",
            x=1.0,
            y=2.0,
            yaw=math.pi / 2.0,
            qx=0.0,
            qy=0.0,
            qz=0.0,
            qw=1.0,
        )
