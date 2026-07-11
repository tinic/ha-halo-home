"""Capability resolution for the fixtures across the Avi-on platform.

The device records here follow the real shape of
`GET /locations/{pid}/abstract_devices`, including the nested `product` object.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "halo_home"))

import products  # noqa: E402


def device(product_id: int | None = None, product: dict | None = None, **extra):
    raw = {"avid": 32896, "name": "x", "friendly_mac_address": "aabbccddeeff", "type": "device"}
    if product_id is not None:
        raw["product_id"] = product_id
    if product is not None:
        raw["product"] = product
    return {**raw, **extra}


# --- Tier 1: the cloud states the Kelvin range ------------------------------


def test_cct_range_from_cloud_wins() -> None:
    """A product's own cct_range is authoritative — no table lookup needed."""
    caps = products.resolve(
        device(
            product_id=162,
            product={
                "id": 162,
                "name": "MicroEdge (HLB)",
                "configurations": [
                    {"id": 0, "key": "cct_range", "value": [{"min": 2700, "max": 5000}]}
                ],
            },
        )
    )
    assert caps["color_temp"] is True
    assert caps["dimmable"] is True
    assert caps["known"] is True
    assert (caps["min_kelvin"], caps["max_kelvin"]) == (2700, 5000)
    assert caps["model"] == "MicroEdge (HLB)"


def test_unknown_product_with_cct_range_is_supported_anyway() -> None:
    """The whole point: a fixture nobody has catalogued still works correctly.

    The outdoor floods are 3000-5000K, not 2700 — a hardcoded range would be
    wrong for them, and this is how we get it right without knowing the product.
    """
    caps = products.resolve(
        device(
            product_id=9999,
            product={
                "id": 9999,
                "name": "Some Outdoor Flood",
                "configurations": [
                    {"id": 0, "key": "cct_range", "value": [{"min": 3000, "max": 5000}]}
                ],
            },
        )
    )
    assert caps["color_temp"] is True
    assert caps["known"] is True
    assert (caps["min_kelvin"], caps["max_kelvin"]) == (3000, 5000)
    assert caps["model"] == "Some Outdoor Flood"


def test_nonsense_cct_range_is_ignored() -> None:
    caps = products.resolve(
        device(
            product_id=162,
            product={"configurations": [{"key": "cct_range", "value": [{"min": 0, "max": 0}]}]},
        )
    )
    # Falls through to the product table, which knows 162 is tunable white.
    assert caps["color_temp"] is True
    assert (caps["min_kelvin"], caps["max_kelvin"]) == (2700, 5000)


def test_non_cct_configuration_does_not_imply_color_temp() -> None:
    """A Scene Keypad has configurations, but not a cct_range one."""
    caps = products.resolve(
        device(
            product_id=127,
            product={
                "configurations": [
                    {"key": "controller_association_info", "value": [{"max_index": 4}]}
                ]
            },
        )
    )
    assert caps["color_temp"] is False


# --- Tier 2: the product table ---------------------------------------------


@pytest.mark.parametrize("product_id", sorted(products.TUNABLE_WHITE - {0}))
def test_tunable_white_products(product_id: int) -> None:
    caps = products.resolve(device(product_id=product_id))
    assert caps["dimmable"] is True
    assert caps["color_temp"] is True
    assert caps["known"] is True


@pytest.mark.parametrize("product_id", [90, 94, 97])
def test_dim_only_products_do_not_claim_color_temp(product_id: int) -> None:
    """Advertising COLOR_TEMP on a fixture that has none produces a broken entity."""
    caps = products.resolve(device(product_id=product_id))
    assert caps["dimmable"] is True
    assert caps["color_temp"] is False
    assert caps["known"] is True


def test_smart_switch_is_on_off_only() -> None:
    caps = products.resolve(device(product_id=167))
    assert caps["dimmable"] is False
    assert caps["color_temp"] is False
    assert caps["known"] is True
    assert caps["model"] == "Smart Switch"


# --- Tier 3: never seen it --------------------------------------------------


def test_unknown_product_assumes_dimmable_and_stays_unknown() -> None:
    """Unknown -> dimmable (every load on this platform dims), but flagged so the
    light platform can upgrade it to tunable white on mesh evidence."""
    caps = products.resolve(device(product_id=31337))
    assert caps["dimmable"] is True
    assert caps["color_temp"] is False
    assert caps["known"] is False
    assert caps["model"] == "Avi-on device"


def test_missing_product_id_entirely() -> None:
    caps = products.resolve(device())
    assert caps["known"] is False
    assert caps["dimmable"] is True


