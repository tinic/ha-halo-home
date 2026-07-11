"""Light platform for Halo Home fixtures."""
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

from . import csrmesh
from .const import CONF_DEVICES, DOMAIN, MAX_KELVIN, MIN_KELVIN
from .coordinator import HaloCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one light entity per fixture."""
    coordinator: HaloCoordinator = entry.runtime_data
    async_add_entities(
        HaloLight(coordinator, dev["avid"], dev["name"]) for dev in entry.data[CONF_DEVICES]
    )


class HaloLight(CoordinatorEntity[HaloCoordinator], LightEntity):
    """A single Halo/Avi-on tunable-white fixture, addressed by its mesh avid."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_color_mode = ColorMode.COLOR_TEMP
    _attr_supported_color_modes = {ColorMode.COLOR_TEMP}
    _attr_min_color_temp_kelvin = MIN_KELVIN
    _attr_max_color_temp_kelvin = MAX_KELVIN

    def __init__(self, coordinator: HaloCoordinator, avid: int, name: str) -> None:
        super().__init__(coordinator)
        self._avid = avid
        self._attr_unique_id = f"{DOMAIN}_{avid}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(avid))},
            name=name,
            manufacturer="Eaton / Halo Home",
            model="MicroEdge (HLB)",
        )

    @property
    def _report(self) -> dict:
        return (self.coordinator.data or {}).get(self._avid, {})

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
        brightness = self._report.get("brightness")
        return brightness or None

    @property
    def color_temp_kelvin(self) -> int | None:
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
            # Plain on: full brightness (or restore is host-side; 255 is a safe on).
            await self.coordinator.mesh.send(csrmesh.set_brightness_payload(self._avid, 255))
        await self._optimistic(**kwargs)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.mesh.send(csrmesh.set_brightness_payload(self._avid, 0))
        self._report_local({"brightness": 0})

    async def _optimistic(self, **kwargs: Any) -> None:
        update: dict = {}
        if ATTR_BRIGHTNESS in kwargs:
            update["brightness"] = kwargs[ATTR_BRIGHTNESS]
        elif ATTR_COLOR_TEMP_KELVIN not in kwargs:
            update["brightness"] = 255
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            update["color_temp"] = kwargs[ATTR_COLOR_TEMP_KELVIN]
        self._report_local(update)

    @callback
    def _report_local(self, update: dict) -> None:
        """Optimistically fold a change into coordinator state and write it."""
        data = dict(self.coordinator.data or {})
        data[self._avid] = {**data.get(self._avid, {}), **update}
        self.coordinator.async_set_updated_data(data)
