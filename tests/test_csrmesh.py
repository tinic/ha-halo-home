"""Unit tests for the CSRmesh protocol core.

`csrmesh.py` deliberately has no Home Assistant or BLE imports, so it can be
tested standalone — no hass fixtures, no BLE mocks, no hardware.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "halo_home"))

import csrmesh  # noqa: E402

PASSPHRASE = "correct horse battery staple"
KEY = csrmesh.generate_key(PASSPHRASE)


def test_key_is_128_bit_and_stable() -> None:
    assert len(KEY) == 16
    assert KEY == csrmesh.generate_key(PASSPHRASE)


def test_key_is_reversed_truncated_sha256_of_passphrase_plus_mcp() -> None:
    """Pin the derivation: sha256(passphrase + b"\\x00MCP"), reversed, first 16 bytes."""
    import hashlib

    expected = bytes(reversed(hashlib.sha256(PASSPHRASE.encode() + b"\x00MCP").digest()))[:16]
    assert KEY == expected


def test_packet_round_trip() -> None:
    payload = csrmesh.set_brightness_payload(32896, 200)
    packet = csrmesh.make_packet(KEY, csrmesh.random_seq(), payload)
    decoded = csrmesh.decode_packet(KEY, packet)
    assert decoded is not None
    assert decoded["payload"] == payload
    assert decoded["source"] == 0x8000


def test_packet_layout() -> None:
    """seq(3) + source(2) + ciphertext + mac(8) + 0xFF."""
    payload = csrmesh.set_brightness_payload(32896, 1)
    packet = csrmesh.make_packet(KEY, 0x123456, payload)
    assert packet[:3] == (0x123456).to_bytes(3, "little")
    assert packet[3:5] == (0x8000).to_bytes(2, "little")
    assert len(packet) == 3 + 2 + len(payload) + 8 + 1
    assert packet[-1] == 0xFF


def test_wrong_key_is_rejected() -> None:
    packet = csrmesh.make_packet(KEY, csrmesh.random_seq(), csrmesh.read_payload(csrmesh.NOUN_DIMMING))
    assert csrmesh.decode_packet(csrmesh.generate_key("some other mesh"), packet) is None


def test_tampered_packet_is_rejected() -> None:
    packet = bytearray(csrmesh.make_packet(KEY, csrmesh.random_seq(), csrmesh.read_payload(0x0A)))
    packet[6] ^= 0xFF  # flip a ciphertext bit
    assert csrmesh.decode_packet(KEY, bytes(packet)) is None


def test_short_packet_is_rejected() -> None:
    assert csrmesh.decode_packet(KEY, b"\x00" * 14) is None


def test_received_packets_carry_a_ttl_not_our_0xff() -> None:
    """Real devices answer with a TTL tail byte; integrity must come from the HMAC alone."""
    payload = csrmesh.set_brightness_payload(32896, 100)
    packet = bytearray(csrmesh.make_packet(KEY, csrmesh.random_seq(), payload))
    packet[-1] = 0x12  # a plausible TTL, as seen on the wire
    decoded = csrmesh.decode_packet(KEY, bytes(packet))
    assert decoded is not None
    assert decoded["payload"] == payload


def test_device_address_is_unicast() -> None:
    payload = csrmesh.set_brightness_payload(32896, 128)
    assert payload[0:2] == b"\x80\x80"  # dest = avid, little-endian in the payload
    assert payload[5:7] == b"\x00\x00"  # group = 0


def test_group_address_broadcasts_with_group_in_payload() -> None:
    """A target below 32896 is a group: dest becomes 0, group id rides in the payload."""
    payload = csrmesh.set_brightness_payload(256, 128)
    assert payload[0:2] == b"\x00\x00"  # dest = 0 -> reaches every node
    assert payload[5:7] == (256).to_bytes(2, "big")


def test_brightness_payload() -> None:
    payload = csrmesh.set_brightness_payload(32896, 200)
    assert payload[2] == 0x73
    assert payload[3] == csrmesh.VERB_WRITE
    assert payload[4] == csrmesh.NOUN_DIMMING
    assert payload[8] == 200


def test_color_payload_is_big_endian_kelvin() -> None:
    payload = csrmesh.set_color_payload(32896, 4000)
    assert payload[4] == csrmesh.NOUN_COLOR
    assert payload[8] == 0x01
    assert payload[9:11] == (4000).to_bytes(2, "big")


def test_read_payload_is_a_broadcast() -> None:
    payload = csrmesh.read_payload(csrmesh.NOUN_DIMMING)
    assert payload[0:2] == b"\x00\x00"
    assert payload[3] == csrmesh.VERB_READ


@pytest.mark.parametrize("level", [0, 1, 100, 255])
def test_parse_brightness_report(level: int) -> None:
    """A device's DIMMING report puts the level in the second value byte."""
    report = bytes([0x80, 0x80, 0x73, csrmesh.VERB_WRITE, csrmesh.NOUN_DIMMING, 0x00, level])
    assert csrmesh.parse_report(report) == {"brightness": level}


def test_parse_color_report() -> None:
    report = bytes(
        [0x80, 0x80, 0x73, csrmesh.VERB_WRITE, csrmesh.NOUN_COLOR, 0x00, 0x01, *(2700).to_bytes(2, "big")]
    )
    assert csrmesh.parse_report(report) == {"color_temp": 2700}


def test_parse_report_ignores_foreign_payloads() -> None:
    assert csrmesh.parse_report(b"\x00\x00\x99\x00\x0a\x00\x64") is None  # wrong opcode
    assert csrmesh.parse_report(b"\x00\x00") is None  # too short


def test_products_threshold_matches_the_protocol() -> None:
    """products.py duplicates this constant to stay dependency-free; keep them in step."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "halo_home"))
    import products  # noqa: PLC0415

    assert products.UNICAST_MIN == csrmesh.UNICAST_MIN


def test_group_command_is_one_broadcast_packet() -> None:
    """The whole point of groups: dest=0 reaches every node, and the group id in
    the payload selects which of them act. One packet, not N."""
    payload = csrmesh.set_brightness_payload(256, 128)
    assert payload[0:2] == b"\x00\x00"            # dest 0 = broadcast
    assert payload[5:7] == (256).to_bytes(2, "big")  # group id
    assert payload[8] == 128


def test_parse_temperature_report() -> None:
    """THERMOMETER (0x27) value is [0x00, degC, degC]; value[1] is the temperature."""
    report = bytes([0x80, 0x80, 0x73, csrmesh.VERB_WRITE, csrmesh.NOUN_TEMPERATURE, 0x00, 34, 34])
    assert csrmesh.parse_report(report) == {"temperature": 34}


def test_temperature_read_is_a_broadcast() -> None:
    payload = csrmesh.read_payload(csrmesh.NOUN_TEMPERATURE)
    assert payload[0:2] == b"\x00\x00"
    assert payload[3] == csrmesh.VERB_READ
    assert payload[4] == csrmesh.NOUN_TEMPERATURE


def test_parse_report_distinguishes_the_three_nouns() -> None:
    mk = lambda noun, *vals: bytes([0x80, 0x80, 0x73, 0x00, noun, *vals])
    assert csrmesh.parse_report(mk(csrmesh.NOUN_DIMMING, 0x00, 200)) == {"brightness": 200}
    assert "color_temp" in csrmesh.parse_report(mk(csrmesh.NOUN_COLOR, 0x00, 0x01, 0x0A, 0x8C))
    assert csrmesh.parse_report(mk(csrmesh.NOUN_TEMPERATURE, 0x00, 30, 31)) == {"temperature": 30}
