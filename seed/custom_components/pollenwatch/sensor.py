"""Sensor entities for PollenWatch.

One sensor per (source, species) pair. State is the current value in the
source's native units; the daily-peak forecast and provenance live in
attributes. Entities sit under a per-source device and are named so their
entity IDs slug to ``sensor.pollenwatch_<source>_<species>``.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .analytics import (
    CONSENSUS_OPTIONS,
    daily_peaks,
    level_for_source,
)
from .analytics import (
    level_label as _level_label,
)
from .const import (
    ALLERGEN_NAMES,
    ATTR_FORECAST,
    ATTR_GRID_SHIFT_KM,
    ATTR_LAST_UPDATED,
    ATTR_MAX_SOURCES,
    ATTR_MULTIPLIER,
    ATTR_REQUESTED_LAT,
    ATTR_REQUESTED_LON,
    ATTR_SNAPPED_LAT,
    ATTR_SNAPPED_LON,
    ATTR_SOURCE_COUNT,
    ATTRIBUTION_CAMS,
    CONF_SENSITIVITY,
    DEFAULT_SENSITIVITY,
    DOMAIN,
    FORECAST_DAYS,
    SOURCE_ATTRIBUTIONS,
    SOURCE_CONFIG_URLS,
    SOURCE_DEVICE_MODELS,
    SOURCE_DEVICE_NAMES,
)
from .coordinator import (
    PollenWatchAnalyticsCoordinator,
    PollenWatchConfigEntry,
    PollenWatchSourceCoordinator,
    _entry_option,
    all_covered_species,
    analytics_device_info,
)
from .sources.species_registry import CANONICAL_SPECIES, threshold_basis_for

# Coordinator-driven entities with no per-entity writes — HA serialization
# is unnecessary; declare parallel updates to keep the silver rule explicit.
PARALLEL_UPDATES = 0

# All possible per-source slugs the integration could ever have created
# entities under. Used to prune entities for sources that have been DISABLED
# via the options flow (those sources stop being built into coordinators, so
# the per-coordinator pruning loop never reaches them — without this catch
# they orphan as ``unavailable`` forever).
ALL_KNOWN_SOURCES: set[str] = set(SOURCE_DEVICE_NAMES.keys())

# Extra-state-attribute keys for the recent_percentile sensor.
ATTR_HISTORY_STATUS = "history_status"
ATTR_DAYS_OF_HISTORY = "days_of_history"
# ... and the consensus sensor.
ATTR_LEVEL = "level"
ATTR_LEVEL_LABEL = "level_label"
ATTR_SOURCE_LEVELS = "source_levels"
# v2.2: per-species threshold evidence-tier (5-value enum from
# species_registry.ThresholdStatus). Surfaced on the raw + consensus
# sensors so downstream consumers can mark "this 'high' is on a borrowed
# family bracket" without consulting a second data source.
ATTR_THRESHOLD_STATUS = "threshold_status"
# Coarse 3-value provenance grouping derived from threshold_status, for
# binary glance-treatment in card UIs (see THRESHOLD_BASIS_FROM_STATUS).
ATTR_THRESHOLD_BASIS = "threshold_basis"


def _source_device_info(entry: PollenWatchConfigEntry, source_key: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{source_key}")},
        name=SOURCE_DEVICE_NAMES[source_key],
        manufacturer="PollenWatch",
        model=SOURCE_DEVICE_MODELS.get(source_key),
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=SOURCE_CONFIG_URLS.get(source_key),
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PollenWatchConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PollenWatch sensors for a config entry.

    Runs on every reload (including after an options change), so it also prunes
    registry entries for allergens no longer configured for each source.
    """
    runtime = entry.runtime_data
    analytics = runtime.analytics
    sensitivity = _entry_option(entry, CONF_SENSITIVITY, {})
    entities: list[SensorEntity] = []
    for source_key, coordinator in runtime.coordinators.items():
        # Keep registry entries for every configured allergen (so a transient
        # absence doesn't delete a sensor); only create sensors for allergens the
        # source actually returned (a source's set is location/country-dependent).
        configured = set(coordinator.source.allergens)
        _async_remove_deconfigured_entities(hass, entry, source_key, configured)
        if coordinator.data is None:
            continue
        present = [a for a in coordinator.source.allergens if a in coordinator.data.allergens]
        for allergen in present:
            entities.append(PollenWatchSensor(coordinator, source_key, allergen))
            entities.append(
                PersonalScoreSensor(
                    coordinator,
                    entry,
                    source_key,
                    allergen,
                    sensitivity.get(allergen, DEFAULT_SENSITIVITY),
                )
            )
            # recent_percentile only for sources whose data may be baselined
            # (supports_history). A no-storage source would skip it cleanly.
            if analytics is not None and coordinator.source.supports_history:
                entities.append(
                    RecentPercentileSensor(analytics, entry, source_key, allergen)
                )

    # Prune entities for sources that are no longer enabled — disabling a
    # source via the options flow stops it being built into a coordinator, so
    # the per-coordinator loop above never runs the prune for it. Without this
    # catch the disabled source's sensors live on as ``unavailable`` forever.
    active_sources = set(runtime.coordinators.keys())
    for source_key in ALL_KNOWN_SOURCES:
        if source_key not in active_sources:
            _async_remove_deconfigured_entities(hass, entry, source_key, set())

    # Cross-source consensus: one per species that >= 1 source currently
    # covers (v2.0+ broadened from >= 2). Single-source species emit a
    # pass-through consensus + n/m=1/x badge; the badge tells the user it's
    # single-source, not the sensor's absence. DivergenceSensor (binary)
    # still requires >= 2 — gated separately in binary_sensor.py.
    covered_species: list[str] = []
    if analytics is not None:
        covered_species = all_covered_species(runtime.coordinators)
        for species in covered_species:
            entities.append(ConsensusSensor(analytics, entry, species))

    # Prune stale consensus sensors for species no longer covered (e.g. user
    # disabled the only source covering a species). Same orphan story as
    # above, applied to the analytics device.
    _async_remove_orphan_analytics(hass, entry, set(covered_species), "consensus")

    async_add_entities(entities)


