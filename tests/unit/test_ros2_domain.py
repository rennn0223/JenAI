from __future__ import annotations

from jenai.adapters import ros2_adapter


def test_ros_cli_environment_always_has_a_canonical_domain(monkeypatch) -> None:
    monkeypatch.delenv("ROS_DOMAIN_ID", raising=False)
    assert ros2_adapter._ros_env(None)["ROS_DOMAIN_ID"] == "0"

    monkeypatch.setenv("ROS_DOMAIN_ID", "20")
    assert ros2_adapter._ros_env(None)["ROS_DOMAIN_ID"] == "20"
    assert ros2_adapter._ros_env(0)["ROS_DOMAIN_ID"] == "0"
