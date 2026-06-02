"""ePIN (Bavaria) source client — Bayerisches Landesamt für Gesundheit (LGL).

ePIN ("elektronisches Polleninformations-Netzwerk") is Bavaria's automatic
pollen network. It exposes a keyless JSON REST API:

- ``GET /api/measurements`` -> ``{from, to, measurements: [...]}`` where each
  record is ``{polle, location, data: [{from, to, value, algorithm}]}``.
  ``value`` is a pollen concentration in grains/m³ (float), in 3-hourly windows
  over roughly the last week.

The data may be used freely in any medium (LGL open data); we cite the source.

Design mirrors the other sources: pure ``parse()`` + injectable transport,
HA-free, ``python -m`` entry. Like DWD/MeteoSwiss it is a regional source decided
by a bounding box, and like MeteoSwiss it is *station-based* (nearest-station
auto-pick). Differences from the forecast sources:

- **Automatic stations only.** The network also has 4 manually-read stations
  ("ePIN Manu"); those run at weekly latency, so mixing them into a picker that
  promises real-time 3-hourly data would be misleading — they are excluded.
- **Observation-only.** No forward forecast — just the recent window + now.
- **Recorder-baselined percentile.** Only ~7 days are exposed, far short of the
  baseline window, so recent_percentile baselines on HA recorder history (it
  reports "insufficient_history" until enough days accrue), unlike MeteoSwiss.
- **Concentration scale.** grains/m³ -> analytics buckets it via ``bucket_level``;
  no collapse function. Canonical overlap is 5/6 (no olive, like DWD).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .base import (
    ALLERGENS,
    AllergenSeries,
    SourceResponseError,
    SourceResult,
    SourceStatus,
    SourceUnavailable,
    _haversine_km,
)

if TYPE_CHECKING:
    import aiohttp

SOURCE_NAME = "epin"
API_URL = "https://epin.lgl.bayern.de/api/measurements"

# Bavaria's bounding box (coarse). Used only as an in/out coverage test, like
# DWD's GERMANY_BBOX; over-inclusion at the edge is benign (nearest-station then
# assigns the closest station).
BAVARIA_BBOX = (47.27, 50.56, 8.98, 13.84)  # lat_min, lat_max, lon_min, lon_max

# ePIN Latin taxon name -> canonical key. ePIN reports 41 taxa; v2.0+ maps 20
# of them (the HIGH + MODERATE allergenic species per the locked matrix). The
# remaining 21 ePIN taxa are LOW or NEGLIGIBLE potency (pine, spruce, fir,
# larch, poplar, willow, maple, lime, etc.) and are deliberately dropped to
# avoid misleading severity readings from non-allergenic high-count taxa.
# Note: ePIN distinguishes "Quercus" (deciduous oak) from "Quercus ilex" (holm
# oak / evergreen Mediterranean) — kept separate, different clinical profiles.
_LATIN_TO_ALLERGEN: dict[str, str] = {
    "Alnus": "alder",
    "Betula": "birch",
    "Corylus": "hazel",
    "Poaceae": "grass",
    "Secale": "rye",
    "Artemisia": "mugwort",
    "Ambrosia": "ragweed",
    "Plantago": "plantago",
    "Urtica": "urtica",
    "Fraxinus": "ash",
    "Quercus": "oak",
    "Quercus ilex": "holm_oak",
    "Fagus": "beech",
    "Ulmus": "elm",
    "Carpinus": "carpinus",
    "Platanus": "plane_tree",
    "Juglans": "juglans",
    "Chenopodium": "chenopodium",
    "Rumex": "rumex",
    "Asteraceae": "asteraceae",
}

# Automatic real-time stations only (network == "ePIN"): id -> (name, lat, lon).
# Captured from /api/locations (the 4 "ePIN Manu" manual stations are excluded).
STATIONS: dict[str, tuple[str, float, float]] = {
    "DEALTO": ("Altötting", 48.23, 12.68),
    "DEFEUC": ("Feucht", 49.38, 11.2),
    "DEGARM": ("Garmisch-Partenkirchen", 47.48, 11.06),
    "DEHOF": ("Hof", 50.32, 11.9),
    "DEMARK": ("Marktheidenfeld", 49.85, 9.62),
    "DEMIND": ("Mindelheim", 48.04, 10.49),
    "DEMUNC": ("München", 48.13, 11.56),
    "DEVIEC": ("Viechtach", 49.08, 12.87),
}

AsyncTransport = Callable[[str, float], Awaitable["tuple[int, Any]"]]
Transport = Callable[[str, float], "tuple[int, Any]"]


def _async_retryable_exceptions() -> tuple[type[BaseException], ...]:
    retryable: tuple[type[BaseException], ...] = (asyncio.TimeoutError, OSError)
    try:
        import aiohttp
    except ImportError:
        return retryable
    return (*retryable, aiohttp.ClientError)


def nearest_station(latitude: float, longitude: float) -> str:
    """Id of the automatic station closest to the coordinates (great-circle)."""
    return min(
        STATIONS,
        key=lambda code: _haversine_km(
            latitude, longitude, STATIONS[code][1], STATIONS[code][2]
        ),
    )


def _to_iso(unix_ts: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(unix_ts), UTC).isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        return None


class EpinSource:
    """Fetches and normalises one ePIN station's recent pollen measurements."""

    name = SOURCE_NAME
    supports_history = True
    provides_history_series = False  # ~7 days only -> recorder-baselined

    def __init__(
        self,
        latitude: float,
        longitude: float,
        allergens: Iterable[str] | None = None,
        *,
        station: str | None = None,
        timeout: float = 30.0,
        retry_delay: float = 1.0,
        transport: Transport | None = None,
        async_transport: AsyncTransport | None = None,
    ) -> None:
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.allergens = self._validate_allergens(allergens)
        self.station = station if station in STATIONS else (
            nearest_station(self.latitude, self.longitude)
            if self._in_bavaria()
            else None
        )
        self.timeout = timeout
        self.retry_delay = retry_delay
        self._transport = transport or _http_get_json
        self._async_transport = async_transport

    @staticmethod
    def _validate_allergens(allergens: Iterable[str] | None) -> list[str]:
        covered = set(_LATIN_TO_ALLERGEN.values())
        if allergens is None:
            return [a for a in ALLERGENS if a in covered]
        return [a for a in allergens if a in covered]

    def _in_bavaria(self) -> bool:
        lat_min, lat_max, lon_min, lon_max = BAVARIA_BBOX
        return (
            lat_min <= self.latitude <= lat_max
            and lon_min <= self.longitude <= lon_max
        )

    def _station_label(self) -> str | None:
        if self.station is None:
            return None
        return f"{STATIONS[self.station][0]} ({self.station})"

    # -- fetching ------------------------------------------------------------

    async def async_fetch(
        self, session: aiohttp.ClientSession | None = None
    ) -> SourceResult:
        """Fetch and normalise. Locations outside Bavaria short-circuit to
        out-of-coverage without hitting the network."""
        if not self._in_bavaria() or self.station is None:
            return self._coverage_result()

        if self._async_transport is not None:
            return await self._fetch_with(self._async_transport)

        import aiohttp

        owns_session = session is None
        if owns_session:
            session = aiohttp.ClientSession()
        try:
            return await self._fetch_with(
                self._make_aiohttp_transport(aiohttp, session)
            )
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
                    f"ePIN request failed after {attempts} attempts: {err}"
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

    def fetch_sync(self) -> SourceResult:
        """Synchronous fetch for the standalone probe / offline validation."""
        if not self._in_bavaria() or self.station is None:
            return self._coverage_result()
        attempts = 2
        for attempt in range(attempts):
            try:
                status, payload = self._transport(API_URL, self.timeout)
                break
            except OSError as err:
                if attempt + 1 < attempts:
                    time.sleep(self.retry_delay)
                    continue
                raise SourceUnavailable(
                    f"ePIN request failed after {attempts} attempts: {err}"
                ) from err
        return self._handle_response(status, payload)

    def _handle_response(self, status: int, payload: Any) -> SourceResult:
        if status != 200:
            raise SourceResponseError(f"ePIN returned unexpected HTTP {status}.")
        if not isinstance(payload, dict) or "measurements" not in payload:
            raise SourceResponseError("ePIN response missing 'measurements'.")
        return self.parse(payload)

    def _coverage_result(self) -> SourceResult:
        return SourceResult(
            source=SOURCE_NAME,
            status=SourceStatus.OUT_OF_COVERAGE,
            requested_lat=self.latitude,
            requested_lon=self.longitude,
            generated_at=_now_iso(),
            message="Location is outside ePIN (Bavaria) coverage.",
        )

    # -- parsing (pure) ------------------------------------------------------

    def parse(self, payload: dict[str, Any]) -> SourceResult:
        """Extract this station's recent series into a SourceResult.

        Records for the chosen station are matched by ``location``; each wanted
        taxon's 3-hourly windows are aligned onto a shared time axis (the union
        of window starts). ``current`` is the latest window's value. Observation
        only — no forecast tail; recent_percentile is recorder-baselined.
        """
        station_lat = STATIONS[self.station][1] if self.station else None
        station_lon = STATIONS[self.station][2] if self.station else None
        records = [
            m
            for m in payload.get("measurements", [])
            if m.get("location") == self.station
            and _LATIN_TO_ALLERGEN.get(m.get("polle")) in self.allergens
        ]

        # Per-allergen {timestamp -> value}, and the union of all timestamps.
        by_allergen: dict[str, dict[str, float]] = {}
        all_times: set[str] = set()
        for record in records:
            canon = _LATIN_TO_ALLERGEN[record["polle"]]
            point_map = by_allergen.setdefault(canon, {})
            for point in record.get("data") or []:
                iso = _to_iso(point.get("from"))
                value = point.get("value")
                if iso is None or value is None:
                    continue
                point_map[iso] = float(value)
                all_times.add(iso)

        times = sorted(all_times)
        allergens: dict[str, AllergenSeries] = {}
        for canon, point_map in by_allergen.items():
            values: list[float | None] = [point_map.get(t) for t in times]
            current = next((v for v in reversed(values) if v is not None), None)
            allergens[canon] = AllergenSeries(
                allergen=canon,
                unit="grains/m³",  # ePIN values are concentrations (no unit in API)
                current=current,
                values=values,
            )

        return SourceResult(
            source=SOURCE_NAME,
            status=SourceStatus.OK,
            requested_lat=self.latitude,
            requested_lon=self.longitude,
            snapped_lat=station_lat,
            snapped_lon=station_lon,
            timezone="UTC",
            times=times,
            current_time=times[-1] if times else None,
            allergens=allergens,
            station=self._station_label(),
            generated_at=_now_iso(),
        )


