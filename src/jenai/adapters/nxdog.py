"""Typed, read-only observations from the NXDog example HTTP backend."""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

NXDOG_API_URL_ENV = "JENAI_NXDOG_API_URL"
_MAX_RESPONSE_BYTES = 64 * 1024
_READ_ONLY_ENDPOINTS = (
    "/nav_health",
    "/get_ready_flag",
    "/current_map",
    "/odom",
    "/velocity",
    "/is_charging",
)


class NXDogObservationError(Exception):
    """Base exception for NXDog observation failures."""


class NXDogConfigurationError(NXDogObservationError):
    """Raised when the observation adapter is not safely configured."""


class NXDogTransportError(NXDogObservationError):
    """Raised when a read-only HTTP request cannot return bounded JSON."""


class NXDogPayloadError(NXDogObservationError):
    """Raised when a vendor response does not match the documented shape."""


class NXDogFailureKind(StrEnum):
    """Stable failure categories preserved in a partial observation."""

    TRANSPORT = "transport"
    INVALID_PAYLOAD = "invalid_payload"


class NXDogPoseObservation(BaseModel):
    """Vendor-reported 2D pose and map labels without a source timestamp."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    yaw: float = Field(allow_inf_nan=False)
    map_name: str | None = None
    map_tile: str | None = None


class NXDogVelocityObservation(BaseModel):
    """Vendor-reported planar velocity without a source timestamp."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    vx: float = Field(allow_inf_nan=False)
    vy: float = Field(allow_inf_nan=False)
    wz: float = Field(allow_inf_nan=False)


class NXDogEndpointFailure(BaseModel):
    """One endpoint that could not contribute evidence to a snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    endpoint: str
    kind: NXDogFailureKind
    message: str


class NXDogObservation(BaseModel):
    """One best-effort snapshot from all documented read-only endpoints.

    Missing fields are distinguishable from valid ``false`` values through
    ``failures``. The vendor example does not expose source timestamps or a
    cryptographic map digest, so those limitations are explicit invariants.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    captured_at: datetime
    base_url: str
    transport_authenticated: Literal[False] = False
    source_timestamps_available: Literal[False] = False
    cryptographic_map_identity_available: Literal[False] = False
    nav_alive: bool | None = None
    client_ready: bool | None = None
    current_map: str | None = None
    pose: NXDogPoseObservation | None = None
    velocity: NXDogVelocityObservation | None = None
    charging: bool | None = None
    failures: tuple[NXDogEndpointFailure, ...] = Field(default_factory=tuple)

    @property
    def complete(self) -> bool:
        """Return whether every read-only endpoint produced valid evidence."""

        return not self.failures

    def failure_for(self, endpoint: str) -> NXDogEndpointFailure | None:
        """Return the failure for one documented endpoint, if present."""

        return next((item for item in self.failures if item.endpoint == endpoint), None)


class NXDogJsonTransport(Protocol):
    """Internal seam used by the HTTP adapter and deterministic test adapter."""

    def get_json(self, endpoint: str, *, timeout_s: float) -> object: ...


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Prevent allowlisted observations from redirecting to an action endpoint."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None


