"""The PollenWatch integration.

This package entry point is deliberately free of top-level ``homeassistant``
imports: importing the package (and therefore the source layer under
``sources/``) must not require Home Assistant, so the data layer stays testable
in isolation. The Home Assistant API is imported inside the entry functions,
which only run when HA loads the integration.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .const import DOMAIN, PLATFORMS

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import PollenWatchConfigEntry

_LOGGER = logging.getLogger(__name__)

# Auto-register the bundled Lovelace card as a frontend resource (once per HA
# boot). The card lives at custom_components/pollenwatch/frontend/ and is served
# via a registered static path; cache-busted by manifest version. There is no
# public "remove_extra_js_url" in HA, so this persists for the session even on
# unload (benign; cleared on restart). HACS uses the same pattern.
_CARD_URL_BASE = "/pollenwatch_card_static"
_CARD_FILE = "pollenwatch-card.js"
_CARD_LOADED_KEY = "pollenwatch_card_registered"

__all__ = [
    "DOMAIN",
    "async_migrate_entry",
    "async_setup_entry",
    "async_unload_entry",
]


async def async_migrate_entry(
    hass: HomeAssistant, entry: PollenWatchConfigEntry
) -> bool:
    """Migrate a config entry to the current version.

    v1 → v2 (multi-source): additively add the per-source enablement config to
    options. Purely additive — no existing key is moved or dropped — so the live
    single-source (Open-Meteo) setup is preserved. Idempotent.

    v2 → v3 (species selection): rename the storage key for the user's
    selected species from CONF_ALLERGENS ("allergens") to
    CONF_SELECTED_SPECIES ("selected_species"). Lossless: the list contents
    are preserved exactly; entity unique_ids are keyed on species canonical
    keys so no entity_id churn occurs. The v1.x → v3 path runs both legs
    sequentially.
    """
    from .const import (
        CONF_SELECTED_SPECIES,
        CONF_SOURCES,
        DEFAULT_SELECTED_SPECIES,
        new_sources_config,
    )

    # Literal pre-v3 key. Once CONF_ALLERGENS is dropped (release after next)
    # this can move into a private helper; for now it lives here as the only
    # call site that needs the legacy string.
    legacy_allergens_key = "allergens"

    if entry.version > 3:
        # Downgrade (e.g. user rolled back) — refuse rather than corrupt.
        return False

    if entry.version == 1:
        options = {**entry.options}
        options.setdefault(CONF_SOURCES, new_sources_config())
        hass.config_entries.async_update_entry(
            entry, options=options, version=2
        )

    if entry.version == 2:
        data = {**entry.data}
        options = {**entry.options}
        # Recover the species list from either the legacy key or the new
        # key (the new key may already be present if the entry was created
        # by a v3 config flow but not yet bumped — defensive).
        species_list = (
            options.get(CONF_SELECTED_SPECIES)
            or data.get(CONF_SELECTED_SPECIES)
            or options.pop(legacy_allergens_key, None)
            or data.pop(legacy_allergens_key, None)
        )
        if not species_list:
            # Defensive: a v2 entry should always have the key somewhere.
            # Fall back to the canonical 6 so the install doesn't lose
            # entities silently.
            species_list = list(DEFAULT_SELECTED_SPECIES)
            _LOGGER.warning(
                "PollenWatch entry %s migrating to v3 with no recoverable "
                "species list; falling back to canonical v1 set",
                entry.entry_id,
            )
        # Always write the new key; clean up any stale legacy key in
        # both data and options (idempotency).
        options[CONF_SELECTED_SPECIES] = species_list
        options.pop(legacy_allergens_key, None)
        data.pop(legacy_allergens_key, None)
        hass.config_entries.async_update_entry(
            entry, data=data, options=options, version=3
        )

    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve + register the bundled Lovelace card. Idempotent per HA boot."""
    if hass.data.get(_CARD_LOADED_KEY):
        return
    # HTTP isn't available in some non-frontend HA contexts (e.g. unit-test
    # harnesses that don't load the http component) — no-op there.
    if getattr(hass, "http", None) is None:
        return
    hass.data[_CARD_LOADED_KEY] = True

    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    frontend_dir = Path(__file__).parent / "frontend"
    if not (frontend_dir / _CARD_FILE).is_file():
        _LOGGER.warning(
            "PollenWatch card bundle not found at %s; card will not be served",
            frontend_dir / _CARD_FILE,
        )
        return

    # Cache-bust via manifest version so a HACS update reloads the JS in the
    # browser. Read in an executor — sync I/O on the event loop trips HA's
    # blocking-call detector.
    def _read_version() -> str:
        try:
            data = json.loads((Path(__file__).parent / "manifest.json").read_text())
            return data.get("version", "0")
        except OSError:
            return "0"

    version = await hass.async_add_executor_job(_read_version)

    await hass.http.async_register_static_paths(
        [StaticPathConfig(_CARD_URL_BASE, str(frontend_dir), False)]
    )
    add_extra_js_url(hass, f"{_CARD_URL_BASE}/{_CARD_FILE}?v={version}")
    _LOGGER.info("PollenWatch Lovelace card registered (v%s)", version)


async def async_setup_entry(
    hass: HomeAssistant, entry: PollenWatchConfigEntry
) -> bool:
    """Set up PollenWatch from a config entry."""
    from homeassistant.const import Platform

    from .const import SOURCE_OPEN_METEO
    from .coordinator import (
        PollenWatchAnalyticsCoordinator,
        PollenWatchData,
        build_coordinators,
    )

    # One install delivers the integration AND the Lovelace card — auto-register
    # the card on first config-entry load (no-op on subsequent entries).
    await _async_register_card(hass)

    # Register the frontend-facing WS API once per HA boot. Idempotent.
    from .websocket_api import async_register as _async_register_ws
    _async_register_ws(hass)

    coordinators = build_coordinators(hass, entry)
    # Open-Meteo is the primary, keyless source: it must be ready or the entry
    # retries. Optional sources (polleninformation) refresh non-blockingly so a
    # failure there leaves their sensors unavailable without taking the entry down.
    await coordinators[SOURCE_OPEN_METEO].async_config_entry_first_refresh()
    for source_key, coordinator in coordinators.items():
        if source_key != SOURCE_OPEN_METEO:
            await coordinator.async_refresh()

    # Analytics (derived) coordinator reads the source coordinators above.
    analytics = PollenWatchAnalyticsCoordinator(hass, entry, coordinators)
    await analytics.async_refresh()

    entry.runtime_data = PollenWatchData(
        coordinators=coordinators, analytics=analytics
    )

    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform(p) for p in PLATFORMS]
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: PollenWatchConfigEntry
) -> bool:
    """Unload a config entry."""
    from homeassistant.const import Platform

    return await hass.config_entries.async_unload_platforms(
        entry, [Platform(p) for p in PLATFORMS]
    )


async def _async_reload_entry(
    hass: HomeAssistant, entry: PollenWatchConfigEntry
) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