def _http_get_json(url: str, timeout: float) -> tuple[int, Any]:
    """Default synchronous transport built on the standard library."""
    req = urllib.request.Request(url, headers={"User-Agent": "PollenWatch/1.1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        return err.code, None
    # urllib.error.URLError (an OSError) and timeouts propagate for retry.


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# -- standalone entry point --------------------------------------------------


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe ePIN pollen for a location (nearest Bavarian station)."
    )
    parser.add_argument("--lat", type=float, default=48.13, help="latitude")
    parser.add_argument("--lon", type=float, default=11.56, help="longitude")
    args = parser.parse_args(argv)

    source = EpinSource(args.lat, args.lon)
    if source.station is None:
        print("Location is outside Bavaria — ePIN out of coverage.")
        return 0
    print(f"GET {API_URL}  (station {source._station_label()})\n")
    result = source.fetch_sync()
    print(f"PollenWatch · ePIN — status: {result.status.value}")
    print(f"  station:   {result.station}")
    print(f"  windows:   {len(result.times)} (latest {result.current_time})")
    for canon, s in result.allergens.items():
        nums = [v for v in s.values if v is not None]
        cur = "n/a" if s.current is None else f"{s.current:.1f}"
        hi = f"{max(nums):.1f}" if nums else "n/a"
        print(f"  {canon:<7} current={cur:>6}  max={hi:>6}  [{s.unit}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
