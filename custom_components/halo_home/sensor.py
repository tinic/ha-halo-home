"""Sensor platform: each light fixture reports its own temperature.

Avi-on light fixtures answer a read of the THERMOMETER noun (0x27) with their
internal temperature in whole degrees Celsius. This is not exposed by any other
client. The reading tracks LED load — it rises under brightness and heat-soaks
briefly after switch-off — so it reflects the fixture, not the room.

A sensor is created for each light load, and is unavailable until that fixture
first reports a temperature. On/off-only devices (Smart Switches) drive no LED
and never report one, so they get no sensor rather than a permanently dead entity.
"""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICES, DOMAIN
from .coordinator import HaloCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """One temperature sensor per light fixture (not per on/off switch)."""
    coordinator: HaloCoordinator = entry.runtime_data
    async_add_entities(
        HaloTemperature(coordinator, dev)
        for dev in entry.data[CONF_DEVICES]
        if dev.get("dimmable", True)
    )


class HaloTemperature(CoordinatorEntity[HaloCoordinator], SensorEntity):
    """A fixture's internal temperature, in degrees Celsius."""

    _attr_has_entity_name = True
    _attr_translation_key = "temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: HaloCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._avid = device["avid"]
        self._attr_unique_id = f"{DOMAIN}_{self._avid}_temperature"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(self._avid))},
            name=device["name"],
            manufacturer="Eaton / Avi-on",
            model=device.get("model") or "Avi-on device",
        )

    @property
    def _temperature(self) -> int | None:
        return (self.coordinator.data or {}).get(self._avid, {}).get("temperature")

    @property
    def available(self) -> bool:
        # Available once this fixture has actually reported a temperature.
        return super().available and self._temperature is not None

    @property
    def native_value(self) -> int | None:
        return self._temperature
