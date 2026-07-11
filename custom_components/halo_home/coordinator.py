"""Mesh connection manager + polling coordinator for Halo Home.

CSRmesh is a flood mesh: we hold ONE GATT connection to any reachable fixture and
it relays every command/query to the whole mesh. So a single persistent connection
(the "gateway") serves all lights, both for sending commands and for receiving
status notifications (including changes made at a physical wall dimmer).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import csrmesh
from .const import (
    CHAR_HIGH,
    CHAR_LOW,
    DOMAIN,
    POLL_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

_GATEWAY_NAME = "halo-home-mesh"
# Seconds to let status notifications stream in after a broadcast READ.
_READ_SETTLE = 1.5


class HaloMesh:
    """Owns the gateway BLE connection and speaks the mesh protocol."""

    def __init__(self, hass: HomeAssistant, key: bytes, macs: list[str]) -> None:
        self._hass = hass
        self._key = key
        self._macs = [m.upper() for m in macs]
        self._client: BleakClientWithServiceCache | None = None
        self._conn_lock = asyncio.Lock()
        self._op_lock = asyncio.Lock()
        # Unmatched fragments of long inbound packets, awaiting their other half.
        # BLE gives no ordering guarantee across the two characteristics, so the
        # low (20-byte) half and the short overflow can arrive in either order.
        self._lows: list[bytes] = []
        self._highs: list[bytes] = []
        self.state: dict[int, dict] = {}
        self._push_cbs: list = []

    def add_push_listener(self, cb) -> None:
        """Register a callback(state) fired when a notification updates state."""
        self._push_cbs.append(cb)

    def _find_device(self) -> BLEDevice | None:
        for mac in self._macs:
            dev = bluetooth.async_ble_device_from_address(self._hass, mac, connectable=True)
            if dev is not None:
                return dev
        return None

    async def _ensure_connected(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        async with self._conn_lock:
            if self._client is not None and self._client.is_connected:
                return
            device = self._find_device()
            if device is None:
                raise UpdateFailed("No Halo fixture is currently in Bluetooth range")
            _LOGGER.debug("Connecting to mesh gateway %s", device.address)
            client = await establish_connection(
                BleakClientWithServiceCache,
                device,
                _GATEWAY_NAME,
                disconnected_callback=self._on_disconnect,
                use_services_cache=True,
            )
            self._lows.clear()
            self._highs.clear()
            await client.start_notify(CHAR_LOW, self._on_notify)
            await client.start_notify(CHAR_HIGH, self._on_notify)
            self._client = client
            _LOGGER.debug("Mesh gateway connected via %s", device.address)

    @callback
    def _on_disconnect(self, _client) -> None:
        _LOGGER.debug("Mesh gateway disconnected")
        self._client = None
        self._lows.clear()
        self._highs.clear()

    @callback
    def _on_notify(self, _char, data: bytearray) -> None:
        """Reassemble fragments and ingest any complete, HMAC-valid packet.

        A long inbound packet arrives as a 20-byte fragment (the "low") plus a
        short overflow, and BLE does not guarantee which of the two characteristics
        delivers first — so either half may arrive before the other, and with
        several lights answering one broadcast they interleave. We buffer whichever
        half is unmatched and try each candidate pairing by HMAC; a wrong pairing
        simply fails to decode and is skipped.
        """
        frame = bytes(data)
        _LOGGER.debug(
            "notify %d bytes (%d lows, %d highs buffered)",
            len(frame),
            len(self._lows),
            len(self._highs),
        )
        if self._ingest(frame):  # a complete short packet delivered whole
            return
        if len(frame) == 20:
            # A low half. Try it against overflows already waiting, else buffer it.
            for j, high in enumerate(self._highs):
                if self._ingest(frame + high):
                    self._highs.pop(j)
                    return
            self._buffer(self._lows, frame)
        else:
            # A short overflow. Try it against lows already waiting, else buffer it.
            for i, low in enumerate(self._lows):
                if self._ingest(low + frame):
                    self._lows.pop(i)
                    return
            self._buffer(self._highs, frame)

    @staticmethod
    def _buffer(buf: list[bytes], frame: bytes) -> None:
        buf.append(frame)
        if len(buf) > 16:  # bound the buffer; oldest unmatched fragment falls out
            buf.pop(0)

    def _ingest(self, packet: bytes) -> bool:
        decoded = csrmesh.decode_packet(self._key, packet)
        if decoded is None:
            return False
        source = decoded["source"]
        if source == 0x8000:  # our own broadcast echoing back
            return True
        report = csrmesh.parse_report(decoded["payload"])
        if report:
            _LOGGER.debug("report from avid %s: %s", source, report)
            self.state.setdefault(source, {}).update(report)
            for cb in self._push_cbs:
                cb(dict(self.state))
        return True

    async def send(self, payload: bytes) -> None:
        """Encrypt and write one MCP payload to the mesh."""
        async with self._op_lock:
            await self._ensure_connected()
            # Capture the client: a disconnect between the two writes nulls
            # self._client, and we want a clean BleakError, not an AttributeError.
            client = self._client
            if client is None:
                raise UpdateFailed("Mesh gateway is not connected")
            packet = csrmesh.make_packet(self._key, csrmesh.random_seq(), payload)
            await client.write_gatt_char(CHAR_LOW, packet[:20], response=False)
            await client.write_gatt_char(CHAR_HIGH, packet[20:], response=False)

    async def poll(self) -> dict[int, dict]:
        """Broadcast READs; status arrives via notifications into self.state."""
        for noun in (
            csrmesh.NOUN_DIMMING,
            csrmesh.NOUN_COLOR,
            csrmesh.NOUN_TEMPERATURE,
        ):
            await self.send(csrmesh.read_payload(noun))
            await asyncio.sleep(_READ_SETTLE)
        return dict(self.state)

    async def disconnect(self) -> None:
        async with self._conn_lock:
            if self._client is not None:
                try:
                    await self._client.disconnect()
                finally:
                    self._client = None


class HaloCoordinator(DataUpdateCoordinator[dict[int, dict]]):
    """Polls the mesh and relays pushed state changes to entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, mesh: HaloMesh) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_INTERVAL_SECONDS),
        )
        self.mesh = mesh
        mesh.add_push_listener(self._on_push)

    async def _async_update_data(self) -> dict[int, dict]:
        try:
            return await self.mesh.poll()
        except UpdateFailed:
            raise
        except Exception as err:  # noqa: BLE001 - surface any BLE failure uniformly
            raise UpdateFailed(str(err)) from err

    @callback
    def _on_push(self, state: dict[int, dict]) -> None:
        self.async_set_updated_data(state)
