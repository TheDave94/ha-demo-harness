"""WebSocket API for the PollenWatch frontend card.

Single command for now: ``pollenwatch/config`` — returns the per-config-entry
species selection and the user's default card layout. The bundled Lovelace card
(v2.4+) calls this on connect so the no-oriel HACS user never has to type YAML
to get a multi-species view.

Registration is idempotent and one-shot per HA boot: the integration registers
once on first ``async_setup_entry`` call via :func:`async_register`. Older card
builds that never reach this endpoint fall back to a ``hass.states`` scan —
that contract lives in the card, not here.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import (
    ALLOWED_LAYOUTS,
    CONF_DEFAULT_LAYOUT,
    CONF_SELECTED_SPECIES,
    DEFAULT_LAYOUT,
    DEFAULT_SELECTED_SPECIES,
    DOMAIN,
)

_REGISTERED_KEY = "pollenwatch_ws_registered"

_WS_TYPE_CONFIG = "pollenwatch/config"


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register WS commands once per HA boot. Idempotent.

    Called from ``async_setup_entry`` so the registration follows the
    first entry's load — the integration has no ``async_setup`` and the
    HACS pattern is to do one-shot wiring on the first per-entry call.
    """
    if hass.data.get(_REGISTERED_KEY):
        return
    hass.data[_REGISTERED_KEY] = True
    websocket_api.async_register_command(hass, _ws_get_config)


@websocket_api.websocket_command(
    {
        vol.Required("type"): _WS_TYPE_CONFIG,
        vol.Required("entry_id"): str,
    }
)
@callback
def _ws_get_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return ``{selected_species, default_layout}`` for a PollenWatch entry.

    Unknown ``entry_id`` (or one not owned by this domain) returns a clean
    ``not_found`` error frame, not an exception — the card surfaces this as
    a soft fallback (use YAML / scan ``hass.states``) rather than a hard
    failure.
    """
    entry_id = msg["entry_id"]
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(
            msg["id"],
            "not_found",
            f"No PollenWatch config entry with id {entry_id!r}",
        )
        return

    options = entry.options or {}
    selected_species = list(
        options.get(CONF_SELECTED_SPECIES) or DEFAULT_SELECTED_SPECIES
    )
    raw_layout = options.get(CONF_DEFAULT_LAYOUT, DEFAULT_LAYOUT)
    # Defensive: an option set by an older build (or hand-edited storage)
    # may not match the v2.4+ enum. Treat unknown as the safe baseline so
    # the card never receives a layout it doesn't know how to render.
    default_layout = (
        raw_layout if raw_layout in ALLOWED_LAYOUTS else DEFAULT_LAYOUT
    )

    connection.send_result(
        msg["id"],
        {
            "selected_species": selected_species,
            "default_layout": default_layout,
        },
    )


__all__ = ["async_register"]
