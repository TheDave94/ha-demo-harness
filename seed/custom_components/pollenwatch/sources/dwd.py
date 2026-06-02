"""DWD Pollenflug source client (Deutscher Wetterdienst, Germany only).

DWD publishes an open, keyless pollen-hazard-index forecast (s31fg.json): a
7-point ordinal scale ("0","0-1","1","1-2","2","2-3","3", plus "-1" = no data),
3 days, per *region* (no lat/lon in the feed). See ANALYTICS.md / MILESTONE
notes for the probe findings.

Design mirrors the other sources: pure ``parse()`` + injectable transport,
HA-free, ``python -m`` entry. Differences:
- **Region-based.** The caller supplies a DWD ``partregion_id``; the client
  fetches the (single) feed and extracts that region. Coverage is decided by a
  Germany bounding box on the configured lat/lon — outside Germany →
  ``out_of_coverage`` (no fetch).
- **Native scale.** Values are kept numeric for the raw sensor (0–3 with .5
  half-steps) AND the native string ("2-3") is preserved on the series so the
  analytics layer can map by meaning via ``analytics.dwd_collapse``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .base import (
    ALLERGENS,
    AllergenSeries,
    SourceResponseError,
    SourceResult,
    SourceStatus,
    SourceUnavailable,
)

if TYPE_CHECKING:
    import aiohttp

API_URL = "https://opendata.dwd.de/climate_environment/health/alerts/s31fg.json"
SOURCE_NAME = "dwd"

# Germany bounding box (coarse). Used only as an in/out coverage test — its
# benign failure mode is offering DWD to someone just outside the border.
GERMANY_BBOX = (47.27, 55.06, 5.87, 15.04)  # lat_min, lat_max, lon_min, lon_max

# DWD species name -> PollenWatch canonical key. The s31fg.json feed reports 8
# species total (DWD has no olive); all 8 mapped from v2.0+. rye = Secale
# (cereal rye, broken out by DWD because of historical agricultural exposure
# tracking in German cereal regions).
DWD_SPECIES_TO_ALLERGEN: dict[str, str] = {
    "Erle": "alder",
    "Birke": "birch",
    "Graeser": "grass",
    "Beifuss": "mugwort",
    "Ambrosia": "ragweed",
    "Hasel": "hazel",
    "Esche": "ash",
    "Roggen": "rye",
}

# DWD 7-point string -> numeric 0–3 (half-steps as .5). "-1"/unknown -> omit.
_STR_TO_FLOAT: dict[str, float] = {
    "0": 0.0,
    "0-1": 0.5,
    "1": 1.0,
    "1-2": 1.5,
    "2": 2.0,
    "2-3": 2.5,
    "3": 3.0,
}

_DAY_KEYS = ("today", "tomorrow", "dayafter_to")

AsyncTransport = Callable[[str, float], Awaitable["tuple[int, Any]"]]


def _async_retryable_exceptions() -> tuple[type[BaseException], ...]:
    retryable: tuple[type[BaseException], ...] = (asyncio.TimeoutError, OSError)
    try:
        import aiohttp
    except ImportError:
        return retryable
    return (*retryable, aiohttp.ClientError)


def _dwd_to_float(value: Any) -> float | None:
    if value is None:
        return None
    return _STR_TO_FLOAT.get(str(value).strip())


class DwdSource:
    """Fetches and normalises one DWD region's pollen forecast."""

    name = SOURCE_NAME
    supports_history = True
    provides_history_series = False  # 3-day forecast only -> recorder-baselined

    def __init__(
        self,
        latitude: float,
        longitude: float,
        region_id: int,
        allergens: Iterable[str] | None = None,
        *,
        timeout: float = 30.0,
        retry_delay: float = 1.0,
        async_transport: AsyncTransport | None = None,
    ) -> None:
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.region_id = int(region_id)
        self.allergens = self._validate_allergens(allergens)
        self.timeout = timeout
        self.retry_delay = retry_delay
        self._async_transport = async_transport

    @staticmethod
    def _validate_allergens(allergens: Iterable[str] | None) -> list[str]:
        covered = set(DWD_SPECIES_TO_ALLERGEN.values())
        if allergens is None:
            return [a for a in ALLERGENS if a in covered]
        return [a for a in allergens if a in covered]

    def _in_germany(self) -> bool:
        lat_min, lat_max, lon_min, lon_max = GERMANY_BBOX
        return (
            lat_min <= self.latitude <= lat_max
            and lon_min <= self.longitude <= lon_max
        )

    # -- fetching ------------------------------------------------------------

    async def async_fetch(
        self, session: aiohttp.ClientSession | None = None
    ) -> SourceResult:
        """Fetch and normalise. Non-German locations short-circuit to
        out-of-coverage without hitting the feed."""
        if not self._in_germany():
            return SourceResult(
                source=SOURCE_NAME,
                status=SourceStatus.OUT_OF_COVERAGE,
                requested_lat=self.latitude,
                requested_lon=self.longitude,
                generated_at=_now_iso(),
                message="Location is outside DWD (Germany) coverage.",
            )

        if self._async_transport is not None:
            return await self._fetch_with(self._async_transport)

        import aiohttp

        owns_session = session is None
        if owns_session:
            session = aiohttp.ClientSession()
        try:
            return await self._fetch_with(self._make_aiohttp_transport(aiohttp, session))
        finally:
            if owns_session:
                await session.close()

    async def _fetch_with(self, transport: AsyncTransport) -> SourceResult:
        retryable = _async_retryable_exceptions()
        attempts = 2
        for attempt in range(attempts):
            try:
                status, payload = await transport(API_URL, self.timeout)
                break
            except retryable as err:
                if attempt + 1 < attempts:
                    await asyncio.sleep(self.retry_delay)
                    continue
                raise SourceUnavailable(
                    f"DWD request failed after {attempts} attempts: {err}"
                ) from err
        return self._handle_response(status, payload)

    def _make_aiohttp_transport(
        self, aiohttp_mod: Any, session: aiohttp.ClientSession
    ) -> AsyncTransport:
        async def transport(url: str, timeout: float) -> tuple[int, Any]:
            client_timeout = aiohttp_mod.ClientTimeout(total=timeout)
            async with session.get(url, timeout=client_timeout) as resp:
                text = await resp.text()
                return resp.status, json.loads(text)

        return transport

    def _handle_response(self, status: int, payload: Any) -> SourceResult:
        if status != 200:
            raise SourceResponseError(f"DWD returned unexpected HTTP {status}.")
        if not isinstance(payload, dict) or "content" not in payload:
            raise SourceResponseError("DWD response missing 'content'.")
        return self.parse(payload)

    # -- parsing (pure) ------------------------------------------------------

    def parse(self, payload: dict[str, Any]) -> SourceResult:
        """Extract this region's forecast into a SourceResult.

        Values are numeric (0–3, .5 half-steps) with the native string kept on
        each series; an allergen whose ``today`` value is no-data ("-1") or
        unexpected is omitted. The feed carries no dates, so the 3 days are
        labelled today + the next two (UTC).
        """
        region = next(
            (
                r
                for r in payload.get("content", [])
                if r.get("partregion_id") == self.region_id
            ),
            None,
        )
        if region is None:
            return SourceResult(
                source=SOURCE_NAME,
                status=SourceStatus.OUT_OF_COVERAGE,
                requested_lat=self.latitude,
                requested_lon=self.longitude,
                generated_at=_now_iso(),
                message=f"DWD region {self.region_id} not found in feed.",
            )

        base = datetime.now(UTC).date()
        times = [(base + timedelta(days=i)).isoformat() for i in range(len(_DAY_KEYS))]
        pollen = region.get("Pollen") or {}

        allergens: dict[str, AllergenSeries] = {}
        for dwd_species, canon in DWD_SPECIES_TO_ALLERGEN.items():
            if canon not in self.allergens:
                continue
            days = pollen.get(dwd_species)
            if not days:
                continue
            values = [_dwd_to_float(days.get(k)) for k in _DAY_KEYS]
            if values[0] is None:
                continue  # today is no-data/unexpected -> omit this allergen
            native_raw = days.get(_DAY_KEYS[0])
            allergens[canon] = AllergenSeries(
                allergen=canon,
                unit=None,  # 0–3 ordinal index, not a concentration
                current=values[0],
                values=values,
                native=str(native_raw).strip() if native_raw is not None else None,
            )

        return SourceResult(
            source=SOURCE_NAME,
            status=SourceStatus.OK,
            requested_lat=self.latitude,
            requested_lon=self.longitude,
            timezone=None,
            elevation=None,
            times=times,
            current_time=times[0],
            allergens=allergens,
            generated_at=_now_iso(),
        )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