class UrllibNXDogTransport:
    """Bounded standard-library HTTP adapter for the vendor example server."""

    def __init__(self, base_url: str) -> None:
        self._base_url = _normalize_base_url(base_url)
        self._opener = urllib.request.build_opener(_RejectRedirects())

    def get_json(self, endpoint: str, *, timeout_s: float) -> object:
        if endpoint not in _READ_ONLY_ENDPOINTS:
            raise NXDogTransportError(f"NXDog endpoint is not read-only: {endpoint}")
        request = urllib.request.Request(
            f"{self._base_url}{endpoint}",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=timeout_s) as response:
                payload = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise NXDogTransportError(f"{endpoint} returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise NXDogTransportError(f"{endpoint} request failed: {exc}") from exc

        if len(payload) > _MAX_RESPONSE_BYTES:
            raise NXDogTransportError(f"{endpoint} response exceeded {_MAX_RESPONSE_BYTES} bytes")
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NXDogTransportError(f"{endpoint} did not return valid UTF-8 JSON") from exc


_T = TypeVar("_T")


class NXDogObserver:
    """Collect all safe NXDog evidence behind one small interface."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 3.0,
        transport: NXDogJsonTransport | None = None,
    ) -> None:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise NXDogConfigurationError("NXDog timeout must be a finite positive number")
        self._base_url = _normalize_base_url(base_url)
        self._timeout_s = timeout_s
        self._transport = transport or UrllibNXDogTransport(self._base_url)

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        timeout_s: float = 3.0,
        transport: NXDogJsonTransport | None = None,
    ) -> NXDogObserver:
        """Build the explicit opt-in adapter without storing a robot IP in Git."""

        values = os.environ if environ is None else environ
        base_url = values.get(NXDOG_API_URL_ENV)
        if base_url is None or not base_url.strip():
            raise NXDogConfigurationError(
                f"{NXDOG_API_URL_ENV} is not set; NXDog observation is disabled"
            )
        return cls(base_url, timeout_s=timeout_s, transport=transport)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def uses_https(self) -> bool:
        return urlsplit(self._base_url).scheme == "https"

    def observe(self) -> NXDogObservation:
        """Capture every read-only endpoint concurrently and preserve failures."""

        captures: tuple[tuple[str, Callable[[object], object]], ...] = (
            ("/nav_health", _parse_nav_health),
            ("/get_ready_flag", _parse_ready_flag),
            ("/current_map", _parse_current_map),
            ("/odom", _parse_odom),
            ("/velocity", _parse_velocity),
            ("/is_charging", _parse_charging),
        )
        values: dict[str, object] = {}
        failures: list[NXDogEndpointFailure] = []
        with ThreadPoolExecutor(max_workers=len(captures), thread_name_prefix="nxdog-read") as pool:
            pending = {
                endpoint: pool.submit(self._capture, endpoint, parser)
                for endpoint, parser in captures
            }
            for endpoint, _parser in captures:
                try:
                    values[endpoint] = pending[endpoint].result()
                except NXDogPayloadError as exc:
                    failures.append(
                        NXDogEndpointFailure(
                            endpoint=endpoint,
                            kind=NXDogFailureKind.INVALID_PAYLOAD,
                            message=str(exc),
                        )
                    )
                except NXDogObservationError as exc:
                    failures.append(
                        NXDogEndpointFailure(
                            endpoint=endpoint,
                            kind=NXDogFailureKind.TRANSPORT,
                            message=str(exc),
                        )
                    )
                except Exception as exc:  # Defensive isolation of a test/vendor adapter.
                    failures.append(
                        NXDogEndpointFailure(
                            endpoint=endpoint,
                            kind=NXDogFailureKind.TRANSPORT,
                            message=f"{endpoint} adapter failed: {type(exc).__name__}",
                        )
                    )

        return NXDogObservation(
            captured_at=datetime.now(UTC),
            base_url=self._base_url,
            transport_authenticated=False,
            nav_alive=_typed_value(values, "/nav_health", bool),
            client_ready=_typed_value(values, "/get_ready_flag", bool),
            current_map=_optional_text_value(values, "/current_map"),
            pose=_typed_value(values, "/odom", NXDogPoseObservation),
            velocity=_typed_value(values, "/velocity", NXDogVelocityObservation),
            charging=_typed_value(values, "/is_charging", bool),
            failures=tuple(failures),
        )

    def _capture(
        self,
        endpoint: str,
        parser: Callable[[object], _T],
    ) -> _T:
        raw = self._transport.get_json(endpoint, timeout_s=self._timeout_s)
        return parser(raw)


def _normalize_base_url(base_url: str) -> str:
    stripped = base_url.strip().rstrip("/")
    parts = urlsplit(stripped)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise NXDogConfigurationError("NXDog base URL must be an absolute HTTP(S) URL")
    if parts.username is not None or parts.password is not None:
        raise NXDogConfigurationError("NXDog credentials must not be embedded in the base URL")
    if parts.query or parts.fragment:
        raise NXDogConfigurationError("NXDog base URL must not contain a query or fragment")
    if parts.path not in {"", "/"}:
        raise NXDogConfigurationError("NXDog base URL must point to the server root")
    return stripped


def _require_object(payload: object, endpoint: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise NXDogPayloadError(f"{endpoint} response must be a JSON object")
    return payload


def _require_bool(payload: object, endpoint: str, key: str) -> bool:
    value = _require_object(payload, endpoint).get(key)
    if not isinstance(value, bool):
        raise NXDogPayloadError(f"{endpoint}.{key} must be a boolean")
    return value


def _require_finite_number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NXDogPayloadError(f"{path} must be a number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise NXDogPayloadError(f"{path} must be finite")
    return parsed


def _optional_text(value: object, path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise NXDogPayloadError(f"{path} must be null or a non-empty string")
    return value.strip()


def _parse_nav_health(payload: object) -> bool:
    return _require_bool(payload, "/nav_health", "alive")


def _parse_ready_flag(payload: object) -> bool:
    return _require_bool(payload, "/get_ready_flag", "ready_flag")


def _parse_current_map(payload: object) -> str | None:
    value = _require_object(payload, "/current_map").get("current_map")
    return _optional_text(value, "/current_map.current_map")


def _parse_odom(payload: object) -> NXDogPoseObservation:
    odom = _require_object(payload, "/odom").get("odom")
    if not isinstance(odom, dict):
        raise NXDogPayloadError("/odom.odom must be a JSON object")
    return NXDogPoseObservation(
        x=_require_finite_number(odom.get("x"), "/odom.odom.x"),
        y=_require_finite_number(odom.get("y"), "/odom.odom.y"),
        yaw=_require_finite_number(odom.get("yaw"), "/odom.odom.yaw"),
        map_name=_optional_text(odom.get("map"), "/odom.odom.map"),
        map_tile=_optional_text(odom.get("map_tile"), "/odom.odom.map_tile"),
    )


def _parse_velocity(payload: object) -> NXDogVelocityObservation:
    level = _require_object(payload, "/velocity").get("level")
    if not isinstance(level, list) or len(level) != 3:
        raise NXDogPayloadError("/velocity.level must contain [vx, vy, wz]")
    return NXDogVelocityObservation(
        vx=_require_finite_number(level[0], "/velocity.level[0]"),
        vy=_require_finite_number(level[1], "/velocity.level[1]"),
        wz=_require_finite_number(level[2], "/velocity.level[2]"),
    )


def _parse_charging(payload: object) -> bool:
    return _require_bool(payload, "/is_charging", "is_charging")


def _typed_value[T](values: Mapping[str, object], endpoint: str, expected: type[T]) -> T | None:
    value = values.get(endpoint)
    return value if isinstance(value, expected) else None


def _optional_text_value(values: Mapping[str, object], endpoint: str) -> str | None:
    value = values.get(endpoint)
    return value if isinstance(value, str) else None
