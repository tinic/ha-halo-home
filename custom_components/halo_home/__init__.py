"""The Halo Home (Avi-on CSRmesh) integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant

from . import csrmesh
from .const import CONF_MACS, CONF_PASSPHRASE
from .coordinator import HaloCoordinator, HaloMesh

PLATFORMS = [Platform.LIGHT, Platform.SENSOR]

type HaloConfigEntry = ConfigEntry[HaloCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: HaloConfigEntry) -> bool:
    """Set up Halo Home from a config entry."""
    key = csrmesh.generate_key(entry.data[CONF_PASSPHRASE])
    mesh = HaloMesh(hass, key, entry.data[CONF_MACS])
    coordinator = HaloCoordinator(hass, entry, mesh)

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, lambda _evt: hass.async_create_task(mesh.disconnect())
        )
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HaloConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.mesh.disconnect()
    return unload_ok
