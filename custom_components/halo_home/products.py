"""What a given Avi-on fixture can actually do.

Capabilities are resolved from the best source available, in this order:

1. **The cloud's own `product.configurations`.** Every `abstract_devices` entry
   embeds a `product` object, and a `cct_range` configuration states the exact
   Kelvin range of that model. This is authoritative, it is per-product, and it
   means a fixture nobody has ever seen before is supported correctly with no
   code change. Prefer it always.
2. **The product table below**, keyed on `product_id`, for backups old enough to
   predate the `product` object, or accounts where it is absent.
3. **Whatever the mesh itself says.** For a product in neither of the above, the
   light platform watches what the fixture reports on the first poll: if it
   answers a COLOR read, it is tunable white. See `light.py`.

The platform is dimmable and tunable-white only. There is no RGB product and no
RGB opcode — `COLOR` (0x1D) carries a Kelvin value and nothing else. There is
also no on/off opcode: "off" is brightness 0 and "on" is brightness 255.

Product ids and their capability split are facts about Eaton/Avi-on hardware,
catalogued the hard way by the `oyvindkinsey/avionmesh` project and by the users
who reported their fixtures to it. Credit there.
"""
from __future__ import annotations

from typing import Any

# Mesh addressing split: a target at or above this is a device, below it a group.
# Kept in step with csrmesh.UNICAST_MIN by a test rather than imported, so this
# module stays free of the crypto dependency.
UNICAST_MIN = 32896

# Kelvin range assumed when a fixture is tunable but won't say over what range.
# Every indoor product measured so far is 2700-5000K; the outdoor floods are
# 3000-5000K, which `cct_range` reports correctly when present.
DEFAULT_MIN_KELVIN = 2700
DEFAULT_MAX_KELVIN = 5000

PRODUCT_NAMES: dict[int, str] = {
    0: "Group",
    82: "Bridge",
    90: "Lamp Dimmer",
    91: "Accessory Dimmer",
    93: "Recessed Downlight (RL)",
    94: "Light Adapter",
    97: "Smart Dimmer",
    127: "Scene Keypad (Hardwired)",
    134: "Smart Bulb (A19)",
    137: "Surface Downlight (BLD)",
    162: "MicroEdge (HLB)",
    167: "Smart Switch",
}

# Dimmable, and of those, which are also tunable white.
DIMMABLE: frozenset[int] = frozenset({0, 90, 93, 94, 97, 134, 137, 162})
TUNABLE_WHITE: frozenset[int] = frozenset({0, 93, 134, 137, 162})

# Products that switch a load but cannot dim it. On/off is still sent as
# brightness 255/0 — there is no separate power opcode on this platform.
ONOFF_ONLY: frozenset[int] = frozenset({167})


def _cct_range(product: dict[str, Any]) -> tuple[int, int] | None:
    """Pull the Kelvin range out of the cloud's product configurations, if stated."""
    for config in product.get("configurations") or ():
        if config.get("key") != "cct_range":
            continue
        for value in config.get("value") or ():
            low, high = value.get("min"), value.get("max")
            if isinstance(low, int) and isinstance(high, int) and 1000 <= low < high <= 10000:
                return low, high
    return None


def format_mac(raw: str) -> str:
    """Avi-on returns MACs unpunctuated; HA wants AA:BB:CC:DD:EE:FF."""
    raw = raw.replace(":", "").lower()
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2)).upper()


def is_light(raw: dict[str, Any]) -> bool:
    """True for fixtures we can command.

    The cloud reports three kinds of node. `device` is a load — a light. A
    `controller` (wall dimmer, scene keypad) is an *input*: it sends commands
    into the mesh and has no load of its own, so it must never become a light
    entity. `rab` is the Halo Access Bridge. Only loads belong here.
    """
    return raw.get("type") == "device" and bool(raw.get("friendly_mac_address"))


