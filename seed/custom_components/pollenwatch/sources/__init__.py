"""PollenWatch data sources.

The source layer is intentionally independent of Home Assistant. Each source
parses a provider's response into the shared :class:`SourceResult` shape defined
in :mod:`.base`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import (
    ALLERGENS,
    AllergenSeries,
    SourceError,
    SourceResponseError,
    SourceResult,
    SourceStatus,
    SourceUnavailable,
)

if TYPE_CHECKING:
    from .open_meteo import OpenMeteoSource

__all__ = [
    "ALLERGENS",
    "AllergenSeries",
    "OpenMeteoSource",
    "SourceError",
    "SourceResponseError",
    "SourceResult",
    "SourceStatus",
    "SourceUnavailable",
]


def __getattr__(name: str) -> object:
    """Lazily expose source clients without importing them at package load.

    Keeps ``from ...sources import OpenMeteoSource`` working while avoiding the
    eager import that would otherwise double-import the module under
    ``python -m ...sources.open_meteo``.
    """
    if name == "OpenMeteoSource":
        from .open_meteo import OpenMeteoSource

        return OpenMeteoSource
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
