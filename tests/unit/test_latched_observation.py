from __future__ import annotations

import threading
import time

import pytest

from jenai.bridge._latched_observation import LatchedObservation


def test_latched_observation_reuses_latest_sample_without_a_new_callback() -> None:
    observation: LatchedObservation[str] = LatchedObservation()
    observation.observe("map-v1")

    assert observation.wait(0.01) == "map-v1"
    assert observation.wait(0.01) == "map-v1"

    observation.observe("map-v2")
    assert observation.wait(0.01) == "map-v2"


def test_latched_observation_waits_for_first_sample_and_times_out() -> None:
    observation: LatchedObservation[str] = LatchedObservation()

    def publish_later() -> None:
        time.sleep(0.01)
        observation.observe("map")

    publisher = threading.Thread(target=publish_later)
    publisher.start()
    assert observation.wait(0.2) == "map"
    publisher.join()

    empty: LatchedObservation[str] = LatchedObservation()
    with pytest.raises(TimeoutError, match="No observation received"):
        empty.wait(0.01)
