"""Oriel demo extension — registry-backed extra lights + plants for the
demo HA. The plant integration is yaml-only (no async_setup_entry), so
we forward to the light platform normally, and register plant.* entities
directly against the entity registry + state machine.
"""
from __future__ import annotations

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

DOMAIN = "oriel_demo"
PLATFORMS = [Platform.LIGHT]

# (slug, friendly_name, state, moisture, temperature)
PLANTS = [
    ("oriel_monstera", "Monstera", "ok", 62, 21.4),
    ("oriel_fiddle_leaf", "Fiddle leaf", "problem", 31, 22.1),
    ("oriel_snake_plant", "Snake plant", "ok", 45, 20.8),
    ("oriel_pothos", "Pothos", "ok", 54, 21.6),
]


async def async_setup(hass: HomeAssistant, config) -> bool:
    if DOMAIN in config and not hass.config_entries.async_entries(DOMAIN):
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data={}
            )
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_plants(hass, entry)
    return True


def _register_plants(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register plant.* entities directly. The plant integration's
    quality_scale=internal yaml-only design has no async_setup_entry,
    so we can't forward; instead we own the entities outright."""
    registry = er.async_get(hass)
    for slug, name, state, moisture, temp in PLANTS:
        registry.async_get_or_create(
            domain="plant",
            platform=DOMAIN,
            unique_id=f"oriel_demo_plant_{slug}",
            suggested_object_id=slug,
            original_name=name,
            config_entry=entry,
        )
        entity_id = registry.async_get_entity_id("plant", DOMAIN, f"oriel_demo_plant_{slug}")
        if entity_id:
            hass.states.async_set(
                entity_id, state,
                {
                    "friendly_name": name,
                    "moisture": moisture,
                    "temperature": temp,
                    "problem": "none" if state == "ok" else "moisture low",
                    "icon": "mdi:flower",
                },
            )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
