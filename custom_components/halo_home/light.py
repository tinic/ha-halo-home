"""Light platform for Halo Home / Avi-on fixtures and groups.

Two kinds of entity, both addressed the same way — by a mesh id:

* **A fixture** (`avid` >= 32896) is commanded unicast.
* **A group** (`avid` < 32896) is commanded with a *single broadcast packet* that
  every member obeys at once. That is the reason groups are worth exposing:
  switching a room through the group is one packet, where iterating its members
  is N packets and the lights visibly stagger.

Groups report no state of their own, so a group's state is derived from the
fixtures in it.
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
from .const import CONF_DEVICES, CONF_GROUPS, DOMAIN
from .coordinator import HaloCoordinator

# Brightness used for a plain "on" with no level given.
_FULL = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """One entity per fixture, plus one per Avi-on group."""
    coordinator: HaloCoordinator = entry.runtime_data
    entities: list[HaloEntity] = [
        HaloLight(coordinator, dev) for dev in entry.data[CONF_DEVICES]
    ]
    entities += [
        HaloGroup(coordinator, group) for group in entry.data.get(CONF_GROUPS) or ()
    ]
    async_add_entities(entities)


class HaloEntity(CoordinatorEntity[HaloCoordinator], LightEntity):
    """Shared command path for anything addressable on the mesh."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator: HaloCoordinator, avid: int, caps: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._avid = avid

        if caps.get("color_temp"):
            mode = ColorMode.COLOR_TEMP
        elif caps.get("dimmable", True):
            mode = ColorMode.BRIGHTNESS
        else:
            mode = ColorMode.ONOFF
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}

        self._attr_min_color_temp_kelvin = caps.get("min_kelvin", products.DEFAULT_MIN_KELVIN)
        self._attr_max_color_temp_kelvin = caps.get("max_kelvin", products.DEFAULT_MAX_KELVIN)

    @property
    def _dimmable(self) -> bool:
        return ColorMode.ONOFF not in self._attr_supported_color_modes

    @property
    def _tunable(self) -> bool:
        return ColorMode.COLOR_TEMP in self._attr_supported_color_modes

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

        update: dict = {}
        if ATTR_BRIGHTNESS in kwargs:
            update["brightness"] = kwargs[ATTR_BRIGHTNESS]
        elif ATTR_COLOR_TEMP_KELVIN not in kwargs:
            update["brightness"] = _FULL
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            update["color_temp"] = kwargs[ATTR_COLOR_TEMP_KELVIN]
        self._optimistic(update)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.mesh.send(csrmesh.set_brightness_payload(self._avid, 0))
        self._optimistic({"brightness": 0})

    @callback
    def _optimistic(self, update: dict) -> None:
        """Fold a just-sent change into coordinator state, pending the next poll."""
        if not update:
            return
        data = dict(self.coordinator.data or {})
        for avid in self._affects:
            data[avid] = {**data.get(avid, {}), **update}
        self.coordinator.async_set_updated_data(data)

    @property
    def _affects(self) -> list[int]:
        """The fixtures whose state this entity's commands change."""
        raise NotImplementedError


class HaloLight(HaloEntity):
    """A single Avi-on load, addressed by its mesh avid."""

    def __init__(self, coordinator: HaloCoordinator, device: dict[str, Any]) -> None:
        caps = dict(device)

        # Config entries written before capabilities were resolved carry no
        # product data. Treat those as unknown rather than assuming the fixtures
        # this was developed against, and let the mesh evidence settle it: an
        # unknown fixture that answered a COLOR read *is* tunable white. The
        # coordinator's first poll has already run by the time we get here.
        if not caps.get("known", False) and not caps.get("color_temp", False):
            report = (coordinator.data or {}).get(device["avid"], {})
            if "color_temp" in report:
                caps["color_temp"] = True

        super().__init__(coordinator, device["avid"], caps)
        self._attr_unique_id = f"{DOMAIN}_{self._avid}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(self._avid))},
            name=device["name"],
            manufacturer="Eaton / Avi-on",
            model=device.get("model") or "Avi-on device",
        )

    @property
    def _affects(self) -> list[int]:
        return [self._avid]

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
        if not self._dimmable:
            return None
        return self._report.get("brightness") or None

    @property
    def color_temp_kelvin(self) -> int | None:
        if not self._tunable:
            return None
        return self._report.get("color_temp")


class HaloGroup(HaloEntity):
    """An Avi-on group: one broadcast packet, every member obeys at once."""

    def __init__(self, coordinator: HaloCoordinator, group: dict[str, Any]) -> None:
        super().__init__(coordinator, group["avid"], group)
        self._members: list[int] = group["members"]
        self._attr_unique_id = f"{DOMAIN}_group_{self._avid}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"group_{self._avid}")},
            name=group["name"],
            manufacturer="Eaton / Avi-on",
            model="Group",
        )

    @property
    def _affects(self) -> list[int]:
        return self._members

    @property
    def _reports(self) -> list[dict]:
        data = self.coordinator.data or {}
        return [data[avid] for avid in self._members if avid in data]

    @property
    def available(self) -> bool:
        return super().available and bool(self._reports)

    @property
    def is_on(self) -> bool | None:
        levels = [r["brightness"] for r in self._reports if "brightness" in r]
        if not levels:
            return None
        return any(level > 0 for level in levels)

    @property
    def brightness(self) -> int | None:
        """Mean brightness of the members that are on — as HA's own light groups do."""
        if not self._dimmable:
            return None
        lit = [r["brightness"] for r in self._reports if r.get("brightness")]
        if not lit:
            return None
        return round(sum(lit) / len(lit))

    @property
    def color_temp_kelvin(self) -> int | None:
        if not self._tunable:
            return None
        temps = [r["color_temp"] for r in self._reports if r.get("color_temp")]
        if not temps:
            return None
        return round(sum(temps) / len(temps))