@callback
def _async_remove_orphan_analytics(
    hass: HomeAssistant,
    entry: PollenWatchConfigEntry,
    active_species: set[str],
    metric: str,
) -> None:
    """Remove analytics entities (consensus / divergence) for species no
    longer covered by >= 2 sources. Shared by sensor.py + binary_sensor.py.
    """
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_{metric}_"
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.unique_id.startswith(prefix):
            species = reg_entry.unique_id[len(prefix):]
            if species not in active_species:
                registry.async_remove(reg_entry.entity_id)


@callback
def _async_remove_deconfigured_entities(
    hass: HomeAssistant,
    entry: PollenWatchConfigEntry,
    source_key: str,
    configured: set[str],
) -> None:
    """Remove a source's sensor entities for allergens no longer configured.

    Without this, deselecting an allergen in the options flow would leave the
    sensor lingering as ``unavailable`` in the registry instead of disappearing.
    """
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_{source_key}_"
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.unique_id.startswith(prefix):
            # Suffix is "<allergen>" or "<allergen>_<metric>" (e.g.
            # grass_recent_percentile); the allergen is the first token. Removing
            # a deconfigured allergen drops all its entities (raw + derived).
            allergen = reg_entry.unique_id[len(prefix):].split("_", 1)[0]
            if allergen not in configured:
                registry.async_remove(reg_entry.entity_id)


def _forecast_attr(
    times: list[str], values: list[float | None], today: str, max_days: int
) -> list[dict[str, Any]]:
    """The upcoming daily-peak forecast: per-day max for dates >= today.

    The series now spans ~92 past days (for recent_percentile), so the forecast
    must be the today-onward slice — not the earliest days. Peaks (not means)
    drive allergic reactions; the partially-null 5th day is dropped via max_days.
    """
    return [
        {"date": date, "value": peak}
        for date, peak in daily_peaks(times, values)
        if date >= today
    ][:max_days]


