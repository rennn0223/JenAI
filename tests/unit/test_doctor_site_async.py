from __future__ import annotations

import asyncio
from types import SimpleNamespace


def test_sync_site_probe_is_safe_inside_an_existing_event_loop(monkeypatch) -> None:
    from jenai.doctor import site

    async def read_identity():
        return SimpleNamespace(digest="a" * 64, frame_id="map")

    monkeypatch.setattr(site, "_read_active_map_identity_async", read_identity)

    async def scenario():
        return site._read_active_map_identity()

    observed = asyncio.run(scenario())

    assert observed.digest == "a" * 64
