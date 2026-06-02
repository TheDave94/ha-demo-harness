"""Open-Meteo (CAMS) pollen source client.

Open-Meteo serves the Copernicus Atmosphere Monitoring Service (CAMS) European
pollen forecast: 6 species, hourly, ~5-day forecast, and up to 92 past days —
free and keyless. This is PollenWatch's primary source.

The module is standalone and HA-free. Run it directly to probe a location::

    python -m custom_components.pollenwatch.sources.open_meteo --lat 48.123 --lon 14.456

Design notes
------------
- :meth:`OpenMeteoSource.parse` is a pure function (JSON dict -> SourceResult)
  with no I/O, so it is reusable from an async HA coordinator that does its own
  ``aiohttp`` fetch.
- :meth:`OpenMeteoSource.fetch` is a synchronous convenience used by the
  standalone entry point and the unit tests. Its transport is injectable.
- Out-of-coverage is detected via Open-Meteo's HTTP 400 ``error`` response
  (``"No data is available for this location"``). It is NOT inferred from
  all-zero values: a valid European location off-season legitimately returns
  all zeros.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiohttp

from .base import (
    ALLERGENS,
    AllergenSeries,
    SourceResponseError,
    SourceResult,
    SourceStatus,
    SourceUnavailable,
)

BASE_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
SOURCE_NAME = "open_meteo"

#: All six CAMS pollen species this source can request.
SUPPORTED_ALLERGENS: tuple[str, ...] = ALLERGENS

#: Open-Meteo's CAMS domain for European pollen. ``cams_global`` returns nulls
#: for pollen, so this is set explicitly rather than relying on auto-selection.
DOMAIN_CAMS_EUROPE = "cams_europe"

#: Hard ceiling Open-Meteo enforces on backfill.
MAX_PAST_DAYS = 92

# canonical allergen key -> Open-Meteo hourly variable name
_API_VAR: dict[str, str] = {a: f"{a}_pollen" for a in SUPPORTED_ALLERGENS}

# Substring (case-insensitive) that marks an out-of-coverage error response, as
# opposed to a genuinely malformed request.
_COVERAGE_REASON = "no data is available"

#: ``transport(url, timeout) -> (status_code, parsed_json)``. Network-level
#: failures must raise ``OSError`` (incl. ``urllib.error.URLError``) so
#: :meth:`OpenMeteoSource.fetch` can retry. HTTP error *responses* that carry a
#: body (e.g. 400) should be returned as ``(code, body)``, not raised.
Transport = Callable[[str, float], "tuple[int, Any]"]

#: Async counterpart of :data:`Transport`, awaited by
#: :meth:`OpenMeteoSource.async_fetch`.
AsyncTransport = Callable[[str, float], Awaitable["tuple[int, Any]"]]


def _http_get_json(url: str, timeout: float) -> tuple[int, Any]:
    """Default synchronous transport built on the standard library."""
    req = urllib.request.Request(url, headers={"User-Agent": "PollenWatch/0.0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        # Open-Meteo returns a JSON body (e.g. {"error": true, "reason": ...})
        # alongside 4xx codes. Surface it instead of treating it as transport
        # failure, so the caller can classify it.
        raw = err.read().decode("utf-8", errors="replace")
        try:
            return err.code, json.loads(raw)
        except json.JSONDecodeError:
            return err.code, {"error": True, "reason": raw[:200]}
    # urllib.error.URLError (a subclass of OSError) and socket timeouts
    # propagate to fetch() for retry handling.


def _async_retryable_exceptions() -> tuple[type[BaseException], ...]:
    """Transport exceptions the async fetch should retry.

    Includes ``aiohttp.ClientError`` when aiohttp is importable; always covers
    timeouts and OS-level errors (which also covers injected test transports
    raising e.g. ``ConnectionError``).
    """
    retryable: tuple[type[BaseException], ...] = (asyncio.TimeoutError, OSError)
    try:
        import aiohttp
    except ImportError:
        return retryable
    return (*retryable, aiohttp.ClientError)


class OpenMeteoSource:
    """Fetches and normalises CAMS pollen data from Open-Meteo."""

    name = SOURCE_NAME
    supports_history = True
    provides_history_series = True  # 92-day backfill -> self-baselined percentile

    def __init__(
        self,
        latitude: float,
        longitude: float,
        allergens: Iterable[str] | None = None,
        *,
        past_days: int = MAX_PAST_DAYS,
        forecast_days: int = 5,
        domains: str = DOMAIN_CAMS_EUROPE,
        timeout: float = 30.0,
        retry_delay: float = 1.0,
        transport: Transport | None = None,
        async_transport: AsyncTransport | None = None,
    ) -> None:
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.allergens = self._validate_allergens(allergens)
        self.past_days = max(0, min(int(past_days), MAX_PAST_DAYS))
        self.forecast_days = max(1, int(forecast_days))
        self.domains = domains
        self.timeout = timeout
        self.retry_delay = retry_delay
        self._transport: Transport = transport or _http_get_json
        self._async_transport: AsyncTransport | None = async_transport

    @staticmethod
    def _validate_allergens(allergens: Iterable[str] | None) -> list[str]:
        # Silent-drop pattern matching every other source: the orchestrator
        # passes the user's GLOBAL selection (24 species in v2+) and each
        # source takes only what it covers. v1.x raised on unknowns because
        # the global selection == OM's set; v2+ has a bigger global set, so a
        # raise here would block every install selecting e.g. hazel.
        if allergens is None:
            return list(SUPPORTED_ALLERGENS)
        return [a for a in allergens if a in _API_VAR]

    # -- request building ----------------------------------------------------

    def _params(self) -> dict[str, str]:
        variables = ",".join(_API_VAR[a] for a in self.allergens)
        return {
            "latitude": _fmt_coord(self.latitude),
            "longitude": _fmt_coord(self.longitude),
            "hourly": variables,
            "current": variables,
            "domains": self.domains,
            "past_days": str(self.past_days),
            "forecast_days": str(self.forecast_days),
            "timezone": "auto",
        }

    def build_url(self) -> str:
        return f"{BASE_URL}?{urllib.parse.urlencode(self._params())}"

    # -- fetching ------------------------------------------------------------

    def fetch(self) -> SourceResult:
        """Fetch and normalise data, retrying transport failures once.

        Raises :class:`SourceUnavailable` if the transport fails twice and
        :class:`SourceResponseError` for unrecognised error responses.
        """
        url = self.build_url()
        attempts = 2  # initial try + one retry
        for attempt in range(attempts):
            try:
                status, payload = self._transport(url, self.timeout)
                break
            except OSError as err:  # incl. urllib.error.URLError, timeouts
                if attempt + 1 < attempts:
                    time.sleep(self.retry_delay)
                    continue
                raise SourceUnavailable(
                    f"Open-Meteo request failed after {attempts} attempts: {err}"
                ) from err
        return self._handle_response(status, payload)

    async def async_fetch(
        self, session: aiohttp.ClientSession | None = None
    ) -> SourceResult:
        """Async counterpart of :meth:`fetch`, for use inside Home Assistant.

        Pass HA's shared ``aiohttp`` session (``async_get_clientsession``). If
        omitted, a temporary session is created and closed. An injected
        ``async_transport`` (see ``__init__``) takes precedence and is used as-is
        — handy for tests without ``aiohttp``. Shares :meth:`_handle_response`
        and :meth:`parse` with the sync path, so classification and parsing are
        identical.
        """
        url = self.build_url()
        if self._async_transport is not None:
            return await self._async_fetch_with(self._async_transport, url)

        import aiohttp  # local import keeps the module importable without aiohttp

        owns_session = session is None
        if owns_session:
            session = aiohttp.ClientSession()
        try:
            return await self._async_fetch_with(
                self._make_aiohttp_transport(aiohttp, session), url
            )
        finally:
            if owns_session:
                await session.close()

    async def _async_fetch_with(
        self, transport: AsyncTransport, url: str
    ) -> SourceResult:
        retryable = _async_retryable_exceptions()
        attempts = 2  # initial try + one retry
        for attempt in range(attempts):
            try:
                status, payload = await transport(url, self.timeout)
                break
            except retryable as err:
                if attempt + 1 < attempts:
                    await asyncio.sleep(self.retry_delay)
                    continue
                raise SourceUnavailable(
                    f"Open-Meteo request failed after {attempts} attempts: {err}"
                ) from err
        return self._handle_response(status, payload)

    def _make_aiohttp_transport(
        self, aiohttp_mod: Any, session: aiohttp.ClientSession
    ) -> AsyncTransport:
        async def transport(url: str, timeout: float) -> tuple[int, Any]:
            client_timeout = aiohttp_mod.ClientTimeout(total=timeout)
            async with session.get(
                url,
                headers={"User-Agent": "PollenWatch/0.1.0"},
                timeout=client_timeout,
            ) as resp:
                # Read the body regardless of status: Open-Meteo returns a JSON
                # error body alongside 4xx codes (e.g. out-of-coverage).
                text = await resp.text()
                try:
                    return resp.status, json.loads(text)
                except json.JSONDecodeError:
                    return resp.status, {"error": True, "reason": text[:200]}

        return transport

    def _handle_response(self, status: int, payload: Any) -> SourceResult:
        if isinstance(payload, dict) and payload.get("error"):
            reason = str(payload.get("reason", "")).strip()
            if _COVERAGE_REASON in reason.lower():
                return self._coverage_result(reason)
            raise SourceResponseError(
                f"Open-Meteo error (HTTP {status}): {reason or 'unknown reason'}"
            )
        if status != 200:
            raise SourceResponseError(
                f"Open-Meteo returned unexpected HTTP {status}."
            )
        if not isinstance(payload, dict) or "hourly" not in payload:
            raise SourceResponseError(
                "Open-Meteo response missing 'hourly' data."
            )
        return self.parse(payload)

    def _coverage_result(self, reason: str) -> SourceResult:
        return SourceResult(
            source=SOURCE_NAME,
            status=SourceStatus.OUT_OF_COVERAGE,
            requested_lat=self.latitude,
            requested_lon=self.longitude,
            generated_at=_now_iso(),
            message=(
                reason
                or "This location is outside CAMS European coverage."
            ),
        )

    # -- parsing (pure) ------------------------------------------------------

    def parse(self, payload: dict[str, Any]) -> SourceResult:
        """Convert a successful Open-Meteo JSON payload into a SourceResult.

        Pure and side-effect free; safe to call from an async coordinator that
        did its own fetch.
        """
        hourly = payload.get("hourly") or {}
        units = payload.get("hourly_units") or {}
        current = payload.get("current") or {}
        times = list(hourly.get("time") or [])

        allergens: dict[str, AllergenSeries] = {}
        for canon in self.allergens:
            var = _API_VAR[canon]
            if var not in hourly:
                continue
            allergens[canon] = AllergenSeries(
                allergen=canon,
                unit=units.get(var, "grains/m³"),
                current=current.get(var),
                values=list(hourly.get(var) or []),
            )

        return SourceResult(
            source=SOURCE_NAME,
            status=SourceStatus.OK,
            requested_lat=self.latitude,
            requested_lon=self.longitude,
            snapped_lat=payload.get("latitude"),
            snapped_lon=payload.get("longitude"),
            timezone=payload.get("timezone"),
            elevation=payload.get("elevation"),
            times=times,
            current_time=current.get("time"),
            allergens=allergens,
            generated_at=_now_iso(),
        )


def _fmt_coord(value: float) -> str:
    """Format a coordinate without trailing float noise."""
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# -- standalone entry point --------------------------------------------------


def _summarise(result: SourceResult) -> str:
    lines: list[str] = []
    lines.append(f"PollenWatch · Open-Meteo (CAMS) — status: {result.status.value}")
    lines.append(
        f"  requested: {result.requested_lat:.4f}, {result.requested_lon:.4f}"
    )
    if result.status is not SourceStatus.OK:
        if result.message:
            lines.append(f"  message:   {result.message}")
        return "\n".join(lines)

    shift = result.coordinate_shift_km
    lines.append(
        f"  snapped:   {result.snapped_lat}, {result.snapped_lon}"
        + (f"  (~{shift:.1f} km away)" if shift is not None else "")
    )
    lines.append(f"  timezone:  {result.timezone}   elevation: {result.elevation} m")
    lines.append(
        f"  hours:     {len(result.times)} "
        f"(forecast starts at index {result.forecast_split}: "
        f"{result.current_time})"
    )
    lines.append("")
    lines.append(f"  {'allergen':<9} {'current':>9} {'min':>7} {'max':>7}  forecast tail")
    lines.append(f"  {'-' * 9} {'-' * 9} {'-' * 7} {'-' * 7}  {'-' * 13}")
    for canon, series in result.allergens.items():
        nums = [v for v in series.values if v is not None]
        trailing_nulls = 0
        for v in reversed(series.values):
            if v is None:
                trailing_nulls += 1
            else:
                break
        cur = "n/a" if series.current is None else f"{series.current:.1f}"
        lo = f"{min(nums):.1f}" if nums else "n/a"
        hi = f"{max(nums):.1f}" if nums else "n/a"
        lines.append(
            f"  {canon:<9} {cur:>9} {lo:>7} {hi:>7}  "
            f"{trailing_nulls} null hr(s) [{series.unit}]"
        )
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe Open-Meteo CAMS pollen for a location.",
    )
    # Default coordinates: the demo location.
    parser.add_argument("--lat", type=float, default=48.123, help="latitude")
    parser.add_argument("--lon", type=float, default=14.456, help="longitude")
    parser.add_argument(
        "--allergens",
        default=",".join(SUPPORTED_ALLERGENS),
        help="comma-separated allergen keys (default: all)",
    )
    parser.add_argument("--past-days", type=int, default=7)
    parser.add_argument("--forecast-days", type=int, default=5)
    args = parser.parse_args(argv)

    allergens = [a.strip() for a in args.allergens.split(",") if a.strip()]
    source = OpenMeteoSource(
        args.lat,
        args.lon,
        allergens,
        past_days=args.past_days,
        forecast_days=args.forecast_days,
    )
    print(f"GET {source.build_url()}\n")
    result = source.fetch()
    print(_summarise(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
