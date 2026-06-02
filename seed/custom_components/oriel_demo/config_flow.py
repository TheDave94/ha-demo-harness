"""Import-only config flow — yaml triggers one entry, that's it."""
from __future__ import annotations

from homeassistant.config_entries import ConfigFlow

DOMAIN = "oriel_demo"


class OrielDemoFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_import(self, _data):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Oriel Demo Extensions", data={})

    async def async_step_user(self, _user_input=None):
        return await self.async_step_import(None)
