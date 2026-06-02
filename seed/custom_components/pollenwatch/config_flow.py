"""Config and options flow for PollenWatch.

Milestone 2: location + allergen selection for the Open-Meteo source, with a
coverage probe that refuses locations outside CAMS European coverage (detected
via the source's out-of-coverage status, which comes from Open-Meteo's HTTP 400
error body — not from all-zero values). Location is intentionally fixed after
setup (changing it would invalidate the future recent-percentile baseline);
allergens and the update interval are editable in the options flow.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    ALLERGENS,
    ALLOWED_LAYOUTS,
    CONF_ALLERGENS,
    CONF_API_KEY,
    CONF_COUNTRY,
    CONF_DEFAULT_LAYOUT,
    CONF_ENABLED,
    CONF_REGION,
    CONF_SELECTED_SPECIES,
    CONF_SENSITIVITY,
    CONF_SOURCES,
    CONF_STATION,
    CONF_UPDATE_INTERVAL,
    DEFAULT_ALLERGENS,
    DEFAULT_LAYOUT,
    DEFAULT_SENSITIVITY,
    DEFAULT_UPDATE_INTERVAL_MIN,
    DOMAIN,
    DWD_PARTREGIONS,
    MAX_SENSITIVITY,
    MAX_UPDATE_INTERVAL_MIN,
    MIN_SENSITIVITY,
    MIN_UPDATE_INTERVAL_MIN,
    SOURCE_DWD,
    SOURCE_EPIN,
    SOURCE_GOOGLE,
    SOURCE_METEOSWISS,
    SOURCE_OPEN_METEO,
    SOURCE_POLLENINFORMATION,
    new_sources_config,
)
from .coordinator import PollenWatchConfigEntry, _entry_option
from .region_defaults import (
    HIGH_POTENCY_CORE,
    get_locally_available_species,
    get_region_defaults,
    species_class_label,
)
from .sources import epin as epin_source
from .sources import meteoswiss as ms_source
from .sources.base import SourceAuthError, SourceError, SourceStatus
from .sources.dwd import DwdSource
from .sources.google import GoogleSource
from .sources.open_meteo import OpenMeteoSource
from .sources.polleninformation import SUPPORTED_COUNTRIES, PolleninformationSource
from .sources.species_registry import CANONICAL_SPECIES

CONF_LOCATION = "location"
CONF_ENABLE_PI = "enable_polleninformation"
CONF_ENABLE_DWD = "enable_dwd"
CONF_ENABLE_MS = "enable_meteoswiss"
CONF_ENABLE_EPIN = "enable_epin"
CONF_ENABLE_GOOGLE = "enable_google"
# Distinct form field so it doesn't collide with polleninformation's api_key.
CONF_GOOGLE_API_KEY = "google_api_key"
# Species-selection step (v3) — quick-pick toggle that replaces the
# user's selection with the cross-validated core (8 species). One flat
# form per Q2 decision (no Min-HA bump for grouped SelectSelector).
CONF_QUICK_PICK_CORE = "use_cross_validated_core"


def _station_label(stations: dict[str, tuple[str, float, float]], code: str) -> str:
    """Human label for a stored station code, or a placeholder if unset."""
    if code and code in stations:
        return f"{stations[code][0]} ({code})"
    return "auto-detected when enabled"

def _species_selector(country: str | None) -> selector.SelectSelector:
    """Build a species multi-select for this country.

    Locally-available filter: only species whose source set includes at
    least one source CAPABLE of being enabled here (OM + Google always;
    PI/DWD/MS/ePIN per country). Label is class-prefixed + source-count
    annotated, e.g. ``[Tree] Hazel (5/6 sources)``, sorted by class then
    canonical key so the dropdown reads as grouped (no Min-HA bump
    needed for true UI grouping).
    """
    keys = get_locally_available_species(country)
    # Sort by (class, key) so the flat list visually clusters per class.
    keys = sorted(keys, key=lambda k: (
        ("tree", "grass", "herb", "spore").index(
            str(CANONICAL_SPECIES[k].class_)
        ),
        k,
    ))
    options = []
    for key in keys:
        info = CANONICAL_SPECIES[key]
        max_n = len(info.sources)
        label = (
            f"[{species_class_label(key)}] "
            f"{info.common} ({max_n}/6 sources)"
        )
        options.append(selector.SelectOptionDict(value=key, label=label))
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
        )
    )

_INTERVAL_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=MIN_UPDATE_INTERVAL_MIN,
        max=MAX_UPDATE_INTERVAL_MIN,
        step=15,
        unit_of_measurement="min",
        mode=selector.NumberSelectorMode.BOX,
    )
)

# HA's built-in country selector: ISO-2 values, names localised automatically,
# bounded to the countries polleninformation serves. No translation keys needed.
_COUNTRY_SELECTOR = selector.CountrySelector(
    selector.CountrySelectorConfig(countries=list(SUPPORTED_COUNTRIES))
)

_API_KEY_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)

_SENSITIVITY_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=MIN_SENSITIVITY,
        max=MAX_SENSITIVITY,
        step=0.1,
        mode=selector.NumberSelectorMode.SLIDER,
    )
)


def _sensitivity_field(species: str) -> str:
    return f"{CONF_SENSITIVITY}_{species}"


async def _async_probe_polleninformation(
    hass, latitude: float, longitude: float, country: str, api_key: str,
    allergens: list[str],
) -> str | None:
    """Return a config-flow error key if polleninformation can't be used."""
    source = PolleninformationSource(
        latitude, longitude, country, api_key, allergens
    )
    try:
        result = await source.async_fetch(session=async_get_clientsession(hass))
    except SourceAuthError:
        return "invalid_api_key"
    except SourceError:
        return "cannot_connect"
    if result.status is SourceStatus.OUT_OF_COVERAGE:
        return "out_of_coverage"
    return None


