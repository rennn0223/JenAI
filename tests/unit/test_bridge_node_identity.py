from __future__ import annotations

import pytest

from jenai.bridge._node_identity import bridge_node_name


def test_bridge_node_name_is_process_unique_and_ros_safe() -> None:
    assert bridge_node_name(12345) == "jenai_bridge_12345"


@pytest.mark.parametrize("pid", [0, -1])
def test_bridge_node_name_rejects_non_process_ids(pid: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        bridge_node_name(pid)
