"""CSRmesh / Avi-on wire protocol: key derivation, packet crypto, MCP messages.

Pure protocol, no Home Assistant or BLE dependencies, so it is unit-testable on
its own. Uses `cryptography` (a Home Assistant core dependency) for AES-128-OFB;
hashlib/hmac are stdlib. Verified byte-identical to the `csrmesh` reference.

See docs/protocol.md for the derivation of every constant here.
"""
from __future__ import annotations

import hashlib
import hmac
import random
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

try:  # OFB moved to `decrepit` in cryptography 43; keep working on both.
    from cryptography.hazmat.decrepit.ciphers.modes import OFB
except ImportError:  # pragma: no cover
    from cryptography.hazmat.primitives.ciphers.modes import OFB

_KEY_SUFFIX = b"\x00\x4d\x43\x50"  # "\x00MCP"
_SOURCE = 0x8000  # fixed source for a controller "without a network id"
_OPCODE = 0x73  # MODEL_OPCODE ("set"/"get" model messages)

VERB_WRITE = 0
VERB_READ = 1
NOUN_DIMMING = 0x0A
NOUN_COLOR = 0x1D

# A device avid is >= this; anything below addresses a group (dest becomes 0 =
# broadcast, with the group id carried in the payload).
UNICAST_MIN = 32896


def generate_key(passphrase: str) -> bytes:
    """Derive the 128-bit network key from the location passphrase."""
    digest = bytearray(hashlib.sha256(passphrase.encode("ascii") + _KEY_SUFFIX).digest())
    digest.reverse()
    return bytes(digest[:16])


def _ofb(key: bytes, iv: bytes, data: bytes) -> bytes:
    # OFB is symmetric: same call encrypts and decrypts.
    op = Cipher(algorithms.AES(key), OFB(iv)).encryptor()
    return op.update(data) + op.finalize()


def random_seq() -> int:
    """24-bit sequence/nonce. Only needs to differ between packets."""
    return random.randint(1, 0xFFFFFF)


def make_packet(key: bytes, seq: int, data: bytes) -> bytes:
    """Encrypt+authenticate an MCP payload into an on-wire CSRmesh packet."""
    seq_b = seq.to_bytes(3, "little")
    iv = struct.pack("<3sxH10x", seq_b, _SOURCE)
    cipher = _ofb(key, iv, data)
    prehmac = struct.pack(f"<8x3sH{len(data)}s", seq_b, _SOURCE, cipher)
    mac = bytearray(hmac.new(key, prehmac, hashlib.sha256).digest())
    mac.reverse()
    return struct.pack(f"<3sH{len(data)}s8sc", seq_b, _SOURCE, cipher, bytes(mac[:8]), b"\xff")


def decode_packet(key: bytes, packet: bytes) -> dict | None:
    """Validate HMAC and decrypt a received packet.

    Returns {'source': avid, 'payload': mcp_bytes} or None if too short / bad MAC.
    The trailing byte of received packets is a TTL (not our 0xFF), so integrity is
    checked purely by the HMAC.
    """
    if len(packet) < 15:
        return None
    dlen = len(packet) - 14
    seq_b, source, cipher, mac_pkt, _ttl = struct.unpack(f"<3sH{dlen}s8sc", packet)
    prehmac = struct.pack(f"<8x3sH{dlen}s", seq_b, source, cipher)
    mac = bytearray(hmac.new(key, prehmac, hashlib.sha256).digest())
    mac.reverse()
    if bytes(mac[:8]) != mac_pkt:
        return None
    iv = struct.pack("<3sxH10x", seq_b, source)
    return {"source": source, "payload": _ofb(key, iv, cipher)}


def _mcp(target: int, verb: int, noun: int, value: list[int]) -> bytes:
    if target < UNICAST_MIN:
        dest, group = 0, target  # group / broadcast
    else:
        dest, group = target, 0  # unicast
    d = dest.to_bytes(2, "big")
    g = group.to_bytes(2, "big")
    return bytes([d[1], d[0], _OPCODE, verb, noun, g[0], g[1], 0, *value, 0, 0])


def set_brightness_payload(target: int, level: int) -> bytes:
    """level 0-255 (0 = off)."""
    return _mcp(target, VERB_WRITE, NOUN_DIMMING, [level & 0xFF, 0, 0])


def set_color_payload(target: int, kelvin: int) -> bytes:
    k = kelvin.to_bytes(2, "big")
    return _mcp(target, VERB_WRITE, NOUN_COLOR, [0x01, k[0], k[1]])


def read_payload(noun: int) -> bytes:
    """Broadcast READ of a noun; every light answers with a status packet."""
    return _mcp(0, VERB_READ, noun, [0, 0, 0])


def parse_report(payload: bytes) -> dict | None:
    """Decode a device's status payload -> {'brightness'} or {'color_temp'}."""
    if len(payload) < 6 or payload[2] != _OPCODE:
        return None
    noun = payload[4]
    value = payload[5:]
    if noun == NOUN_DIMMING and len(value) >= 2:
        return {"brightness": value[1]}
    if noun == NOUN_COLOR and len(value) >= 4:
        return {"color_temp": int.from_bytes(value[2:4], "big")}
    return None
