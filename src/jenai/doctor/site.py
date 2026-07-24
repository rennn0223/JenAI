"""Active Site Profile verification for ``JenAI doctor``."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jenai.bridge import BridgeError, MapIdentityInfo, RosBridgeClient
from jenai.config.models import AppConfig
from jenai.schemas import DoctorCheckItem, DoctorStatus
from jenai.site_assets import SiteAssetError, validate_site_assets


async def _read_active_map_identity_async() -> MapIdentityInfo:
    """Read one map fingerprint while owning and closing the sidecar process."""
    bridge = RosBridgeClient()
    try:
        for attempt in range(2):
            try:
                return await bridge.map_identity(timeout=5.0)
            except BridgeError:
                if attempt == 1:
                    raise
                await asyncio.sleep(0.2)
        raise AssertionError("bounded map identity loop did not return or raise")
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


def read_live_map_identity() -> MapIdentityInfo:
    """Read the current ROS occupancy-map identity without requiring an active site."""

    return _read_active_map_identity()


def _check_site_map(config: AppConfig | None) -> list[DoctorCheckItem]:
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


def check_site(
    config: AppConfig | None,
    config_path: Path | None = None,
) -> list[DoctorCheckItem]:
    """Check both the live map identity and every bound Site Profile asset."""

    items = _check_site_map(config)
    if config is None or not config.site.active:
        return items
    if not config.site.execution_ready:
        items.append(
            DoctorCheckItem(
                section="site",
                check_name="execution_trust",
                status=DoctorStatus.FAIL,
                message=(
                    f"Active site '{config.site.display_name}' is selected but not execution-ready."
                ),
                fix_suggestion=(
                    "With the validated map active, run "
                    "'JenAI site validate --repair' to bind current locations explicitly."
                ),
            )
        )
        return items
    if config_path is None:
        items.append(
            DoctorCheckItem(
                section="site",
                check_name="locations_identity",
                status=DoctorStatus.FAIL,
                message="The active Site Profile locations cannot be verified without config_path.",
                fix_suggestion=("Run JenAI doctor with the same config file used to launch JenAI."),
            )
        )
        return items
    try:
        locations = validate_site_assets(config, config_path)
    except SiteAssetError as exc:
        items.append(
            DoctorCheckItem(
                section="site",
                check_name="locations_identity",
                status=DoctorStatus.FAIL,
                message=str(exc),
                fix_suggestion="Revalidate and reactivate this Site Profile before navigation.",
            )
        )
    else:
        digest = config.site.locations_sha256
        items.append(
            DoctorCheckItem(
                section="site",
                check_name="locations_identity",
                status=DoctorStatus.PASS,
                message=(
                    f"Active site binds {len(locations)} validated location(s) to "
                    f"{digest[:12] if digest else 'missing'}."
                ),
            )
        )
    return items
