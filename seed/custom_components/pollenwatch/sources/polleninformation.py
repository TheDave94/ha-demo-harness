"""polleninformation.at source client.

Optional second source (14-ish European countries; 13 currently supported). It
serves a **daily 0–4 contamination index** per allergen for 4 days — not the
hourly grains/m³ Open-Meteo gives — so values are kept on their native index
scale here; the analytics layer normalises across sources.

HA-free like the rest of the source layer: aiohttp is imported lazily, and the
transport is injectable for tests (which use a placeholder key, never the real
one).

API shape (verified by live probe):
    GET https://www.polleninformation.at/api/forecast/public
        ?country=<ISO-2>&lang=<ISO-639-1>&latitude=&longitude=&apikey=
    -> {"contamination": [{poll_id, poll_title, contamination_1..4}, ...],
        "allergyrisk": {...}, "allergyrisk_hourly": {...}}
    contamination_N is the 0–4 index for day N (1 = today). Unsupported location
    returns HTTP 200 with {"error": ...}; bad key returns 401/403 or an
    api-key error body.
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
    SourceAuthError,
    SourceResponseError,
    SourceResult,
    SourceStatus,
    SourceUnavailable,
)

if TYPE_CHECKING:
    import aiohttp

API_URL = "https://www.polleninformation.at/api/forecast/public"
SOURCE_NAME = "polleninformation"

#: ISO-2 country codes the API serves (from the integration's documented list).
SUPPORTED_COUNTRIES: tuple[str, ...] = (
    "AT", "CH", "DE", "ES", "FR", "GB", "IT", "LV", "LT", "PL", "SE", "TR", "UA",
)

#: polleninformation poll_id -> PollenWatch canonical allergen key. Mapping is
#: by stable numeric id (language-independent). All 12 species PI's
#: /api/forecast/public exposes are mapped; the plant-compass page lists more
#: but those are educational content, not forecast variables. IDs confirmed
#: from the krissen/polleninformation HACS integration's language_map.json.
#: alternaria (poll_id 23) is a fungal spore, not pollen — labelled "(spore)"
#: in the UI; opt-in only (never default-selected) per the locked recommendation.
POLL_ID_TO_ALLERGEN: dict[int, str] = {
    1: "alder",            # Alnus
    2: "birch",            # Betula
    3: "hazel",            # Corylus
    5: "grass",            # Poaceae
    6: "ragweed",          # Ambrosia
    7: "mugwort",          # Artemisia
    15: "nettle_family",   # Urticaceae (incl. Parietaria pellitory)
    16: "plane_tree",      # Platanus
    17: "cypress_family",  # Cupressaceae
    18: "olive",           # Olea
    23: "alternaria",      # Alternaria (fungal spore)
    291: "rye",            # Secale
}

#: Number of daily forecast values (contamination_1..4).
FORECAST_DAYS = 4

AsyncTransport = Callable[[str, float], Awaitable["tuple[int, Any]"]]


def _async_retryable_exceptions() -> tuple[type[BaseException], ...]:
    retryable: tuple[type[BaseException], ...] = (asyncio.TimeoutError, OSError)
    try:
        import aiohttp
    except ImportError:
        return retryable
    return (*retryable, aiohttp.ClientError)


class PolleninformationSource:
    """Fetches and normalises the daily pollen index from polleninformation.at."""

    name = SOURCE_NAME
    supports_history = True
    provides_history_series = False  # daily index only -> recorder-baselined

    def __init__(
        self,
        latitude: float,
        longitude: float,
        country: str,
        api_key: str,
        allergens: Iterable[str] | None = None,
        *,
        lang: str = "en",
        timeout: float = 30.0,
        retry_delay: float = 1.0,
        async_transport: AsyncTransport | None = None,
    ) -> None:
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.country = country.upper()
        self._api_key = api_key
        self.lang = lang
        self.allergens = self._validate_allergens(allergens)
        self.timeout = timeout
        self.retry_delay = retry_delay
        self._async_transport = async_transport

    @staticmethod
    def _validate_allergens(allergens: Iterable[str] | None) -> list[str]:
        mapped = set(POLL_ID_TO_ALLERGEN.values())
        if allergens is None:
            return [a for a in ALLERGENS if a in mapped]
        # Silently drop allergens this source can't provide (global selection
        # is mapped onto each source's capabilities).
        return [a for a in allergens if a in mapped]

    def _params(self) -> dict[str, str]:
        return {
            "country": self.country,
            "lang": self.lang,
            "latitude": str(self.latitude),
            "longitude": str(self.longitude),
            "apikey": self._api_key,
        }

    # -- fetching ------------------------------------------------------------

    async def async_fetch(
        self, session: aiohttp.ClientSession | None = None
    ) -> SourceResult:
        """Fetch and normalise, retrying transport failures once."""
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
                    f"polleninformation request failed after {attempts} "
                    f"attempts: {err}"
                ) from err
        return self._handle_response(status, payload)

    def _make_aiohttp_transport(
        self, aiohttp_mod: Any, session: aiohttp.ClientSession
    ) -> AsyncTransport:
        async def transport(url: str, timeout: float) -> tuple[int, Any]:
            client_timeout = aiohttp_mod.ClientTimeout(total=timeout)
            async with session.get(
                url,
                params=self._params(),
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "User-Agent": (
                        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
                    ),
                },
                timeout=client_timeout,
            ) as resp:
                text = await resp.text()
                try:
                    return resp.status, json.loads(text)
                except json.JSONDecodeError:
                    return resp.status, {"error": text[:200]}

        return transport

    def _handle_response(self, status: int, payload: Any) -> SourceResult:
        if status in (401, 403):
            raise SourceAuthError(
                f"polleninformation rejected the API key (HTTP {status})."
            )
        if isinstance(payload, dict) and "error" in payload:
            reason = str(payload.get("error", "")).strip()
            if "api key" in reason.lower() or "apikey" in reason.lower():
                raise SourceAuthError(reason or "Invalid polleninformation API key.")
            # Any other error body = location not covered for this country.
            return SourceResult(
                source=SOURCE_NAME,
                status=SourceStatus.OUT_OF_COVERAGE,
                requested_lat=self.latitude,
                requested_lon=self.longitude,
                generated_at=_now_iso(),
                message=reason or "Location not covered by polleninformation.",
            )
        if status != 200:
            raise SourceResponseError(
                f"polleninformation returned unexpected HTTP {status}."
            )
        if not isinstance(payload, dict) or "contamination" not in payload:
            raise SourceResponseError(
                "polleninformation response missing 'contamination'."
            )
        return self.parse(payload)

    # -- parsing (pure) ------------------------------------------------------

    def parse(self, payload: dict[str, Any]) -> SourceResult:
        """Convert a successful response into a SourceResult (daily index).

        The API carries no dates, so the four daily values are labelled with
        today + the next three calendar dates (UTC). Values stay on the native
        0–4 index scale (unit ``None``); milestone 3b normalises across sources.
        """
        base = datetime.now(UTC).date()
        times = [(base + timedelta(days=i)).isoformat() for i in range(FORECAST_DAYS)]

        allergens: dict[str, AllergenSeries] = {}
        for item in payload.get("contamination") or []:
            canon = POLL_ID_TO_ALLERGEN.get(item.get("poll_id"))
            if canon is None or canon not in self.allergens:
                continue
            values = [
                item.get(f"contamination_{i}") for i in range(1, FORECAST_DAYS + 1)
            ]
            allergens[canon] = AllergenSeries(
                allergen=canon,
                unit=None,  # ordinal 0–4 index, not a concentration
                current=values[0],
                values=values,
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
