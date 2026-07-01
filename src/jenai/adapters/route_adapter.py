from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass
class RouteSendResult:
    execution_status: str
    detail: str


class RouteAdapter(Protocol):
    def resolve(self, outgoing_action: dict) -> RouteSendResult: ...


class StubRouteAdapter:
    """No real navigation stack is available in v0.1.0.

    Logs the intended action and returns a synthetic success result rather
    than calling anything external.
    """

    def resolve(self, outgoing_action: dict) -> RouteSendResult:
        logger.info("StubRouteAdapter: would send route action: %s", outgoing_action)
        return RouteSendResult(execution_status="sent (stub)", detail=str(outgoing_action))


def get_route_adapter(adapter_name: str) -> RouteAdapter:
    if adapter_name == "stub":
        return StubRouteAdapter()
    raise NotImplementedError(f"Route adapter '{adapter_name}' is not implemented in v0.1.0.")