def test_product_id_can_come_from_the_nested_product_object() -> None:
    caps = products.resolve(device(product={"id": 162}))
    assert caps["product_id"] == 162
    assert caps["color_temp"] is True


# --- The controller/load split ---------------------------------------------


def test_only_loads_are_lights() -> None:
    """Wall dimmers and keypads send commands; they are not lights. The bridge
    is not a light either. Making any of them a light entity is a bug."""
    assert products.is_light(device(product_id=162)) is True
    assert products.is_light({**device(product_id=91), "type": "controller"}) is False
    assert products.is_light({**device(product_id=127), "type": "controller"}) is False
    assert products.is_light({**device(product_id=82), "type": "rab"}) is False
    assert products.is_light({"avid": 1, "type": "device"}) is False  # no MAC


def test_duplicate_names_get_a_mac_suffix() -> None:
    devices = products.dedupe_names(
        [
            {"name": "MicroEdge (HLB)", "mac": "1C:D6:BD:9E:59:8F"},
            {"name": "MicroEdge (HLB)", "mac": "1C:D6:BD:9E:59:BC"},
            {"name": "Kitchen", "mac": "1C:D6:BD:9E:58:3B"},
        ]
    )
    assert [d["name"] for d in devices] == [
        "MicroEdge (HLB) 59:8F",
        "MicroEdge (HLB) 59:BC",
        "Kitchen",  # unique already — left alone
    ]


# --- Groups -----------------------------------------------------------------


def _dev(avid, pid, **caps):
    base = {"avid": avid, "pid": pid, "name": f"d{avid}", "mac": "AA:BB:CC:DD:EE:FF"}
    return {**base, "dimmable": True, "color_temp": True, "min_kelvin": 2700,
            "max_kelvin": 5000, "known": True, **caps}


RAW_DEVICES = [
    {"avid": 40000, "pid": "aaa", "type": "device", "friendly_mac_address": "1", "product_id": 162},
    {"avid": 40001, "pid": "bbb", "type": "device", "friendly_mac_address": "2", "product_id": 162},
]


def test_group_members_resolve_from_pids_to_avids() -> None:
    """The cloud lists members by device pid; entities are addressed by avid."""
    devices = [_dev(40000, "aaa"), _dev(40001, "bbb")]
    groups = products.parse_groups(
        [{"avid": 256, "name": "Kitchen", "devices": ["aaa", "bbb"]}], devices, RAW_DEVICES
    )
    assert len(groups) == 1
    assert groups[0]["avid"] == 256
    assert groups[0]["name"] == "Kitchen"
    assert groups[0]["members"] == [40000, 40001]


def test_group_capabilities_are_the_intersection_of_members() -> None:
    """One dim-only member means the group cannot offer color temperature — a
    broadcast color command would silently do nothing to that fixture."""
    devices = [_dev(40000, "aaa"), _dev(40001, "bbb", color_temp=False)]
    group = products.parse_groups(
        [{"avid": 256, "name": "Mixed", "devices": ["aaa", "bbb"]}], devices, RAW_DEVICES
    )[0]
    assert group["dimmable"] is True
    assert group["color_temp"] is False


def test_group_kelvin_range_is_the_overlap() -> None:
    devices = [
        _dev(40000, "aaa", min_kelvin=2700, max_kelvin=5000),
        _dev(40001, "bbb", min_kelvin=3000, max_kelvin=4000),
    ]
    group = products.parse_groups(
        [{"avid": 256, "name": "Mixed", "devices": ["aaa", "bbb"]}], devices, RAW_DEVICES
    )[0]
    assert (group["min_kelvin"], group["max_kelvin"]) == (3000, 4000)


def test_empty_and_unknown_groups_are_dropped() -> None:
    """An empty group entity would be a switch that does nothing."""
    devices = [_dev(40000, "aaa")]
    groups = products.parse_groups(
        [
            {"avid": 256, "name": "Empty", "devices": []},
            {"avid": 257, "name": "Strangers", "devices": ["zzz"]},
            {"avid": 258, "name": "Real", "devices": ["aaa"]},
        ],
        devices,
        RAW_DEVICES,
    )
    assert [g["name"] for g in groups] == ["Real"]


def test_a_group_avid_must_be_below_the_unicast_threshold() -> None:
    """Above it, the packet would be sent unicast to one device instead of broadcast."""
    devices = [_dev(40000, "aaa")]
    assert products.parse_groups(
        [{"avid": 40000, "name": "Bogus", "devices": ["aaa"]}], devices, RAW_DEVICES
    ) == []