class PollenWatchSensor(
    CoordinatorEntity[PollenWatchSourceCoordinator], SensorEntity
):
    """Current pollen concentration for one allergen from one source."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flower-pollen"

    def __init__(
        self,
        coordinator: PollenWatchSourceCoordinator,
        source_key: str,
        allergen: str,
    ) -> None:
        super().__init__(coordinator)
        self._allergen = allergen
        self._source_key = source_key
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{source_key}_{allergen}"
        self._attr_translation_key = allergen
        # Fallback name (used if no strings.json translation exists for the
        # allergen — every v2.0+ new species). Without this, HA can't slugify
        # the entity_id past the device prefix and entities end up as
        # `sensor.pollenwatch_<source>` (collisions across allergens).
        self._attr_name = ALLERGEN_NAMES.get(
            allergen, allergen.replace("_", " ").title()
        )
        # Force entity_id to match the canonical species registry key
        # (e.g. `plantago` not `plantain`, `carpinus` not `hornbeam`). One
        # consistent rule across all 24: entity_id always ends in the
        # canonical key. HA's has_entity_name auto-naming was unreliable
        # here (some entities slugged correctly, others collapsed to the
        # bare device id), and suggested_object_id was ignored for some
        # combinations. Explicit assignment bypasses both — HA preserves
        # existing entity_ids by unique_id lookup, so v1 species (already
        # registered) are untouched.
        self.entity_id = f"sensor.{DOMAIN}_{source_key}_{allergen}"
        self._attr_attribution = SOURCE_ATTRIBUTIONS.get(source_key, ATTRIBUTION_CAMS)
        # Unit comes from the source: grains/m³ for Open-Meteo, None (an ordinal
        # 0–4 index) for polleninformation — never fake a concentration unit.
        series = coordinator.data.allergens.get(allergen)
        self._attr_native_unit_of_measurement = series.unit if series else None
        self._attr_device_info = _source_device_info(entry, source_key)

    @property
    def available(self) -> bool:
        return (
            super().available
            and self._allergen in self.coordinator.data.allergens
        )

    @property
    def native_value(self) -> float | None:
        series = self.coordinator.data.allergens.get(self._allergen)
        return series.current if series else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        result = self.coordinator.data
        series = result.allergens.get(self._allergen)
        if series is None:
            return None
        shift = result.coordinate_shift_km
        today = (result.current_time or "")[:10]
        # Normalised severity from the integration's own bucketing
        # (analytics.level_for_source — single source of truth for every
        # downstream consumer, replacing per-dashboard threshold tables).
        lvl = level_for_source(self._source_key, self._allergen, series)
        # v2.2: evidence-tier for the bucketing — surfaced so a "high" on a
        # borrowed family bracket is distinguishable from a "high" on
        # species-specific cited evidence. Sourced from species_registry.
        thr_status = CANONICAL_SPECIES[self._allergen].thresholds.value
        attrs: dict[str, Any] = {
            ATTR_FORECAST: _forecast_attr(
                result.times, series.values, today, FORECAST_DAYS
            ),
            ATTR_LEVEL: lvl,
            ATTR_LEVEL_LABEL: _level_label(lvl),
            ATTR_THRESHOLD_STATUS: thr_status,
            ATTR_THRESHOLD_BASIS: threshold_basis_for(self._allergen),
            ATTR_REQUESTED_LAT: result.requested_lat,
            ATTR_REQUESTED_LON: result.requested_lon,
            ATTR_SNAPPED_LAT: result.snapped_lat,
            ATTR_SNAPPED_LON: result.snapped_lon,
            ATTR_GRID_SHIFT_KM: round(shift, 2) if shift is not None else None,
            ATTR_LAST_UPDATED: result.generated_at,
        }
        if series.native is not None:
            # Source's native categorical value (e.g. DWD "2-3").
            attrs["native_value"] = series.native
        if result.station is not None:
            # Station-based sources (MeteoSwiss, ePIN): which station was picked.
            attrs["station"] = result.station
        return attrs


class PersonalScoreSensor(
    CoordinatorEntity[PollenWatchSourceCoordinator], SensorEntity
):
    """A source's raw value scaled by the user's per-species sensitivity.

    personal_score = raw × multiplier (0.0–2.0). Single-source; keeps the
    source's native unit (grains/m³ for Open-Meteo, none for the index) — it is
    a personally-weighted value, not a cross-source comparison.
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:account-alert"
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: PollenWatchSourceCoordinator,
        entry: PollenWatchConfigEntry,
        source_key: str,
        allergen: str,
        multiplier: float,
    ) -> None:
        super().__init__(coordinator)
        self._allergen = allergen
        self._multiplier = float(multiplier)
        self._attr_unique_id = (
            f"{entry.entry_id}_{source_key}_{allergen}_personal_score"
        )
        self._attr_translation_key = f"personal_score_{allergen}"
        self._attr_name = f"{ALLERGEN_NAMES.get(allergen, allergen)} personal score"
        # Canonical-key entity_id (see PollenWatchSensor for rationale).
        self.entity_id = (
            f"sensor.{DOMAIN}_{source_key}_{allergen}_personal_score"
        )
        series = coordinator.data.allergens.get(allergen)
        self._attr_native_unit_of_measurement = series.unit if series else None
        self._attr_device_info = _source_device_info(entry, source_key)

    @property
    def available(self) -> bool:
        return super().available and self._allergen in self.coordinator.data.allergens

    @property
    def native_value(self) -> float | None:
        series = self.coordinator.data.allergens.get(self._allergen)
        if series is None or series.current is None:
            return None
        return series.current * self._multiplier

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {ATTR_MULTIPLIER: self._multiplier}


