"""Config flow for Halo Home.

Two ways in, and both matter:

* **Cloud login** — the easy path, while Avi-on's API is still up. Fetches the
  static mesh passphrase once and copies it into the config entry.
* **Restore from backup** — reads the JSON produced by `tools/avion_backup.py`.
  This is the path that still works after the cloud is gone. Do not treat it as
  the fallback; it is the reason this integration has a future.

Nothing here runs at runtime. Once an entry exists, the cloud is never contacted
again.
"""
from __future__ import annotations

import json
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import products
from .cloud import AvionAuthError, AvionCloudError, async_fetch_locations
from .const import (
    CONF_DEVICES,
    CONF_MACS,
    CONF_PASSPHRASE,
    CONF_PID,
    DOMAIN,
)

CONF_BACKUP_PATH = "backup_path"


def _load_backup(path: str) -> list[dict[str, Any]]:
    """Parse an avion_backup.json into the same shape as the cloud client returns."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)

    locations: list[dict[str, Any]] = []
    for entry in data["locations"]:
        location = entry["location"]
        passphrase = location.get("passphrase")
        devices = products.dedupe_names(
            [
                products.parse_device(d)
                for d in entry["abstract_devices"]
                if products.is_light(d)
            ]
        )
        if not passphrase or not devices:
            continue
        locations.append(
            {
                "pid": str(location["pid"]),
                "name": location.get("name") or f"Location {location['pid']}",
                "passphrase": passphrase,
                "devices": devices,
            }
        )

    if not locations:
        raise ValueError("no usable location in backup")
    return locations


class HaloConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up one Avi-on location as a mesh of lights."""

    VERSION = 1

    def __init__(self) -> None:
        self._locations: list[dict[str, Any]] = []

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """A Halo/Avi-on node is in range — offer to set up the mesh."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["cloud", "backup"])

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Log in to Avi-on once to retrieve the mesh passphrase."""
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            try:
                locations = await async_fetch_locations(
                    session, user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
                )
            except AvionAuthError:
                errors["base"] = "invalid_auth"
            except AvionCloudError:
                errors["base"] = "cannot_connect"
            else:
                return await self._async_pick(locations)

        return self.async_show_form(
            step_id="cloud",
            data_schema=vol.Schema(
                {vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}
            ),
            errors=errors,
        )

    async def async_step_backup(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Restore from an avion_backup.json — works with the cloud dead."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                locations = await self.hass.async_add_executor_job(
                    _load_backup, user_input[CONF_BACKUP_PATH]
                )
            except FileNotFoundError:
                errors["base"] = "not_found"
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                errors["base"] = "invalid_backup"
            else:
                return await self._async_pick(locations)

        return self.async_show_form(
            step_id="backup",
            data_schema=vol.Schema(
                {vol.Required(CONF_BACKUP_PATH, default="/config/avion_backup.json"): str}
            ),
            errors=errors,
        )

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which location to set up, when the account has several."""
        if user_input is not None:
            chosen = next(
                loc for loc in self._locations if loc["pid"] == user_input[CONF_PID]
            )
            return await self._async_create(chosen)

        return self.async_show_form(
            step_id="location",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PID): vol.In(
                        {loc["pid"]: loc["name"] for loc in self._locations}
                    )
                }
            ),
        )

    async def _async_pick(self, locations: list[dict[str, Any]]) -> ConfigFlowResult:
        self._locations = locations
        if len(locations) == 1:
            return await self._async_create(locations[0])
        return await self.async_step_location()

    async def _async_create(self, location: dict[str, Any]) -> ConfigFlowResult:
        await self.async_set_unique_id(location["pid"])
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=location["name"],
            data={
                CONF_PID: location["pid"],
                CONF_PASSPHRASE: location["passphrase"],
                CONF_DEVICES: location["devices"],
                CONF_MACS: [d["mac"] for d in location["devices"]],
            },
        )
