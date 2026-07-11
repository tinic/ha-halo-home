#!/usr/bin/env python3
"""Ask the mesh what it knows: broadcast a READ for a noun and print the answers.

**Read-only.** This only ever sends `verb=READ`. It never writes to a fixture, so
it cannot change a setting, a level, or a configuration.

Why this exists: the Avi-on noun space has 36 entries, but only DIMMING (0x0A) and
COLOR (0x1D) have ever been implemented by anyone. The rest are names in an enum,
with no documented value encoding. A noun the firmware does not implement simply
does not answer a READ — so sweeping the space, and reading the ones that do
answer, is the only honest way to learn the wire format.

The immediate target is FADE_TIME (0x19). If it answers, its current value tells
us the width and units, and transitions become implementable. If nothing answers,
it isn't there, and that is worth knowing too.

    pip install bleak cryptography

    # Is FADE_TIME real, and what does it hold?
    python3 tools/probe_noun.py --backup avion_backup.json --noun 0x19

    # What does this fixture implement at all?
    python3 tools/probe_noun.py --backup avion_backup.json --sweep

Run it on a host with a Bluetooth adapter in range of a fixture. If Home Assistant
is running the integration on the same host, stop the integration first — the
component holds a connection, and two clients competing for one radio makes the
results meaningless.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from bleak import BleakClient, BleakScanner

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "custom_components" / "halo_home")
)
import csrmesh  # noqa: E402

CHAR_LOW = "c4edc000-9daf-11e3-8003-00025b000b00"
CHAR_HIGH = "c4edc000-9daf-11e3-8004-00025b000b00"

NOUN_NAMES = {
    0x03: "GROUPS", 0x06: "SUNRISE_SUNSET", 0x07: "SCHEDULE", 0x09: "COUNTDOWN",
    0x0A: "DIMMING", 0x11: "DIMMING_TABLE", 0x15: "DATE", 0x16: "TIME",
    0x19: "FADE_TIME", 0x1B: "ASSOCIATION", 0x1C: "WAKE_STATUS", 0x1D: "COLOR",
    0x1E: "CONFIG", 0x22: "SCENES", 0x27: "THERMOMETER", 0x28: "FIRMWARE_VERSION",
    0x29: "LUX_VALUE", 0x2D: "MOTION_SENSOR", 0x2E: "ALS_DIMMING", 0x5B: "AVION_SENSOR",
}


def load_key(path: str) -> tuple[bytes, list[str]]:
    data = json.loads(Path(path).read_text())
    location = data["locations"][0]
    key = csrmesh.generate_key(location["location"]["passphrase"])
    macs = [
        ":".join(
            d["friendly_mac_address"].replace(":", "").lower()[i : i + 2]
            for i in range(0, 12, 2)
        ).upper()
        for d in location["abstract_devices"]
        if d.get("type") == "device" and d.get("friendly_mac_address")
    ]
    return key, macs


async def probe(key: bytes, macs: list[str], nouns: list[int], settle: float) -> None:
    print("scanning for a fixture in range...")
    found = {d.address.upper(): d for d in await BleakScanner.discover(timeout=6.0)}
    target = next((found[m] for m in macs if m in found), None)
    if target is None:
        sys.exit(f"none of the {len(macs)} known fixtures are in range")
    print(f"connecting to {target.address}\n")

    lows: list[bytes] = []
    answers: dict[int, list[tuple[int, bytes]]] = {}
    current = {"noun": 0}

    def on_notify(_char, data: bytearray) -> None:
        frame = bytes(data)

        def ingest(packet: bytes) -> bool:
            decoded = csrmesh.decode_packet(key, packet)
            if decoded is None:
                return False
            if decoded["source"] != 0x8000:  # not our own READ echoing back
                answers.setdefault(current["noun"], []).append(
                    (decoded["source"], decoded["payload"])
                )
            return True

        if ingest(frame):
            return
        if len(frame) == 20:
            lows.append(frame)
            return
        for i, low in enumerate(lows):
            if ingest(low + frame):
                lows.pop(i)
                return

    async with BleakClient(target) as client:
        await client.start_notify(CHAR_LOW, on_notify)
        await client.start_notify(CHAR_HIGH, on_notify)

        for noun in nouns:
            current["noun"] = noun
            lows.clear()
            packet = csrmesh.make_packet(key, csrmesh.random_seq(), csrmesh.read_payload(noun))
            await client.write_gatt_char(CHAR_LOW, packet[:20], response=False)
            await client.write_gatt_char(CHAR_HIGH, packet[20:], response=False)
            await asyncio.sleep(settle)

            name = NOUN_NAMES.get(noun, "?")
            replies = answers.get(noun, [])
            if not replies:
                print(f"  0x{noun:02X} {name:<18} —")
                continue
            print(f"  0x{noun:02X} {name:<18} {len(replies)} reply(ies):")
            for source, payload in replies:
                print(f"      avid {source}: {payload.hex(' ')}")

    print("\nA noun with no reply is not implemented by this firmware.")
    print("For one that answers: bytes 0-1 dest, 2 opcode(0x73), 3 verb, 4 noun, 5+ value.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--backup", required=True, help="path to avion_backup.json")
    ap.add_argument("--noun", default="0x19", help="noun to read (default FADE_TIME)")
    ap.add_argument("--sweep", action="store_true", help="read every plausible noun")
    ap.add_argument("--settle", type=float, default=1.5, help="seconds to wait per read")
    args = ap.parse_args()

    key, macs = load_key(args.backup)
    nouns = sorted(NOUN_NAMES) if args.sweep else [int(args.noun, 0)]
    asyncio.run(probe(key, macs, nouns, args.settle))


if __name__ == "__main__":
    main()