class ConsensusSensor(
    CoordinatorEntity[PollenWatchAnalyticsCoordinator], SensorEntity
):
    """Cross-source consensus level for one species (none/low/high/mixed).

    Categorical (ENUM) so it can report "mixed" when sources disagree by >1
    level. v2.0+: also created for single-source species (pass-through level
    + source_count=1 in attributes). Card uses ``source_count`` /
    ``max_possible_sources`` to render the n/m badge — the honesty mechanism
    that signals single-source vs cross-validated readings.
    """

    # Device-scoped entity ID: HA 2026.5 prefixes device-associated entities with
    # the device slug regardless of has_entity_name/self.entity_id, so the ID is
    # sensor.pollenwatch_analytics_<species>_consensus (Analytics device kept).
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CONSENSUS_OPTIONS
    _attr_icon = "mdi:scale-balance"

    def __init__(
        self,
        coordinator: PollenWatchAnalyticsCoordinator,
        entry: PollenWatchConfigEntry,
        species: str,
    ) -> None:
        super().__init__(coordinator)
        self._species = species
        self._attr_unique_id = f"{entry.entry_id}_consensus_{species}"
        self._attr_translation_key = f"consensus_{species}"
        self._attr_name = f"{ALLERGEN_NAMES.get(species, species)} consensus"
        # Canonical-key entity_id (see PollenWatchSensor for rationale).
        self.entity_id = f"sensor.{DOMAIN}_analytics_{species}_consensus"
        self._attr_device_info = analytics_device_info(entry)

    def _result(self):
        return self.coordinator.data.consensus.get(self._species)

    @property
    def available(self) -> bool:
        # Available when at least one source is currently contributing. The
        # n/m badge in attributes tells the user whether it's single-source
        # or cross-validated; the sensor's presence is no longer the gate.
        result = self._result()
        return (
            super().available
            and result is not None
            and result.source_count >= 1
        )

    @property
    def native_value(self) -> str | None:
        result = self._result()
        return result.state if result else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        result = self._result()
        if result is None:
            return None
        return {
            ATTR_LEVEL: result.level,
            ATTR_SOURCE_LEVELS: result.source_levels,
            ATTR_SOURCE_COUNT: result.source_count,
            ATTR_MAX_SOURCES: result.max_possible,
            # v2.2: evidence-tier for the species's bucketing — invariant
            # across sources; reads the same as on the raw sensor.
            ATTR_THRESHOLD_STATUS: CANONICAL_SPECIES[self._species].thresholds.value,
            ATTR_THRESHOLD_BASIS: threshold_basis_for(self._species),
        }


class RecentPercentileSensor(
    CoordinatorEntity[PollenWatchAnalyticsCoordinator], SensorEntity
):
    """Today's daily peak as a percentile of the recent window (per source).

    Single-source (each source gets its own). Open-Meteo computes from its
    92-day backfill (day one); polleninformation baselines on recorder history
    and reports an honest "insufficient_history" state (no number) until enough
    days accrue. State is unitless 0–100; the history status is in attributes.
    """

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:chart-bell-curve-cumulative"
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: PollenWatchAnalyticsCoordinator,
        entry: PollenWatchConfigEntry,
        source_key: str,
        allergen: str,
    ) -> None:
        super().__init__(coordinator)
        self._key = (source_key, allergen)
        self._attr_unique_id = (
            f"{entry.entry_id}_{source_key}_{allergen}_recent_percentile"
        )
        self._attr_translation_key = f"recent_percentile_{allergen}"
        self._attr_name = f"{ALLERGEN_NAMES.get(allergen, allergen)} recent percentile"
        # Canonical-key entity_id (see PollenWatchSensor for rationale).
        self.entity_id = (
            f"sensor.{DOMAIN}_{source_key}_{allergen}_recent_percentile"
        )
        self._attr_device_info = _source_device_info(entry, source_key)

    @property
    def native_value(self) -> float | None:
        result = self.coordinator.data.percentiles.get(self._key)
        if result is None or result.status != "ok":
            return None
        return result.percentile

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        result = self.coordinator.data.percentiles.get(self._key)
        if result is None:
            return None
        return {
            ATTR_HISTORY_STATUS: result.status,
            ATTR_DAYS_OF_HISTORY: result.days,
        }
