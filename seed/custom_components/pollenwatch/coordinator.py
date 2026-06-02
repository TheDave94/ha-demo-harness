"""Data update coordinators for PollenWatch.

Per-source ``DataUpdateCoordinator`` pattern (HA 2026.5): one coordinator per
data source, collected in a small runtime-data container on the config entry.
The meta-coordinator that fans these into cross-source analytics arrives in
milestone 3b.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .analytics import (
    PERCENTILE_WINDOW_DAYS,
    ConsensusResult,
    PercentileResult,
    compute_recent_percentile,
    consensus,
    daily_peaks,
    level_for_source,
    recent_percentile_from_series,
)
from .const import (
    ANALYTICS_DEVICE_NAME,
    CONF_ALLERGENS,
    CONF_API_KEY,
    CONF_COUNTRY,
    CONF_ENABLED,
    CONF_REGION,
    CONF_SELECTED_SPECIES,
    CONF_SOURCES,
    CONF_STATION,
    CONF_UPDATE_INTERVAL,
    DEFAULT_ALLERGENS,
    DEFAULT_UPDATE_INTERVAL_MIN,
    DOMAIN,
    DWD_UPDATE_INTERVAL_MIN,
    EPIN_UPDATE_INTERVAL_MIN,
    GOOGLE_UPDATE_INTERVAL_MIN,
    METEOSWISS_UPDATE_INTERVAL_MIN,
    OPEN_METEO_FORECAST_DAYS,
    OPEN_METEO_PAST_DAYS,
    PI_UPDATE_INTERVAL_MIN,
    SOURCE_DWD,
    SOURCE_EPIN,
    SOURCE_GOOGLE,
    SOURCE_METEOSWISS,
    SOURCE_OPEN_METEO,
    SOURCE_POLLENINFORMATION,
)
from .sources.base import AllergenSeries, PollenSource, SourceError, SourceResult
from .sources.dwd import DwdSource
from .sources.epin import EpinSource
from .sources.google import GoogleSource
from .sources.meteoswiss import MeteoSwissSource
from .sources.open_meteo import OpenMeteoSource
from .sources.polleninformation import PolleninformationSource
from .sources.species_registry import CANONICAL_SPECIES

_LOGGER = logging.getLogger(__name__)


@dataclass
class PollenWatchData:
    """Runtime data stored on the config entry: source + analytics coordinators."""

    coordinators: dict[str, PollenWatchSourceCoordinator] = field(
        default_factory=dict
    )
    analytics: PollenWatchAnalyticsCoordinator | None = None


type PollenWatchConfigEntry = ConfigEntry[PollenWatchData]


def _entry_option(entry: ConfigEntry, key: str, default: Any) -> Any:
    """Read a value preferring options (user-editable) over initial data."""
    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key, default)


class PollenWatchSourceCoordinator(DataUpdateCoordinator[SourceResult]):
    """Fetches one pollen source on an interval; source-agnostic."""

    config_entry: PollenWatchConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: PollenWatchConfigEntry,
        source: PollenSource,
        source_key: str,
        update_interval_min: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{source_key}",
            config_entry=entry,
            update_interval=timedelta(minutes=update_interval_min),
        )
        self.source = source
        self.source_key = source_key

    async def _async_update_data(self) -> SourceResult:
        session = async_get_clientsession(self.hass)
        try:
            result = await self.source.async_fetch(session=session)
        except SourceError as err:
            raise UpdateFailed(str(err)) from err
        if not result.ok:
            # The location is coverage-checked at setup; a non-OK result now
            # means a transient upstream issue rather than a misconfiguration.
            raise UpdateFailed(
                result.message
                or f"{self.source_key} returned no usable pollen data."
            )
        return result


def build_coordinators(
    hass: HomeAssistant, entry: PollenWatchConfigEntry
) -> dict[str, PollenWatchSourceCoordinator]:
    """Construct the per-source coordinators enabled for this entry.

    Open-Meteo is always built; polleninformation is built only when enabled
    with an API key + country. The global allergen selection is passed to every
    source, which maps it onto its own capabilities.
    """
    interval = _entry_option(entry, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MIN)
    # v3 storage key (CONF_SELECTED_SPECIES). Falls back to legacy
    # CONF_ALLERGENS only if the v3 key is ENTIRELY ABSENT — explicit
    # empty `[]` stays empty (the "selection bounds the blowup" guarantee
    # requires honouring deliberate emptiness, not silently restoring v1
    # defaults). Production v3 entries always have CONF_SELECTED_SPECIES;
    # the alias-fallback is for test fixtures or un-migrated edge cases.
    allergens = _entry_option(entry, CONF_SELECTED_SPECIES, None)
    if allergens is None:
        allergens = _entry_option(entry, CONF_ALLERGENS, DEFAULT_ALLERGENS)
    latitude = entry.data[CONF_LATITUDE]
    longitude = entry.data[CONF_LONGITUDE]
    sources_cfg = _entry_option(entry, CONF_SOURCES, {})

    open_meteo = OpenMeteoSource(
        latitude,
        longitude,
        allergens,
        past_days=OPEN_METEO_PAST_DAYS,
        forecast_days=OPEN_METEO_FORECAST_DAYS,
    )
    coordinators: dict[str, PollenWatchSourceCoordinator] = {
        SOURCE_OPEN_METEO: PollenWatchSourceCoordinator(
            hass, entry, open_meteo, SOURCE_OPEN_METEO, interval
        )
    }

    pi_cfg = sources_cfg.get(SOURCE_POLLENINFORMATION, {})
    if pi_cfg.get(CONF_ENABLED) and pi_cfg.get(CONF_API_KEY) and pi_cfg.get(CONF_COUNTRY):
        polleninformation = PolleninformationSource(
            latitude,
            longitude,
            pi_cfg[CONF_COUNTRY],
            pi_cfg[CONF_API_KEY],
            allergens,
        )
        coordinators[SOURCE_POLLENINFORMATION] = PollenWatchSourceCoordinator(
            hass,
            entry,
            polleninformation,
            SOURCE_POLLENINFORMATION,
            PI_UPDATE_INTERVAL_MIN,
        )

    dwd_cfg = sources_cfg.get(SOURCE_DWD, {})
    if dwd_cfg.get(CONF_ENABLED) and dwd_cfg.get(CONF_REGION) not in (None, ""):
        dwd = DwdSource(latitude, longitude, dwd_cfg[CONF_REGION], allergens)
        coordinators[SOURCE_DWD] = PollenWatchSourceCoordinator(
            hass, entry, dwd, SOURCE_DWD, DWD_UPDATE_INTERVAL_MIN
        )

    # MeteoSwiss / ePIN auto-pick the nearest station from the location; a
    # stored station (resolved at probe time) is honoured, else picked here.
    # Outside their bbox the fetch returns out_of_coverage, so an enabled but
    # uncovered source simply contributes no data (no sensors, no consensus).
    ms_cfg = sources_cfg.get(SOURCE_METEOSWISS, {})
    if ms_cfg.get(CONF_ENABLED):
        meteoswiss = MeteoSwissSource(
            latitude, longitude, allergens, station=ms_cfg.get(CONF_STATION) or None
        )
        coordinators[SOURCE_METEOSWISS] = PollenWatchSourceCoordinator(
            hass, entry, meteoswiss, SOURCE_METEOSWISS, METEOSWISS_UPDATE_INTERVAL_MIN
        )

    epin_cfg = sources_cfg.get(SOURCE_EPIN, {})
    if epin_cfg.get(CONF_ENABLED):
        epin = EpinSource(
            latitude, longitude, allergens, station=epin_cfg.get(CONF_STATION) or None
        )
        coordinators[SOURCE_EPIN] = PollenWatchSourceCoordinator(
            hass, entry, epin, SOURCE_EPIN, EPIN_UPDATE_INTERVAL_MIN
        )

    # Google: global coverage (no bbox), billing-gated key. Consensus-only —
    # supports_history=False on the source keeps it out of recent_percentile.
    google_cfg = sources_cfg.get(SOURCE_GOOGLE, {})
    if google_cfg.get(CONF_ENABLED) and google_cfg.get(CONF_API_KEY):
        google = GoogleSource(
            latitude, longitude, google_cfg[CONF_API_KEY], allergens
        )
        coordinators[SOURCE_GOOGLE] = PollenWatchSourceCoordinator(
            hass, entry, google, SOURCE_GOOGLE, GOOGLE_UPDATE_INTERVAL_MIN
        )

    return coordinators


@dataclass
class AnalyticsData:
    """Output of the analytics coordinator."""

    # recent_percentile per (source_key, species)
    percentiles: dict[tuple[str, str], PercentileResult] = field(default_factory=dict)
    # cross-source consensus per species
    consensus: dict[str, ConsensusResult] = field(default_factory=dict)


class PollenWatchAnalyticsCoordinator(DataUpdateCoordinator[AnalyticsData]):
    """Computes derived analytics from the source coordinators.

    Milestone 3b: recent_percentile per (source, species) and cross-source
    consensus per species (with its divergence flag). Sources with their own
    history (Open-Meteo's 92-day backfill) compute the percentile purely; sources
    without one (polleninformation) baseline on HA recorder history, emitting
    "insufficient_history" until enough days accrue. Consensus compares each
    source on the common 0/1/2 level scale (Open-Meteo bucketed, the index
    collapsed).
    """

    config_entry: PollenWatchConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: PollenWatchConfigEntry,
        sources: dict[str, PollenWatchSourceCoordinator],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_analytics",
            config_entry=entry,
            update_interval=timedelta(hours=1),
        )
        self._sources = sources

    def _source_level(
        self, source_key: str, species: str, series: AllergenSeries
    ) -> int | None:
        """Delegate to analytics.level_for_source (single source of truth).

        Kept as a thin method for call-site stability; v2.1+ the raw
        ``PollenWatchSensor`` reads the same function for its ``level``
        attribute so dashboards never re-implement bucketing.
        """
        return level_for_source(source_key, species, series)

    async def _async_update_data(self) -> AnalyticsData:
        today = dt_util.now().date().isoformat()
        percentiles: dict[tuple[str, str], PercentileResult] = {}
        levels: dict[str, dict[str, int]] = {}
        for source_key, coordinator in self._sources.items():
            data = coordinator.data
            if data is None:
                continue
            source = coordinator.source
            # Self-baselining sources rank their own latest day (observation
            # feeds like MeteoSwiss lag, so the calendar today may be absent).
            ref_day = (data.current_time or today)[:10]
            for species, series in data.allergens.items():
                if source.supports_history:
                    # supports_history=False (e.g. a future no-storage source)
                    # gets no recent_percentile at all.
                    if source.provides_history_series:
                        percentiles[(source_key, species)] = (
                            recent_percentile_from_series(
                                data.times, series.values, ref_day
                            )
                        )
                    else:
                        percentiles[(source_key, species)] = (
                            await self._recorder_percentile(source_key, species, today)
                        )
                level = self._source_level(source_key, species, series)
                if level is not None:
                    levels.setdefault(species, {})[source_key] = level
        # Pass each species's registry ceiling so the consensus result carries
        # max_possible — the n/m badge denominator on the card. Unknown
        # species fall back to len(src) (no badge ceiling claim).
        consensus_map = {
            sp: consensus(src, _registry_max_possible(sp))
            for sp, src in levels.items()
        }
        return AnalyticsData(percentiles=percentiles, consensus=consensus_map)

    async def _recorder_percentile(
        self, source_key: str, species: str, today: str
    ) -> PercentileResult:
        """recent_percentile from HA recorder history of a source's raw sensor."""
        entity_id = f"sensor.{DOMAIN}_{source_key}_{species}"
        peaks = await self._recorder_daily_peaks(entity_id)
        return compute_recent_percentile(peaks[-PERCENTILE_WINDOW_DAYS:], today)

    async def _recorder_daily_peaks(
        self, entity_id: str
    ) -> list[tuple[str, float]]:
        """Daily peaks of a numeric entity over the trailing window, via recorder.

        Returns an empty list if the recorder is unavailable (→ the caller emits
        "insufficient_history" rather than a misleading number).
        """
        if "recorder" not in self.hass.config.components:
            return []
        from homeassistant.components.recorder import get_instance, history

        end = dt_util.now()
        start = end - timedelta(days=PERCENTILE_WINDOW_DAYS)
        states = await get_instance(self.hass).async_add_executor_job(
            history.state_changes_during_period,
            self.hass,
            start,
            end,
            entity_id,
        )
        times: list[str] = []
        values: list[float] = []
        for state in states.get(entity_id, []):
            try:
                value = float(state.state)
            except (ValueError, TypeError):
                continue  # 'unknown'/'unavailable'
            times.append(dt_util.as_local(state.last_changed).isoformat())
            values.append(value)
        return daily_peaks(times, values)


def analytics_device_info(entry: PollenWatchConfigEntry) -> DeviceInfo:
    """Device for the cross-source analytics entities (consensus, divergence)."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_analytics")},
        name=ANALYTICS_DEVICE_NAME,
        manufacturer="PollenWatch",
        model="Cross-source analytics",
        entry_type=DeviceEntryType.SERVICE,
    )


def multi_source_species(
    coordinators: dict[str, PollenWatchSourceCoordinator],
) -> list[str]:
    """Species currently covered by >= 2 sources (divergence needs two).

    Used to gate `DivergenceSensor` creation — single-source species never
    emit a divergence entity (nothing to disagree with).
    """
    counts: dict[str, int] = {}
    for coordinator in coordinators.values():
        if coordinator.data is None:
            continue
        for species in coordinator.data.allergens:
            counts[species] = counts.get(species, 0) + 1
    return sorted(species for species, n in counts.items() if n >= 2)


def all_covered_species(
    coordinators: dict[str, PollenWatchSourceCoordinator],
) -> list[str]:
    """Species currently covered by >= 1 source.

    Used to gate `ConsensusSensor` creation in v2.0+. Single-source species
    still get a consensus sensor (pass-through level + n/m badge); the badge
    is what tells users the reading is single-source, not the sensor's
    absence.
    """
    covered: set[str] = set()
    for coordinator in coordinators.values():
        if coordinator.data is None:
            continue
        covered.update(coordinator.data.allergens.keys())
    return sorted(covered)


def _registry_max_possible(species: str) -> int:
    """Global source-count ceiling for a species, from the canonical registry.

    The n/m badge on the card uses this as the denominator. Returns 0 for
    species not in the registry (defensive — shouldn't happen for canonical
    species, but a stale storage entry shouldn't crash analytics).
    """
    info = CANONICAL_SPECIES.get(species)
    return len(info.sources) if info else 0
