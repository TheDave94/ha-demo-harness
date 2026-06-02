"""MeteoSwiss (SwissPollen) source client — Switzerland, keyless.

MeteoSwiss publishes its automatic real-time pollen network as open data on the
``data.geo.admin.ch`` STAC service (collection ``ch.meteoschweiz.ogd-pollen``):
per-station CSV files, hourly average concentrations in pollen grains per m³
(MeteoSwiss labels the unit ``No/m³``). We read the *hourly recent* file, which
spans the current year through the latest published hour.

Design mirrors the other sources: pure ``parse()`` + injectable transport,
HA-free, ``python -m`` entry. Like DWD it is a regional source decided by a
bounding box — but it is *station-based*: the configured lat/lon is auto-snapped
to the nearest measuring station (unlike DWD's manual region pick). Differences
from the forecast sources:

- **Observation-only.** No forward forecast — the series is past → latest hour.
- **Self-baselined percentile.** The recent CSV carries months of history, so
  recent_percentile is computed from the series (no recorder), like Open-Meteo.
- **Concentration scale.** grains/m³ → analytics buckets it via ``bucket_level``;
  no collapse function (unlike DWD/polleninformation).
- **Canonical overlap is alder/birch/grass only.** The automatic Poleno network
  measures 7 taxa (also hazel/beech/ash/oak); only three are in our six. The
  column set is fixed network-wide (verified across northern + southern stations).

Note on time: ``reference_timestamp`` has no zone suffix, but MeteoSwiss defines
its aggregation windows in UTC (see the parameter metadata, "0-0 UTC"), so the
timestamps are treated as UTC. recent_percentile compares a source against
itself, so the absolute zone never biases the ranking.
"""

from __future__ import annotations

import argparse
import asyncio
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

SOURCE_NAME = "meteoswiss"
BASE_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-pollen"

# Switzerland's national bounding box (coarse). Used only as an in/out coverage
# test, like DWD's GERMANY_BBOX. Deliberately the *country* extent rather than
# the tighter station-spread extent, so a valid Swiss location near a border is
# not rejected — the nearest-station pick then assigns it the closest station.
SWITZERLAND_BBOX = (45.80, 47.81, 5.95, 10.50)  # lat_min, lat_max, lon_min, lon_max

# Hourly-column shortname -> canonical key (verified against the OGD parameter
# metadata; "h0" = hourly average). All 7 measured taxa mapped from v2.0+
# (was 3 in v1.x; the additional 4 — hazel/beech/ash/oak — are now in the
# canonical species set).
_CODE_TO_ALLERGEN: dict[str, str] = {
    "kaalnuh0": "alder",  # Alnus
    "kabetuh0": "birch",  # Betula
    "kacoryh0": "hazel",  # Corylus
    "kafaguh0": "beech",  # Fagus
    "kafraxh0": "ash",    # Fraxinus
    "kaquerh0": "oak",    # Quercus
    "khpoach0": "grass",  # Poaceae
}

# Automatic measuring stations: asset id -> (display name, lat, lon). Captured
# from the STAC collection geometry (factual, stable infrastructure) so the
# config flow needs no network call, mirroring DWD_PARTREGIONS.
STATIONS: dict[str, tuple[str, float, float]] = {
    "blr": ("Coldrerio / Mezzana", 45.853561, 8.998167),
    "pbe": ("Bern", 46.950342, 7.424661),
    "pbs": ("Basel", 47.5618, 7.583931),
    "pbu": ("Buchs, SG", 47.173267, 9.472614),
    "pcf": ("La Chaux-de-Fonds", 47.113514, 6.832),
    "pds": ("Davos / Wolfgang", 46.829092, 9.855489),
    "pge": ("Genève", 46.191969, 6.147544),
    "pju": ("Jungfraujoch", 46.547511, 7.985044),
    "plo": ("Locarno / Monti", 46.172547, 8.787389),
    "pls": ("Lausanne", 46.524103, 6.644825),
    "plu": ("Lugano", 46.004231, 8.960631),
    "plz": ("Luzern", 47.057678, 8.296803),
    "pme": ("Meyrin", 46.228894, 6.067308),
    "pmu": ("Münsterlingen", 47.630206, 9.236878),
    "pne": ("Neuchâtel", 47.000269, 6.949828),
    "ppy": ("Payerne", 46.813403, 6.942939),
    "psn": ("Sion", 46.235403, 7.384606),
    "pvi": ("Visp", 46.292094, 7.882928),
    "pzh": ("Zürich", 47.378225, 8.565644),
}

