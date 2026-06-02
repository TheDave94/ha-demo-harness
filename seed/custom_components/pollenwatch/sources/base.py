"""Source-layer primitives shared by all PollenWatch data sources.

This module is deliberately free of any Home Assistant imports so the data
layer can be developed and tested in isolation (see ``open_meteo.py`` for a
standalone ``python -m`` entry point).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

# Canonical allergen keys understood by the source layer. Individual sources
# expose a subset of these. The analytics layer reconciles sources by this key.
ALLERGENS: tuple[str, ...] = (
    "alder",
    "birch",
    "grass",
    "mugwort",
    "olive",
    "ragweed",
)


class SourceStatus(StrEnum):
    """Outcome of a source fetch that did not raise."""

    OK = "ok"
    OUT_OF_COVERAGE = "out_of_coverage"


class SourceError(Exception):
    """Base class for source-layer errors."""


class SourceUnavailable(SourceError):
    """Transport failed (network/timeout/5xx) and retries were exhausted.

    Distinct from :class:`SourceResponseError`: the request never produced a
    usable response, so a later retry may succeed.
    """


class SourceResponseError(SourceError):
    """The source returned a response we could not interpret.

    Used for malformed payloads or error responses that do not correspond to a
    recognised, expected condition (e.g. out-of-coverage). These usually signal
    a bug or an upstream change rather than a transient failure.
    """


class SourceAuthError(SourceError):
    """The source rejected the credentials (e.g. invalid/expired API key).

    Distinct from the transient/coverage errors: it requires the user to fix
    the credential, so it should drive a re-auth / repair flow.
    """


@dataclass(slots=True)
class AllergenSeries:
    """A single allergen's values for one source result.

    ``values`` is aligned positionally to :attr:`SourceResult.times`. Entries
    may be ``None`` where the source has no value (e.g. the tail of the
    forecast horizon). Out-of-season values are typically ``0.0``, not ``None``.
    """

    allergen: str
    unit: str | None
    current: float | None
    values: list[float | None] = field(default_factory=list)
    # Source's native categorical value, when it isn't a plain number (e.g. DWD's
    # "2-3" 7-point string). Carried so analytics can map by the native scale
    # while the raw sensor still shows a numeric value. None for numeric sources.
    native: str | None = None


@dataclass(slots=True)
class SourceResult:
    """Normalised result from a single source fetch.

    ``times`` holds the shared hourly time axis (past days + forecast). Each
    allergen in :attr:`allergens` carries a ``values`` list aligned to it.
    """

    source: str
    status: SourceStatus
    requested_lat: float
    requested_lon: float
    snapped_lat: float | None = None
    snapped_lon: float | None = None
    timezone: str | None = None
    elevation: float | None = None
    times: list[str] = field(default_factory=list)
    current_time: str | None = None
    allergens: dict[str, AllergenSeries] = field(default_factory=dict)
    generated_at: str | None = None
    message: str | None = None
    # Station-based sources (MeteoSwiss, ePIN) auto-pick the nearest measuring
    # station and report it here for display. ``snapped_lat/lon`` carry the
    # station's coordinates so ``coordinate_shift_km`` is the distance to it.
    station: str | None = None

    @property
    def ok(self) -> bool:
        """True when usable allergen data was returned."""
        return self.status is SourceStatus.OK

    @property
    def coordinate_shift_km(self) -> float | None:
        """Great-circle distance between requested and snapped coordinates.

        Open-Meteo snaps coordinates to a ~0.1° grid; surfacing the shift helps
        users understand why their location is not exact. ``None`` when snapped
        coordinates are unavailable.
        """
        if self.snapped_lat is None or self.snapped_lon is None:
            return None
        return _haversine_km(
            self.requested_lat,
            self.requested_lon,
            self.snapped_lat,
            self.snapped_lon,
        )

    @property
    def forecast_split(self) -> int:
        """Index in :attr:`times` where the forecast begins.

        Entries before this index are past/backfill data; entries from this
        index onward are the current hour and forecast. Falls back to ``0`` when
        the current hour cannot be located in the time axis.
        """
        if self.current_time is not None:
            try:
                return self.times.index(self.current_time)
            except ValueError:
                pass
        return 0


@runtime_checkable
class PollenSource(Protocol):
    """Structural interface every source client satisfies.

    Lets the per-source coordinator stay source-agnostic. ``session`` is typed
    loosely (``Any``) so this module need not import aiohttp.

    Two capability flags drive the analytics layer (see the analytics
    coordinator). Keeping them on the source means a future source that may not
    store data (e.g. one whose terms forbid caching) is a clean drop-in:

    - ``supports_history`` — may a recent_percentile baseline be derived/persisted
      for this source at all? ``False`` means no recent_percentile sensor and no
      recorder use (for a source whose licence prohibits storing results).
    - ``provides_history_series`` — does :meth:`async_fetch` return its own
      multi-day history series? ``True`` → self-baseline the percentile from that
      series (Open-Meteo backfill, MeteoSwiss recent CSV). ``False`` (but
      ``supports_history``) → baseline on Home Assistant recorder history.
    """

    name: str
    allergens: list[str]
    supports_history: bool
    provides_history_series: bool

    async def async_fetch(self, session: Any = None) -> SourceResult: ...


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))
