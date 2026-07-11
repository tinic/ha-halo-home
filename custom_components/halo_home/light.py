"""Light platform for Halo Home / Avi-on fixtures.

The platform spans three kinds of load: tunable white (dim + Kelvin), dim-only,
and switch-only. What a given fixture is comes from `products.resolve()`; see
that module for how it is decided.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import csrmesh, products
from .const import CONF_DEVICES, DOMAIN
from .coordinator import HaloCoordinator

# Brightness used for a plain "on" with no level given.
_FULL = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one light entity per fixture."""
    coordinator: HaloCoordinator = entry.runtime_data
    async_add_entities(HaloLight(coordinator, dev) for dev in entry.data[CONF_DEVICES])


class HaloLight(CoordinatorEntity[HaloCoordinator], LightEntity):
    """A single Avi-on load, addressed by its mesh avid."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator: HaloCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._avid: int = device["avid"]
        self._attr_unique_id = f"{DOMAIN}_{self._avid}"

        # Config entries written before capabilities were resolved carry no
        # product data. Treat those as unknown rather than assuming the fixtures
        # this was developed against, and let the mesh evidence below settle it.
        dimmable = device.get("dimmable", True)
        color_temp = device.get("color_temp", False)
        known = device.get("known", False)

        # An unknown product that answered a COLOR read *is* tunable white,
        # whatever the cloud did or didn't say about it. The coordinator's first
        # poll has already run by the time this platform is set up, so the
        # evidence is available right here.
        if not known and not color_temp and "color_temp" in self._report_of(coordinator):
            color_temp = True

        if color_temp:
            self._attr_supported_color_modes = {ColorMode.COLOR_TEMP}
            self._attr_color_mode = ColorMode.COLOR_TEMP
        elif dimmable:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF

        self._attr_min_color_temp_kelvin = device.get(
            "min_kelvin", products.DEFAULT_MIN_KELVIN
        )
        self._attr_max_color_temp_kelvin = device.get(
            "max_kelvin", products.DEFAULT_MAX_KELVIN
        )

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(self._avid))},
            name=device["name"],
            manufacturer="Eaton / Avi-on",
            model=device.get("model") or "Avi-on device",
        )

    def _report_of(self, coordinator: HaloCoordinator) -> dict:
        return (coordinator.data or {}).get(self._avid, {})

    @property
    def _report(self) -> dict:
        return self._report_of(self.coordinator)

    @property
    def _dimmable(self) -> bool:
        return ColorMode.ONOFF not in self._attr_supported_color_modes

    @property
    def available(self) -> bool:
        # Available once we've heard this fixture report at least once.
        return super().available and self._avid in (self.coordinator.data or {})

    @property
    def is_on(self) -> bool | None:
        brightness = self._report.get("brightness")
        return None if brightness is None else brightness > 0

    @property
    def brightness(self) -> int | None:
        if not self._dimmable:
            return None
        return self._report.get("brightness") or None

    @property
    def color_temp_kelvin(self) -> int | None:
        if ColorMode.COLOR_TEMP not in self._attr_supported_color_modes:
            return None
        return self._report.get("color_temp")

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            await self.coordinator.mesh.send(
                csrmesh.set_color_payload(self._avid, kwargs[ATTR_COLOR_TEMP_KELVIN])
            )
        if ATTR_BRIGHTNESS in kwargs:
            await self.coordinator.mesh.send(
                csrmesh.set_brightness_payload(self._avid, kwargs[ATTR_BRIGHTNESS])
            )
        elif ATTR_COLOR_TEMP_KELVIN not in kwargs:
            # Plain on. There is no on/off opcode on this platform: full brightness
            # is "on", including for the switch-only loads.
            await self.coordinator.mesh.send(csrmesh.set_brightness_payload(self._avid, _FULL))
        self._optimistic(**kwargs)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.mesh.send(csrmesh.set_brightness_payload(self._avid, 0))
        self._report_local({"brightness": 0})

    @callback
    def _optimistic(self, **kwargs: Any) -> None:
        update: dict = {}
        if ATTR_BRIGHTNESS in kwargs:
            update["brightness"] = kwargs[ATTR_BRIGHTNESS]
        elif ATTR_COLOR_TEMP_KELVIN not in kwargs:
            update["brightness"] = _FULL
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            update["color_temp"] = kwargs[ATTR_COLOR_TEMP_KELVIN]
        self._report_local(update)

    @callback
    def _report_local(self, update: dict) -> None:
        """Optimistically fold a change into coordinator state and write it."""
        data = dict(self.coordinator.data or {})
        data[self._avid] = {**data.get(self._avid, {}), **update}
        self.coordinator.async_set_updated_data(data)