def dedupe_names(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Disambiguate duplicate device names.

    Avi-on names every fixture of a model identically ("MicroEdge (HLB)"), which
    would leave HA to fall back on `_2`, `_3` suffixes that say nothing about
    which can in the ceiling is which. Where a name repeats, append the last two
    MAC octets so the entity is at least identifiable. Users can rename freely.
    """
    counts: dict[str, int] = {}
    for dev in devices:
        counts[dev["name"]] = counts.get(dev["name"], 0) + 1
    for dev in devices:
        if counts[dev["name"]] > 1:
            tail = dev["mac"].replace(":", "")[-4:]
            dev["name"] = f"{dev['name']} {tail[:2]}:{tail[2:]}"
    return devices


def parse_device(raw: dict[str, Any]) -> dict[str, Any]:
    """One cloud device record -> the dict we persist in the config entry."""
    return {
        "avid": raw["avid"],
        "name": raw.get("name") or f"Light {raw['avid']}",
        "mac": format_mac(raw["friendly_mac_address"]),
        **resolve(raw),
    }


def resolve_group(members: list[dict[str, Any]]) -> dict[str, Any]:
    """Capabilities of a group: the *intersection* of what its members can do.

    A group is commanded with a single broadcast packet that every member obeys,
    so it can only offer what all of them support. Claiming color temperature on
    a group containing one dim-only fixture would silently do nothing to that
    fixture. The Kelvin range is likewise narrowed to the overlap.
    """
    if not members:
        return {"dimmable": False, "color_temp": False, "known": True, "model": "Group"}
    color_temp = all(m.get("color_temp", False) for m in members)
    lo = max(m.get("min_kelvin", DEFAULT_MIN_KELVIN) for m in members)
    hi = min(m.get("max_kelvin", DEFAULT_MAX_KELVIN) for m in members)
    if lo >= hi:
        # Members' tunable ranges don't overlap — there is no group-wide Kelvin
        # value that satisfies all of them, so don't offer color as a group.
        color_temp = False
        lo, hi = DEFAULT_MIN_KELVIN, DEFAULT_MAX_KELVIN
    return {
        "model": "Group",
        "known": True,
        "dimmable": all(m.get("dimmable", True) for m in members),
        "color_temp": color_temp,
        "min_kelvin": lo,
        "max_kelvin": hi,
    }


def parse_groups(
    raw_groups: list[dict[str, Any]], devices: list[dict[str, Any]], raw_devices: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Cloud group records -> group entries, with members resolved to avids.

    The cloud lists a group's members by device *pid*, not by avid, so this maps
    them back through the device records. Groups whose members we don't have (or
    that are empty) are dropped — an empty group entity would be a dead switch.
    """
    pid_to_avid = {
        str(d["pid"]): d["avid"] for d in raw_devices if d.get("pid") and d.get("avid")
    }
    by_avid = {d["avid"]: d for d in devices}

    groups: list[dict[str, Any]] = []
    for raw in raw_groups:
        avid = raw.get("avid")
        if not avid or avid >= UNICAST_MIN:
            continue  # a group must be addressable as a group
        members = [
            by_avid[pid_to_avid[str(pid)]]
            for pid in raw.get("devices") or ()
            if str(pid) in pid_to_avid and pid_to_avid[str(pid)] in by_avid
        ]
        if not members:
            continue
        groups.append(
            {
                "avid": avid,
                "name": raw.get("name") or f"Group {avid}",
                "members": [m["avid"] for m in members],
                **resolve_group(members),
            }
        )
    return groups


def resolve(raw: dict[str, Any]) -> dict[str, Any]:
    """Work out a fixture's capabilities from its cloud record.

    `raw` is one entry from `GET /locations/{pid}/abstract_devices`.
    """
    product = raw.get("product") or {}
    product_id = raw.get("product_id") or product.get("id")
    model = product.get("name") or PRODUCT_NAMES.get(product_id) or "Avi-on device"

    caps: dict[str, Any] = {
        "product_id": product_id,
        "model": model,
        "min_kelvin": DEFAULT_MIN_KELVIN,
        "max_kelvin": DEFAULT_MAX_KELVIN,
    }

    # 1. The cloud stated a Kelvin range: tunable white, and we know the bounds.
    if (cct := _cct_range(product)) is not None:
        caps.update(dimmable=True, color_temp=True, known=True)
        caps["min_kelvin"], caps["max_kelvin"] = cct
        return caps

    # 2. Fall back to what we know about this product id.
    if product_id in TUNABLE_WHITE:
        return {**caps, "dimmable": True, "color_temp": True, "known": True}
    if product_id in DIMMABLE:
        return {**caps, "dimmable": True, "color_temp": False, "known": True}
    if product_id in ONOFF_ONLY:
        return {**caps, "dimmable": False, "color_temp": False, "known": True}

    # 3. Never seen it. Assume it dims — every load on this platform does, and
    #    on/off works regardless since it is brightness 255/0. `known: False`
    #    lets the light platform upgrade this to tunable white if the fixture
    #    turns out to report a color temperature.
    return {**caps, "dimmable": True, "color_temp": False, "known": False}
