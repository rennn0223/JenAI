"""Active Site Profile verification for ``JenAI doctor``."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from jenai.bridge import BridgeError, MapIdentityInfo, RosBridgeClient
from jenai.config.models import AppConfig
from jenai.schemas import DoctorCheckItem, DoctorStatus


async def _read_active_map_identity_async() -> MapIdentityInfo:
    """Read one map fingerprint while owning and closing the sidecar process."""
    bridge = RosBridgeClient()
    try:
        return await bridge.map_identity(timeout=3.0)
    except BridgeError:
        raise
    except Exception as exc:
        raise BridgeError(f"map identity probe failed: {exc}") from exc
    finally:
        await bridge.stop()


def _read_active_map_identity() -> MapIdentityInfo:
    """Run the probe in its own loop, even when the caller already has one.

    The CLI calls doctor synchronously, while the HIL runner calls it from an
    active asyncio loop. A dedicated worker gives both paths one safe adapter.
    """
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="jenai-doctor") as pool:
        return pool.submit(lambda: asyncio.run(_read_active_map_identity_async())).result()


def check_site(config: AppConfig | None) -> list[DoctorCheckItem]:
    """Fail closed when an active Site Profile does not match the live map."""
    if config is None or not config.site.active:
        return []

    site = config.site
    expected = site.map_sha256
    if expected is None:
        return [
            DoctorCheckItem(
                section="site",
                check_name="map_identity",
                status=DoctorStatus.FAIL,
                message=f"Active site '{site.display_name}' has no validated map identity.",
                fix_suggestion=(
                    "Validate the site map and set [site] map_sha256 before navigation."
                ),
            )
        ]

    try:
        observed = _read_active_map_identity()
    except BridgeError as exc:
        return [
            DoctorCheckItem(
                section="site",
                check_name="map_identity",
                status=DoctorStatus.FAIL,
                message=(f"Could not verify the active map for site '{site.display_name}': {exc}"),
                fix_suggestion=(
                    "Start the validated map server and localization, then run /doctor again."
                ),
            )
        ]

    if observed.frame_id != site.map_frame:
        return [
            DoctorCheckItem(
                section="site",
                check_name="map_identity",
                status=DoctorStatus.FAIL,
                message=(
                    f"Map frame mismatch for site '{site.display_name}': expected "
                    f"'{site.map_frame}', observed '{observed.frame_id}'."
                ),
                fix_suggestion=(
                    "Activate the correct Site Profile or publish the validated map frame."
                ),
            )
        ]
    if observed.digest != expected:
        return [
            DoctorCheckItem(
                section="site",
                check_name="map_identity",
                status=DoctorStatus.FAIL,
                message=(
                    f"Map identity mismatch for site '{site.display_name}': expected "
                    f"{expected[:12]}, observed {observed.digest[:12]}."
                ),
                fix_suggestion=(
                    "Activate the correct Site Profile; revalidate coordinates before updating "
                    "the stored fingerprint."
                ),
            )
        ]

    return [
        DoctorCheckItem(
            section="site",
            check_name="map_identity",
            status=DoctorStatus.PASS,
            message=(
                f"Active site '{site.display_name}' matches map {observed.digest[:12]} "
                f"in frame '{observed.frame_id}'."
            ),
        )
    ]