#: ``transport(url, timeout) -> (status_code, csv_text)``. Network failures must
#: raise ``OSError`` so the fetch can retry; an HTTP error response should be
#: returned as ``(code, "")`` so :meth:`_handle_response` can classify it.
AsyncTransport = Callable[[str, float], Awaitable["tuple[int, str]"]]
Transport = Callable[[str, float], "tuple[int, str]"]


def _async_retryable_exceptions() -> tuple[type[BaseException], ...]:
    retryable: tuple[type[BaseException], ...] = (asyncio.TimeoutError, OSError)
    try:
        import aiohttp
    except ImportError:
        return retryable
    return (*retryable, aiohttp.ClientError)


def nearest_station(latitude: float, longitude: float) -> str:
    """Asset id of the station closest to the coordinates (great-circle)."""
    return min(
        STATIONS,
        key=lambda code: _haversine_km(
            latitude, longitude, STATIONS[code][1], STATIONS[code][2]
        ),
    )


def _parse_value(cell: str) -> float | None:
    cell = cell.strip()
    if not cell:
        return None
    try:
        return float(cell)
    except ValueError:
        return None


def _to_iso(timestamp: str) -> str | None:
    """``DD.MM.YYYY HH:MM`` (UTC) -> naive ISO ``YYYY-MM-DDTHH:MM:00``."""
    try:
        return datetime.strptime(timestamp.strip(), "%d.%m.%Y %H:%M").isoformat()
    except ValueError:
        return None


