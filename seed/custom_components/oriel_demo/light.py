"""Extra registry-backed lights for the demo HA."""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

# (slug, friendly_name, initial_on)
LIGHTS = [
    ("reading_lamp", "Reading lamp", True),
    ("desk_lamp", "Desk lamp", True),
    ("hallway_ceiling", "Hallway ceiling", False),
    ("garage_light", "Garage light", False),
    ("bathroom_mirror", "Bathroom mirror", True),
    ("porch_light", "Porch light", False),
    ("bedside_lamp", "Bedside lamp", True),
    ("living_room_floor", "Living room floor lamp", True),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([OrielDemoLight(slug, name, on) for slug, name, on in LIGHTS])


class OrielDemoLight(LightEntity):
    _attr_should_poll = False
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_color_mode = ColorMode.ONOFF

    def __init__(self, slug: str, name: str, initial_on: bool) -> None:
        self._attr_unique_id = f"oriel_demo_light_{slug}"
        self._attr_name = name
        self._attr_is_on = initial_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()
