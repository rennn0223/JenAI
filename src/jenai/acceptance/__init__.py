"""Operator-triggered acceptance runners for live robot environments."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jenai.acceptance.isaac_hil import (
        EXECUTION_CONFIRMATION,
        IsaacHilOptions,
        run_isaac_hil,
    )

__all__ = ["EXECUTION_CONFIRMATION", "IsaacHilOptions", "run_isaac_hil"]


def __getattr__(name: str) -> Any:
    """Load the legacy HIL convenience exports only when callers request them."""

    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from jenai.acceptance.isaac_hil import (
        EXECUTION_CONFIRMATION,
        IsaacHilOptions,
        run_isaac_hil,
    )

    exports = {
        "EXECUTION_CONFIRMATION": EXECUTION_CONFIRMATION,
        "IsaacHilOptions": IsaacHilOptions,
        "run_isaac_hil": run_isaac_hil,
    }
    globals().update(exports)
    return exports[name]