_LAYOUT_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(value=v, label=v.capitalize())
            for v in ALLOWED_LAYOUTS
        ],
        mode=selector.SelectSelectorMode.DROPDOWN,
        translation_key="default_layout",
    )
)


_DWD_REGION_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(value=str(pid), label=name)
            for pid, name in DWD_PARTREGIONS.items()
        ],
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)


async def _async_probe_dwd(
    hass, latitude: float, longitude: float, region_id: int, allergens: list[str]
) -> str | None:
    """Return a config-flow error key if DWD can't be used for this location."""
    source = DwdSource(latitude, longitude, region_id, allergens)
    try:
        result = await source.async_fetch(session=async_get_clientsession(hass))
    except SourceError:
        return "cannot_connect"
    if result.status is SourceStatus.OUT_OF_COVERAGE:
        return "out_of_coverage"
    return None


async def _async_probe_meteoswiss(
    hass, latitude: float, longitude: float, allergens: list[str]
) -> tuple[str | None, str]:
    """Probe MeteoSwiss; return (error_key | None, resolved_station_code)."""
    source = ms_source.MeteoSwissSource(latitude, longitude, allergens)
    if source.station is None:
        return "out_of_coverage", ""
    try:
        result = await source.async_fetch(session=async_get_clientsession(hass))
    except SourceError:
        return "cannot_connect", ""
    if result.status is SourceStatus.OUT_OF_COVERAGE:
        return "out_of_coverage", ""
    return None, source.station


async def _async_probe_epin(
    hass, latitude: float, longitude: float, allergens: list[str]
) -> tuple[str | None, str]:
    """Probe ePIN; return (error_key | None, resolved_station_code)."""
    source = epin_source.EpinSource(latitude, longitude, allergens)
    if source.station is None:
        return "out_of_coverage", ""
    try:
        result = await source.async_fetch(session=async_get_clientsession(hass))
    except SourceError:
        return "cannot_connect", ""
    if result.status is SourceStatus.OUT_OF_COVERAGE:
        return "out_of_coverage", ""
    return None, source.station


async def _async_probe_google(
    hass, latitude: float, longitude: float, api_key: str, allergens: list[str]
) -> str | None:
    """Return a config-flow error key if Google can't be used."""
    source = GoogleSource(latitude, longitude, api_key, allergens)
    try:
        await source.async_fetch(session=async_get_clientsession(hass))
    except SourceAuthError:
        return "invalid_api_key"
    except SourceError:
        return "cannot_connect"
    return None


