"""JenAI package."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: read the version from the installed package
    # metadata (populated from pyproject.toml) so the UI never drifts from the
    # real release version.
    __version__ = version("jenai")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+dev"
