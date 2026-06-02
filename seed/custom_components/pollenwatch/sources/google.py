"""Google Pollen API source client — global coverage, billing-gated key.

The sixth PollenWatch source and the architecturally different one:

- **Index scale.** Values are the Universal Pollen Index (UPI) 0–5, not grains/m³,
  so the analytics layer collapses them with ``analytics.google_collapse`` (NOT
  ``bucket_level``).
- **Consensus-only by licence.** Google Maps Platform terms prohibit caching or
  storing forecast results, so this source is **never** baselined into
  recent_percentile: ``supports_history = False``. It still feeds consensus /
  divergence, gets a raw sensor, a 5-day forecast and personal_score (none of
  which persist a baseline). The analytics layer honours the flag (Milestone A).
- **Global coverage**, so there is no bounding-box gate — enable = toggle + key.
- **Billing-gated key.** Unlike the other sources' free keys, Google requires a
  Google Cloud project with the Pollen API enabled *and a billing account*.

Covers all six canonical allergens — the only source providing **olive**.

API shape (verified against Google's reference docs):
    GET https://pollen.googleapis.com/v1/forecast:lookup
        ?key=&location.latitude=&location.longitude=&days=1..5&plantsDescription=false
    -> {"regionCode", "dailyInfo": [{"date": {year,month,day},
         "plantInfo": [{"code": "BIRCH"|..., "inSeason": bool,
                        "indexInfo": {"code": "UPI", "value": 0..5, "category",
                                      "indexDescription"}}]}]}

HA-free like the rest of the source layer: aiohttp is imported lazily and the
transport is injectable for tests (which use a placeholder key, never a real one).
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

from .base import (
    ALLERGENS,
    AllergenSeries,
    SourceAuthError,
    SourceResponseError,
    SourceResult,
    SourceStatus,
    SourceUnavailable,
)

if TYPE_CHECKING:
    import aiohttp

SOURCE_NAME = "google"
BASE_URL = "https://pollen.googleapis.com/v1/forecast:lookup"

#: Number of daily forecast values requested (Google allows 1–5).
FORECAST_DAYS = 5

#: Google plant code -> PollenWatch canonical key. Google's enum has 17 plant
#: codes; v2.0+ maps 11 of them (the EU-relevant HIGH + MODERATE potency set).
#: Non-EU codes (JAPANESE_CEDAR, JAPANESE_CYPRESS) are excluded. LOW-potency
#: codes (PINE, MAPLE, COTTONWOOD) are excluded on potency grounds. JUNIPER and
#: CYPRESS_PINE both fold into cypress_family (the other 5 EU sources don't
#: distinguish them — honest aggregation says merge into the family canonical).
_PLANT_TO_ALLERGEN: dict[str, str] = {
    "ALDER": "alder",
    "ASH": "ash",
    "BIRCH": "birch",
    "CYPRESS_PINE": "cypress_family",
    "ELM": "elm",
    "GRAMINALES": "grass",
    "HAZEL": "hazel",
    "JUNIPER": "cypress_family",
    "MUGWORT": "mugwort",
    "OAK": "oak",
    "OLIVE": "olive",
    "RAGWEED": "ragweed",
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


def _date_iso(date: dict[str, Any] | None) -> str | None:
    try:
        return f"{int(date['year']):04d}-{int(date['month']):02d}-{int(date['day']):02d}"
    except (KeyError, TypeError, ValueError):
        return None


class GoogleSource:
    """Fetches and normalises the Google Pollen API UPI forecast."""

    name = SOURCE_NAME
    supports_history = False  # licence forbids storing results -> no percentile
    provides_history_series = False

    def __init__(
        self,
        latitude: float,
        longitude: float,
        api_key: str,
        allergens: Iterable[str] | None = None,
        *,
        days: int = FORECAST_DAYS,
        timeout: float = 30.0,
        retry_delay: float = 1.0,
        transport: Transport | None = None,
        async_transport: AsyncTransport | None = None,
    ) -> None:
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self._api_key = api_key
        self.days = max(1, min(int(days), FORECAST_DAYS))
        self.allergens = self._validate_allergens(allergens)
        self.timeout = timeout
        self.retry_delay = retry_delay
        self._transport = transport or _http_get_json
        self._async_transport = async_transport

    @staticmethod
    def _validate_allergens(allergens: Iterable[str] | None) -> list[str]:
        covered = set(_PLANT_TO_ALLERGEN.values())
        if allergens is None:
            return [a for a in ALLERGENS if a in covered]
        return [a for a in allergens if a in covered]

    def _params(self) -> dict[str, str]:
        return {
            "key": self._api_key,
            "location.latitude": f"{self.latitude:.6f}",
            "location.longitude": f"{self.longitude:.6f}",
            "days": str(self.days),
            "plantsDescription": "false",
        }

    def _url(self) -> str:
        return f"{BASE_URL}?{urllib.parse.urlencode(self._params())}"

    # -- fetching ------------------------------------------------------------

    async def async_fetch(
        self, session: aiohttp.ClientSession | None = None
    ) -> SourceResult:
        """Fetch and normalise. Global coverage — no bounding-box short-circuit."""
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
                status, payload = await transport(url, self.timeout)
                break
            except retryable as err:
                if attempt + 1 < attempts:
                    await asyncio.sleep(self.retry_delay)
                    continue
                raise SourceUnavailable(
                    f"Google request failed after {attempts} attempts: {err}"
                ) from err
        return self._handle_response(status, payload)

    def _make_aiohttp_transport(
        self, aiohttp_mod: Any, session: aiohttp.ClientSession
    ) -> AsyncTransport:
        async def transport(url: str, timeout: float) -> tuple[int, Any]:
            client_timeout = aiohttp_mod.ClientTimeout(total=timeout)
            async with session.get(url, timeout=client_timeout) as resp:
                text = await resp.text()
                try:
                    return resp.status, json.loads(text)
                except json.JSONDecodeError:
                    return resp.status, {"error": {"message": text[:200]}}

        return transport

    def fetch_sync(self) -> SourceResult:
        """Synchronous fetch for the standalone probe / offline validation."""
        attempts = 2
        url = self._url()
        for attempt in range(attempts):
            try:
                status, payload = self._transport(url, self.timeout)
                break
            except OSError as err:
                if attempt + 1 < attempts:
                    time.sleep(self.retry_delay)
                    continue
                raise SourceUnavailable(
                    f"Google request failed after {attempts} attempts: {err}"
                ) from err
        return self._handle_response(status, payload)

    def _handle_response(self, status: int, payload: Any) -> SourceResult:
        if status in (401, 403):
            raise SourceAuthError(
                f"Google rejected the API key (HTTP {status}). Check the key, that "
                f"the Pollen API is enabled, and that billing is active."
            )
        if status == 429:
            raise SourceUnavailable("Google quota exceeded (HTTP 429).")
        if status != 200:
            msg = ""
            if isinstance(payload, dict):
                msg = str((payload.get("error") or {}).get("message", "")).strip()
            raise SourceResponseError(
                f"Google returned unexpected HTTP {status}." + (f" {msg}" if msg else "")
            )
        if not isinstance(payload, dict) or "dailyInfo" not in payload:
            raise SourceResponseError("Google response missing 'dailyInfo'.")
        return self.parse(payload)

    # -- parsing (pure) ------------------------------------------------------

    def parse(self, payload: dict[str, Any]) -> SourceResult:
        """Convert a forecast:lookup response into a SourceResult (UPI index).

        Builds a per-allergen UPI series aligned to the daily dates; ``current``
        is today's (day 0) UPI. An allergen absent from the whole response is
        omitted (no data for this location); an empty ``plantInfo`` (uncovered
        point) yields no allergens. Values stay on the native 0–5 index scale
        (unit ``None``); analytics maps them via ``google_collapse``.
        """
        times: list[str] = []
        # one {canonical: UPI value} map per day, aligned to ``times``
        daily_present: list[dict[str, float]] = []
        for day in payload.get("dailyInfo") or []:
            iso = _date_iso(day.get("date"))
            if iso is None:
                continue
            present: dict[str, int] = {}
            for plant in day.get("plantInfo") or []:
                canon = _PLANT_TO_ALLERGEN.get(plant.get("code"))
                if canon is None or canon not in self.allergens:
                    continue
                value = (plant.get("indexInfo") or {}).get("value")
                if value is None:
                    continue
                try:
                    present[canon] = int(value)  # UPI is an integer 0–5
                except (TypeError, ValueError):
                    continue
            times.append(iso)
            daily_present.append(present)

        seen = set().union(*daily_present) if daily_present else set()
        allergens: dict[str, AllergenSeries] = {}
        for canon in self.allergens:  # canonical order, only those with data
            if canon not in seen:
                continue
            values: list[float | None] = [dp.get(canon) for dp in daily_present]
            allergens[canon] = AllergenSeries(
                allergen=canon,
                unit=None,  # UPI 0–5 ordinal index, not a concentration
                current=values[0],
                values=values,
            )

        return SourceResult(
            source=SOURCE_NAME,
            status=SourceStatus.OK,
            requested_lat=self.latitude,
            requested_lon=self.longitude,
            timezone=None,
            times=times,
            current_time=times[0] if times else None,
            allergens=allergens,
            generated_at=_now_iso(),
            message=payload.get("regionCode"),
        )


def _http_get_json(url: str, timeout: float) -> tuple[int, Any]:
    """Default synchronous transport built on the standard library."""
    req = urllib.request.Request(url, headers={"User-Agent": "PollenWatch/1.2.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            return err.code, json.loads(raw)
        except json.JSONDecodeError:
            return err.code, {"error": {"message": raw[:200]}}
    # urllib.error.URLError (an OSError) and timeouts propagate for retry.


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# -- standalone entry point --------------------------------------------------


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe Google Pollen API for a location (needs an API key)."
    )
    parser.add_argument("--lat", type=float, default=48.123, help="latitude")
    parser.add_argument("--lon", type=float, default=14.456, help="longitude")
    parser.add_argument("--key", required=True, help="Google Pollen API key")
    args = parser.parse_args(argv)

    source = GoogleSource(args.lat, args.lon, args.key)
    # Never print the key-bearing URL.
    print(f"GET {BASE_URL}  (lat={args.lat}, lon={args.lon}, days={source.days})\n")
    result = source.fetch_sync()
    print(f"PollenWatch · Google — status: {result.status.value}  region={result.message}")
    print(f"  days:      {len(result.times)} (today {result.current_time})")
    for canon, s in result.allergens.items():
        cur = "n/a" if s.current is None else f"{s.current:.0f}"
        print(f"  {canon:<7} UPI today={cur:>3}  series={s.values}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