async def _async_probe_coverage(
    hass, latitude: float, longitude: float, allergens: list[str]
) -> str | None:
    """Return an error key if the location can't be used, else None."""
    source = OpenMeteoSource(
        latitude, longitude, allergens, past_days=0, forecast_days=1
    )
    try:
        result = await source.async_fetch(session=async_get_clientsession(hass))
    except SourceError:
        return "cannot_connect"
    if result.status is SourceStatus.OUT_OF_COVERAGE:
        return "out_of_coverage"
    return None


class PollenWatchConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of a PollenWatch config entry."""

    # v3 (v2.0 release): storage key for species selection is now
    # CONF_SELECTED_SPECIES, not CONF_ALLERGENS. Old entries migrate
    # losslessly via async_migrate_entry. Onboarding is now two-step:
    # location (this step) → species (region-aware multi-select +
    # cross-validated quick-pick toggle).
    VERSION = 3

    def __init__(self) -> None:
        # Carried between steps. Set in async_step_user; consumed in
        # async_step_species when creating the entry.
        self._latitude: float | None = None
        self._longitude: float | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: location only. Probes Open-Meteo coverage, then routes
        to the species step. Allergen selection no longer lives here."""
        errors: dict[str, str] = {}

        if user_input is not None:
            location = user_input[CONF_LOCATION]
            latitude = location[CONF_LATITUDE]
            longitude = location[CONF_LONGITUDE]
            await self.async_set_unique_id(f"{latitude:.4f}_{longitude:.4f}")
            self._abort_if_unique_id_configured()
            # Probe with any OM-supported species — just a connectivity +
            # coverage check (OM silent-drops unknown allergens in v2+).
            error = await _async_probe_coverage(
                self.hass, latitude, longitude, list(DEFAULT_ALLERGENS)
            )
            if error:
                errors["base"] = error
            else:
                self._latitude = latitude
                self._longitude = longitude
                return await self.async_step_species()

        suggested_location = {
            CONF_LATITUDE: self.hass.config.latitude,
            CONF_LONGITUDE: self.hass.config.longitude,
        }
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_LOCATION, default=suggested_location
                ): selector.LocationSelector(
                    selector.LocationSelectorConfig(radius=False)
                ),
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def async_step_species(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: region-aware species multi-select + cross-validated
        quick-pick toggle. The toggle, when set, OVERRIDES the multi-
        select and writes the 8 cross-validated species (alder, birch,
        grass, hazel, mugwort, ragweed, olive, ash) — the one-click sane
        default for users who don't want to think about it.

        Only locally-available species appear (those with at least one
        source capable of being enabled at this country). alternaria
        deliberately never pre-selected (opt-in-only spore decision).
        """
        errors: dict[str, str] = {}
        country = self.hass.config.country
        available = set(get_locally_available_species(country))
        defaults = get_region_defaults(country)  # already filtered

        if user_input is not None:
            if user_input.get(CONF_QUICK_PICK_CORE, False):
                # Quick-pick wins. Take the 8 core species intersected
                # with what's available at this location.
                species = [s for s in HIGH_POTENCY_CORE if s in available]
            else:
                species = list(user_input.get(CONF_SELECTED_SPECIES, []))
            if not species:
                errors[CONF_SELECTED_SPECIES] = "no_species"
            else:
                return self.async_create_entry(
                    title=(
                        f"PollenWatch ({self._latitude:.3f}, "
                        f"{self._longitude:.3f})"
                    ),
                    data={
                        CONF_LATITUDE: self._latitude,
                        CONF_LONGITUDE: self._longitude,
                        CONF_SELECTED_SPECIES: species,
                    },
                    options={CONF_SOURCES: new_sources_config()},
                )

        schema = vol.Schema({
            vol.Required(
                CONF_SELECTED_SPECIES, default=defaults,
            ): _species_selector(country),
            vol.Optional(
                CONF_QUICK_PICK_CORE, default=False,
            ): selector.BooleanSelector(),
        })
        return self.async_show_form(
            step_id="species",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "country": country or "(unset)",
                "default_count": str(len(defaults)),
                "available_count": str(len(available)),
                "core_count": str(len([
                    s for s in HIGH_POTENCY_CORE if s in available
                ])),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: PollenWatchConfigEntry,
    ) -> PollenWatchOptionsFlow:
        return PollenWatchOptionsFlow()


class PollenWatchOptionsFlow(OptionsFlow):
    """Edit allergens, interval, and the polleninformation source after setup.

    Location is fixed (remove + re-add to change it).
    """

    def _supported_default_country(self) -> str | None:
        country = (self.hass.config.country or "").upper()
        return country if country in SUPPORTED_COUNTRIES else None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.config_entry
        sources = _entry_option(entry, CONF_SOURCES, {})
        pi_cfg = sources.get(SOURCE_POLLENINFORMATION, {})
        dwd_cfg = sources.get(SOURCE_DWD, {})
        ms_cfg = sources.get(SOURCE_METEOSWISS, {})
        epin_cfg = sources.get(SOURCE_EPIN, {})
        google_cfg = sources.get(SOURCE_GOOGLE, {})

        if user_input is not None:
            allergens = user_input[CONF_SELECTED_SPECIES]
            default_layout = user_input.get(CONF_DEFAULT_LAYOUT, DEFAULT_LAYOUT)
            if default_layout not in ALLOWED_LAYOUTS:
                # voluptuous already rejects via the selector, but keep a
                # defensive guard so a malformed programmatic submission
                # cannot poison the stored option.
                errors[CONF_DEFAULT_LAYOUT] = "invalid_layout"
            enable_pi = user_input.get(CONF_ENABLE_PI, False)
            country = user_input.get(CONF_COUNTRY)
            api_key = user_input.get(CONF_API_KEY, "")
            enable_dwd = user_input.get(CONF_ENABLE_DWD, False)
            region_raw = user_input.get(CONF_REGION)
            enable_ms = user_input.get(CONF_ENABLE_MS, False)
            enable_epin = user_input.get(CONF_ENABLE_EPIN, False)
            enable_google = user_input.get(CONF_ENABLE_GOOGLE, False)
            google_api_key = user_input.get(CONF_GOOGLE_API_KEY, "")
            # Resolved nearest-station codes (carried over unless re-probed).
            ms_station = ms_cfg.get(CONF_STATION, "")
            epin_station = epin_cfg.get(CONF_STATION, "")
            latitude = entry.data[CONF_LATITUDE]
            longitude = entry.data[CONF_LONGITUDE]

            if not allergens:
                errors[CONF_SELECTED_SPECIES] = "no_species"
            if enable_pi:
                if not country:
                    errors[CONF_COUNTRY] = "country_required"
                if not api_key:
                    errors[CONF_API_KEY] = "api_key_required"
                if not errors:
                    error = await _async_probe_polleninformation(
                        self.hass, latitude, longitude, country, api_key, allergens
                    )
                    if error:
                        errors["base"] = error
            if enable_dwd:
                if region_raw in (None, ""):
                    errors[CONF_REGION] = "region_required"
                elif not errors:
                    error = await _async_probe_dwd(
                        self.hass, latitude, longitude, int(region_raw), allergens
                    )
                    if error:
                        errors["base"] = error
            if enable_ms and not errors:
                error, ms_station = await _async_probe_meteoswiss(
                    self.hass, latitude, longitude, allergens
                )
                if error:
                    errors["base"] = error
            if enable_epin and not errors:
                error, epin_station = await _async_probe_epin(
                    self.hass, latitude, longitude, allergens
                )
                if error:
                    errors["base"] = error
            if enable_google:
                if not google_api_key:
                    errors[CONF_GOOGLE_API_KEY] = "api_key_required"
                elif not errors:
                    error = await _async_probe_google(
                        self.hass, latitude, longitude, google_api_key, allergens
                    )
                    if error:
                        errors["base"] = error

            if not errors:
                sensitivity = {
                    species: user_input[_sensitivity_field(species)]
                    for species in ALLERGENS
                    if _sensitivity_field(species) in user_input
                }
                return self.async_create_entry(
                    data={
                        # v3 storage key. Form-field upstream keeps using
                        # CONF_ALLERGENS so strings.json translations resolve.
                        CONF_SELECTED_SPECIES: allergens,
                        CONF_DEFAULT_LAYOUT: default_layout,
                        CONF_UPDATE_INTERVAL: user_input[CONF_UPDATE_INTERVAL],
                        CONF_SENSITIVITY: sensitivity,
                        CONF_SOURCES: {
                            SOURCE_OPEN_METEO: {CONF_ENABLED: True},
                            SOURCE_POLLENINFORMATION: {
                                CONF_ENABLED: enable_pi,
                                CONF_API_KEY: api_key if enable_pi else "",
                                CONF_COUNTRY: country if enable_pi else "",
                            },
                            SOURCE_DWD: {
                                CONF_ENABLED: enable_dwd,
                                CONF_REGION: int(region_raw)
                                if (enable_dwd and region_raw not in (None, ""))
                                else "",
                            },
                            SOURCE_METEOSWISS: {
                                CONF_ENABLED: enable_ms,
                                CONF_STATION: ms_station if enable_ms else "",
                            },
                            SOURCE_EPIN: {
                                CONF_ENABLED: enable_epin,
                                CONF_STATION: epin_station if enable_epin else "",
                            },
                            SOURCE_GOOGLE: {
                                CONF_ENABLED: enable_google,
                                CONF_API_KEY: google_api_key if enable_google else "",
                            },
                        },
                    }
                )

        # v3 storage key. Falls back to legacy CONF_ALLERGENS only if the
        # v3 key is entirely absent — explicit empty stays empty (matches
        # the coordinator's reader; see build_coordinators for rationale).
        current_allergens = _entry_option(entry, CONF_SELECTED_SPECIES, None)
        if current_allergens is None:
            current_allergens = _entry_option(
                entry, CONF_ALLERGENS, DEFAULT_ALLERGENS
            )
        current_interval = _entry_option(
            entry, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MIN
        )
        # Entries created before v2.4 won't have CONF_DEFAULT_LAYOUT — read
        # with the LAYOUT_GAUGE fallback so the form pre-fills with the
        # existing visual (the only layout the pre-v2.4 card knew).
        current_layout = _entry_option(entry, CONF_DEFAULT_LAYOUT, DEFAULT_LAYOUT)
        if current_layout not in ALLOWED_LAYOUTS:
            current_layout = DEFAULT_LAYOUT
        default_country = pi_cfg.get(CONF_COUNTRY) or self._supported_default_country()
        current_sensitivity = _entry_option(entry, CONF_SENSITIVITY, {})
        schema_dict = {
            vol.Required(
                CONF_SELECTED_SPECIES, default=current_allergens
            ): _species_selector(self.hass.config.country),
            vol.Required(
                CONF_DEFAULT_LAYOUT, default=current_layout
            ): _LAYOUT_SELECTOR,
            vol.Required(
                CONF_UPDATE_INTERVAL, default=current_interval
            ): _INTERVAL_SELECTOR,
            vol.Required(
                CONF_ENABLE_PI, default=pi_cfg.get(CONF_ENABLED, False)
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_COUNTRY,
                description={"suggested_value": default_country},
            ): _COUNTRY_SELECTOR,
            vol.Optional(
                CONF_API_KEY,
                description={"suggested_value": pi_cfg.get(CONF_API_KEY) or None},
            ): _API_KEY_SELECTOR,
            vol.Required(
                CONF_ENABLE_DWD, default=dwd_cfg.get(CONF_ENABLED, False)
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_REGION,
                description={
                    "suggested_value": str(dwd_cfg[CONF_REGION])
                    if dwd_cfg.get(CONF_REGION) not in (None, "")
                    else None
                },
            ): _DWD_REGION_SELECTOR,
            vol.Required(
                CONF_ENABLE_MS, default=ms_cfg.get(CONF_ENABLED, False)
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_ENABLE_EPIN, default=epin_cfg.get(CONF_ENABLED, False)
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_ENABLE_GOOGLE, default=google_cfg.get(CONF_ENABLED, False)
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_GOOGLE_API_KEY,
                description={
                    "suggested_value": google_cfg.get(CONF_API_KEY) or None
                },
            ): _API_KEY_SELECTOR,
        }
        # Personal sensitivity multipliers (one per species).
        for species in ALLERGENS:
            schema_dict[
                vol.Optional(
                    _sensitivity_field(species),
                    default=current_sensitivity.get(species, DEFAULT_SENSITIVITY),
                )
            ] = _SENSITIVITY_SELECTOR
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
            description_placeholders={
                "ms_station": _station_label(
                    ms_source.STATIONS, ms_cfg.get(CONF_STATION, "")
                ),
                "epin_station": _station_label(
                    epin_source.STATIONS, epin_cfg.get(CONF_STATION, "")
                ),
            },
        )