class MeteoSwissSource:
    """Fetches and normalises one MeteoSwiss station's hourly pollen series."""

    name = SOURCE_NAME
    supports_history = True
    provides_history_series = True  # recent CSV carries months -> self-baselined

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
        # Auto-pick the nearest station unless one is pinned (covered locations
        # only; outside Switzerland the station is irrelevant — fetch is skipped).
        self.station = station if station in STATIONS else (
            nearest_station(self.latitude, self.longitude)
            if self._in_switzerland()
            else None
        )
        self.timeout = timeout
        self.retry_delay = retry_delay
        self._transport = transport or _http_get_text
        self._async_transport = async_transport

    @staticmethod
    def _validate_allergens(allergens: Iterable[str] | None) -> list[str]:
        covered = set(_CODE_TO_ALLERGEN.values())
        if allergens is None:
            return [a for a in ALLERGENS if a in covered]
        return [a for a in allergens if a in covered]

    def _in_switzerland(self) -> bool:
        lat_min, lat_max, lon_min, lon_max = SWITZERLAND_BBOX
        return (
            lat_min <= self.latitude <= lat_max
            and lon_min <= self.longitude <= lon_max
        )

    def _url(self) -> str:
        return f"{BASE_URL}/{self.station}/ogd-pollen_{self.station}_h_recent.csv"

    def _station_label(self) -> str | None:
        if self.station is None:
            return None
        return f"{STATIONS[self.station][0]} ({self.station.upper()})"

    # -- fetching ------------------------------------------------------------

    async def async_fetch(
        self, session: aiohttp.ClientSession | None = None
    ) -> SourceResult:
        """Fetch and normalise. Locations outside Switzerland short-circuit to
        out-of-coverage without hitting the network."""
        if not self._in_switzerland() or self.station is None:
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
        url = self._url()
        for attempt in range(attempts):
            try:
                status, text = await transport(url, self.timeout)
                break
            except retryable as err:
                if attempt + 1 < attempts:
                    await asyncio.sleep(self.retry_delay)
                    continue
                raise SourceUnavailable(
                    f"MeteoSwiss request failed after {attempts} attempts: {err}"
                ) from err
        return self._handle_response(status, text)

    def _make_aiohttp_transport(
        self, aiohttp_mod: Any, session: aiohttp.ClientSession
    ) -> AsyncTransport:
        async def transport(url: str, timeout: float) -> tuple[int, str]:
            client_timeout = aiohttp_mod.ClientTimeout(total=timeout)
            async with session.get(url, timeout=client_timeout) as resp:
                return resp.status, await resp.text()

        return transport

    def fetch_sync(self) -> SourceResult:
        """Synchronous fetch for the standalone probe / offline validation."""
        if not self._in_switzerland() or self.station is None:
            return self._coverage_result()
        url = self._url()
        attempts = 2
        for attempt in range(attempts):
            try:
                status, text = self._transport(url, self.timeout)
                break
            except OSError as err:
                if attempt + 1 < attempts:
                    time.sleep(self.retry_delay)
                    continue
                raise SourceUnavailable(
                    f"MeteoSwiss request failed after {attempts} attempts: {err}"
                ) from err
        return self._handle_response(status, text)

    def _handle_response(self, status: int, text: str) -> SourceResult:
        if status != 200:
            raise SourceResponseError(f"MeteoSwiss returned unexpected HTTP {status}.")
        return self.parse(text)

    def _coverage_result(self) -> SourceResult:
        return SourceResult(
            source=SOURCE_NAME,
            status=SourceStatus.OUT_OF_COVERAGE,
            requested_lat=self.latitude,
            requested_lon=self.longitude,
            generated_at=_now_iso(),
            message="Location is outside MeteoSwiss (Switzerland) coverage.",
        )

    # -- parsing (pure) ------------------------------------------------------

    def parse(self, csv_text: str) -> SourceResult:
        """Convert a station's hourly CSV (semicolon-delimited) to a SourceResult.

        Builds the full aligned (times, values) history series so the analytics
        layer can self-baseline recent_percentile; ``current`` is the latest
        published hour. Observation-only: no forecast tail.
        """
        rows = [r for r in csv_text.splitlines() if r.strip()]
        station_lat = STATIONS[self.station][1] if self.station else None
        station_lon = STATIONS[self.station][2] if self.station else None
        if len(rows) < 2:
            # Header only / empty file: covered but no usable data right now.
            return SourceResult(
                source=SOURCE_NAME,
                status=SourceStatus.OK,
                requested_lat=self.latitude,
                requested_lon=self.longitude,
                snapped_lat=station_lat,
                snapped_lon=station_lon,
                station=self._station_label(),
                generated_at=_now_iso(),
            )

        header = [c.strip() for c in rows[0].split(";")]
        # column index -> canonical key, for the wanted taxa present in the header
        col_to_canon: dict[int, str] = {
            i: _CODE_TO_ALLERGEN[code]
            for i, code in enumerate(header)
            if code in _CODE_TO_ALLERGEN and _CODE_TO_ALLERGEN[code] in self.allergens
        }

        times: list[str] = []
        series: dict[str, list[float | None]] = {c: [] for c in col_to_canon.values()}
        for row in rows[1:]:
            cells = row.split(";")
            if len(cells) < 2:
                continue
            iso = _to_iso(cells[1])
            if iso is None:
                continue
            times.append(iso)
            for idx, canon in col_to_canon.items():
                value = _parse_value(cells[idx]) if idx < len(cells) else None
                series[canon].append(value)

        allergens: dict[str, AllergenSeries] = {}
        for canon, values in series.items():
            current = next((v for v in reversed(values) if v is not None), None)
            allergens[canon] = AllergenSeries(
                allergen=canon,
                unit="grains/m³",  # MeteoSwiss labels this "No/m³" (grains per m³)
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


def _http_get_text(url: str, timeout: float) -> tuple[int, str]:
    """Default synchronous transport built on the standard library."""
    req = urllib.request.Request(url, headers={"User-Agent": "PollenWatch/1.1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as err:
        return err.code, ""
    # urllib.error.URLError (an OSError) and timeouts propagate for retry.


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# -- standalone entry point --------------------------------------------------


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe MeteoSwiss pollen for a location (nearest station)."
    )
    parser.add_argument("--lat", type=float, default=46.9503, help="latitude")
    parser.add_argument("--lon", type=float, default=7.4247, help="longitude")
    args = parser.parse_args(argv)

    source = MeteoSwissSource(args.lat, args.lon)
    if source.station is None:
        print("Location is outside Switzerland — MeteoSwiss out of coverage.")
        return 0
    print(f"GET {source._url()}\n")
    result = source.fetch_sync()
    print(f"PollenWatch · MeteoSwiss — status: {result.status.value}")
    print(f"  station:   {result.station}")
    print(f"  hours:     {len(result.times)} (latest {result.current_time})")
    for canon, s in result.allergens.items():
        nums = [v for v in s.values if v is not None]
        cur = "n/a" if s.current is None else f"{s.current:.0f}"
        hi = f"{max(nums):.0f}" if nums else "n/a"
        print(f"  {canon:<7} current={cur:>5}  max={hi:>5}  [{s.unit}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
