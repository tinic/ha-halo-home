# CSRmesh / Avi-on protocol

Everything here is verified against actual source (`nkaminski/csrmesh`, `nayaverdier/halohome`)
on 2026-07-10, not forum hearsay. Line references are to those repos.

## What this is

HALO Home is a rebadge of the **Avi-on** platform, which runs **Qualcomm/CSR CSRmesh 1.x**
on CSR101x silicon. The Avi-on API confirms it directly: a location returns
`mesh_type: "csr"`.

**CSRmesh is not Bluetooth SIG Mesh.** Different crypto (SHA256-"MCP" key derivation,
AES-OFB, reversed-truncated HMAC — none of which is SIG Mesh's AES-CCM scheme). Do not
reach for HA's (nonexistent) SIG-mesh support or BlueZ `bluetooth-meshd`.

It is a **flood mesh**: connect GATT to *any one* reachable node, write an encrypted packet,
and that node rebroadcasts it to the whole mesh. You never connect to each bulb. One
connection slot serves the entire house.

## No pairing, no bonding

Control is a plain LE connect + `write_gatt_char` of an *encrypted* payload. There is no
bond. All security is in the payload — whoever holds the network key can command the mesh.
A neighbor's Halo mesh is inert to us and ours to them purely because the passphrase differs.

## GATT characteristics

A CSRmesh packet is written as **two** GATT writes, split at 20 bytes:

| | UUID |
|---|---|
| low (bytes 0–19) | `c4edc000-9daf-11e3-8003-00025b000b00` |
| high (bytes 20+) | `c4edc000-9daf-11e3-8004-00025b000b00` |

## Key derivation

`csrmesh.crypto.generate_key`, called by halohome with the location passphrase:

```python
key = sha256(passphrase_ascii + b"\x00MCP").digest()   # \x00\x4d\x43\x50
key = bytes(reversed(key))[:16]                         # 128-bit, byte-reversed, truncated
```

The passphrase is a long random string returned by the cloud at
`locations[].location.passphrase` (88 characters on the mesh this was developed against).
`device_key` is null on every device — the location passphrase is the *only* secret, and it
is all anyone needs to command the whole mesh. Treat a backup of it exactly as you would a
house key.

## Packet framing — `csrmesh.crypto.make_packet`

- `seq` = random 24-bit (`random.randint(1, 16777215)`); just needs to differ, not increment.
- `source` = **32768 (0x8000)** — the fixed "device without a network ID" source.
- **IV (16 B)** = `seq(3, LE) ∥ 0x00 ∥ source(2, LE) ∥ 10×0x00`
- **Encrypt**: `AES.new(key, MODE_OFB, iv)` over the plaintext MCP payload.
- **Auth**: `HMAC_SHA256(key, 8×0x00 ∥ seq(3) ∥ source(2) ∥ ciphertext)`, then **reverse the
  digest and truncate to 8 bytes**.
- **On wire**: `seq(3) ∥ source(2, LE) ∥ ciphertext ∥ mac(8) ∥ 0xFF`

`decrypt_packet` inverts this and validates the HMAC — this is also the receive path for
state reports and wall-dimmer notifications.

## MCP command payload (the plaintext) — `halohome._create_packet`

```
byte 0..1  dest address, little-endian (dest[low], dest[high])
byte 2     0x73  ("s" = set verb)
byte 3     0x00
byte 4     noun
byte 5..6  group address, big-endian
byte 7     0x00  (id)
byte 8..   value bytes
...        0x00, 0x00  (padding)
```

**Addressing** (`if target_id < 32896: group_id = target_id; target_id = 0`):
- device avid ≥ 32896 → unicast: `dest = avid`, `group = 0`
- group avid < 32896 → `dest = 0` (broadcast) with the group in the group field
- `dest = 0` reaches **every** node.

### What 0x73 actually is

`0x73` is **not** an Avi-on opcode — it is CSRmesh's `DATA_BLOCK_SEND` (the Data model, id 8):
a generic datagram tunnel carrying **10 opaque bytes**. Avi-on ignores CSRmesh's standard
Light and Power models entirely and tunnels its own verb/noun protocol through the Data model.
That 10-byte cap is why color temperature has to fit in three value bytes.

Consequence: the standard CSRmesh light opcodes (`LIGHT_SET_LEVEL` `0x8A01`,
`LIGHT_SET_RGB` `0x8A03`, `POWER_SET_STATE` `0x8901`) almost certainly do nothing on Halo
hardware. Do not reach for them.

### Nouns

The two that matter:

| action | noun | value bytes |
|---|---|---|
| brightness | `0x0A` DIMMING | `[level, 0, 0]` — on = 255, off = 0 |
| color temp | `0x1D` COLOR | `[0x01, kelvin_hi, kelvin_lo]` (Kelvin as big-endian u16) |

**There is no on/off noun** — "off" is brightness 0 and "on" is brightness 255, including for
the Smart Switch, which cannot dim. **There is no RGB noun**; `COLOR` carries Kelvin only.

The full noun space (from the decompiled Avi-on Android app, via `oyvindkinsey/avionmesh`) is
36 values, most unimplemented by every known client. The ones worth knowing about:

| noun | | noun | |
|---|---|---|---|
| `0x03` | GROUPS | `0x28` | FIRMWARE_VERSION |
| `0x07` | SCHEDULE | `0x29` | LUX_VALUE |
| `0x0A` | **DIMMING** | `0x2D` | MOTION_SENSOR |
| `0x11` | DIMMING_TABLE | `0x2E` | ALS_DIMMING |
| `0x19` | FADE_TIME — *see below* | `0x5B` | AVION_SENSOR |
| `0x1D` | **COLOR** | `0xFF` | NONE |

Verbs: `WRITE=0`, `READ=1`, `INSERT=2`, `DELETE=5`, `PING=6`, plus ~16 more.

An unimplemented noun simply does not answer a `READ`, so the noun space can be swept safely
to discover what a given fixture supports. `tools/probe_noun.py` does exactly that.

## What the hardware actually answers

Measured **2026-07-11** against seven MicroEdge (HLB) fixtures, firmware 1.1.13, by sweeping
the noun space with `READ` (`tools/probe_noun.py`). A response layout is *not* the same as a
request layout — a reply is `dest(2) ∥ 0x73 ∥ verb ∥ noun ∥ value…`, with no group or id
bytes, so the value sits at `payload[5:]`.

| noun | answers? | value | meaning |
|---|---|---|---|
| `0x09` COUNTDOWN | ✅ | `00 00 00` | idle |
| `0x0A` DIMMING | ✅ | `00 <level>` | brightness, 0–255 |
| `0x15` DATE | ✅ | `1a 07 0b` | `[year-2000, month, day]` — decoded as 2026-07-11, the day of the test |
| `0x16` TIME | ✅ | `01 34 18 01 00` | seconds since midnight, 3-byte **big-endian** (`0x013418` = 78 872 s = 21:54:32), then 2 unknown bytes. Each fixture keeps its own clock and they drift seconds apart. |
| `0x19` FADE_TIME | ✅ | `00 ff` | one byte — **read-only in practice, see below** |
| `0x1D` COLOR | ✅ | `00 01 <k_hi> <k_lo>` | Kelvin, big-endian |
| `0x27` THERMOMETER | ✅ | `22 22` | **the fixture's own temperature in °C** (0x22 = 34 °C). Two bytes; across the mesh they were 26–36 °C. |
| `0x28` FIRMWARE_VERSION | ✅ | `01 01 0d` | 1.1.13 |
| `0x03` GROUPS | ❌ silent | | needs COUNT/slot-index, not a plain READ |
| `0x06` SUNRISE_SUNSET | ❌ silent | | |
| `0x07` SCHEDULE | ❌ silent | | |
| `0x11` DIMMING_TABLE | ❌ silent | | |
| `0x1B` ASSOCIATION | ❌ silent | | |
| `0x1C` WAKE_STATUS | ❌ silent | | |
| `0x1E` CONFIG | ❌ silent | | |
| `0x22` SCENES | ❌ silent | | |

A noun the firmware does not implement simply does not answer. THERMOMETER is the notable
find: these fixtures will tell you how hot they are, which is a sensor nobody has exposed.

### FADE_TIME (0x19) — readable, not writable. No transitions.

The obvious way to implement Home Assistant's `transition:` — and it does not work.

**It reads.** All seven fixtures answer, with a single byte: six hold `0xFF`, one holds `0x12`
(18). So the register exists, and it demonstrably can hold a value other than `0xFF`.

**It does not write.** Eleven candidate encodings were tried against two fixtures — including
the one already holding `0x12`, proving the register is not simply pinned:

- verb `WRITE` with the value at `value[0]`, `value[1]`, `value[2]`, all three slots at once,
  and with the id byte set to 1
- verbs `UPDATE` (0x10), `INSERT` (0x02) and `PUSH` (0x0B)

Every one read back unchanged. Both fixtures ended on their original values.

**And no fade is observable anyway.** With `FADE_TIME` = `0xFF` — which, if it were a duration
in any plausible unit, would be the *longest* one — a 255 → 26 step was complete within 0.4 s
and reported no intermediate levels, polling every 100 ms.

Conclusion: on this firmware `0xFF` reads as "no fade", the register is not writable over the
mesh with any encoding anyone has published or that is reasonable to guess, and mesh-commanded
brightness changes are effectively instant. **Transitions are therefore not implementable**,
and this integration does not pretend otherwise. If someone finds hardware where a write does
stick, that would change the answer — please open an issue.

## Groups

Two separate things share the name, and conflating them wastes a day.

**Commanding a group** does not use the GROUPS noun at all. It is pure addressing: a target
below `32896` is sent with `dest = 0` (broadcast) and the group id in the payload's group
field. Every node hears it; the ones in that group act. One packet switches a whole room —
which is the entire reason to expose groups as entities rather than iterating members.

**Group membership** is what noun `0x03` manages, addressed *to a device* (so the payload's
group field is zero) with INSERT/DELETE:

| op | dest | payload |
|---|---|---|
| INSERT membership | `device avid` | `02 03 00 00 00 gid_hi gid_lo 00 00 00` |
| DELETE membership | `device avid` | `05 03 00 00 00 gid_hi gid_lo 00 00 00` |
| COUNT (slot capacity) | `device avid` | `04 03 00 00 00` |
| READ slot *n* | `device avid` | `01 03 00 00 00 n` |

COUNT returns the table capacity in the last payload byte; READ of a slot returns the group id
in the last two bytes, big-endian, with `0` meaning empty. This integration reads group
membership from the cloud instead, so it does not currently need any of this — it is
documented here because it is the escape hatch for building groups with no cloud at all.

Group ids run 256–24575; device avids are ≥ 32896.

## Products and capabilities

The cloud assigns each model a numeric `product_id`, and — importantly — embeds a nested
`product` object in every `abstract_devices` entry:

```json
"product": {"id": 162, "name": "MicroEdge (HLB)", "category": "LIGHT",
            "configurations": [{"key": "cct_range", "value": [{"min": 2700, "max": 5000}]}]}
```

**`cct_range` is the capability signal.** Its presence means the model is tunable white, and
its value gives the exact Kelvin bounds — which differ across the line (indoor fixtures are
2700–5000 K; the outdoor floods are 3000–5000 K). Reading it beats any hardcoded table, and
supports models nobody has catalogued. No other client does this.

Known product ids, for when `product` is absent:

| id | product | dim | CCT | `type` |
|---:|---|:-:|:-:|---|
| 90 | Lamp Dimmer | ✅ | — | device |
| 93 | Recessed Downlight (RL) | ✅ | ✅ | device |
| 94 | Light Adapter | ✅ | — | device |
| 97 | Smart Dimmer | ✅ | — | device |
| 134 | Smart Bulb (A19) | ✅ | ✅ | device |
| 137 | Surface Downlight (BLD) | ✅ | ✅ | device |
| 162 | MicroEdge (HLB) | ✅ | ✅ | device |
| 167 | Smart Switch | — | — | device |
| 82 | Bridge (RAB) | | | `rab` |
| 91 | Accessory Dimmer | | | `controller` |
| 127 | Scene Keypad | | | `controller` |
| 0 | *(synthetic: group)* | ✅ | ✅ | — |

The `type` field is the load/input split: only `device` is a controllable light. A
`controller` is a wall dimmer or keypad that *emits* commands into the mesh and has no load of
its own; `rab` is the Access Bridge. Both must be excluded from the light platform.

Catalogue credit: `oyvindkinsey/avionmesh` and the users who reported their fixtures to it.

## Sending — `halohome._send_packet`

```python
csrpacket = make_packet(key, random_seq(), payload)
await client.write_gatt_char(CHARACTERISTIC_LOW,  csrpacket[:20])
await client.write_gatt_char(CHARACTERISTIC_HIGH, csrpacket[20:])
```

Retried up to 3×; on any exception the connection is dropped and re-established. halohome
picks the connect target by **strongest RSSI** among known devices, which is the right
instinct — connect to the closest fixture and let the mesh relay.

## Receiving — state polling (verified live 2026-07-10)

Every light reports its state on request. This is how HA tracks brightness/color,
including changes made at the physical dimmer.

1. Subscribe to notifications on **both** write characteristics. Reassembled inbound
   packets arrive as a 20-byte fragment on `…8003` plus a short overflow on `…8004`;
   concatenate in arrival order. (bluetoothctl mangles this — use a real GATT client
   / `bleak start_notify`.)
2. Broadcast a **READ**: `dest=0`, opcode `0x73`, `verb=READ=1`, `noun=DIMMING=10`
   (or `COLOR=29`), value bytes zero. One READ makes **all** lights answer.
3. Decrypt each response with the network key and verify the HMAC.

Response wire packet: `seq(3) ∥ source(2,LE) ∥ ciphertext ∥ mac(8) ∥ ttl(1)`.
- **`source` is the reporting device's avid** (e.g. `0x8080`=32896), not `0x8000`.
- The tail byte is a **TTL** (`0x11`–`0x14`, decremented per relay hop), *not* the
  `0xFF` we send. Verify integrity by HMAC, never by the tail.
- Our own broadcast READ echoes back with `source=0x8000` — ignore those.

Decrypted MCP report: `[dest_lo, dest_hi, 0x73, verb, noun, val…]`
- DIMMING (`0x0A`): `val[1]` = brightness 0–255. Round-trip confirmed: SET 100 → every
  fixture reports 100; a change made at the physical wall dimmer reads back correctly, which
  is what keeps HA's state from going stale when someone uses the wall control.
- COLOR (`0x1D`): `val[2:4]` = Kelvin, big-endian.

A single broadcast READ makes every fixture on the mesh answer at once with
`{brightness, color_temp}`, each HMAC valid. Responses from different fixtures interleave on
the two characteristics, so the coordinator reassembles each low/overflow pair by testing the
HMAC rather than assuming arrival order.

## Cloud API (only needed once, to get the passphrase)

`https://api.avi-on.com`, still live 2026-07-10.

- `POST /sessions` `{email, password}` → `credentials.auth_token`
- header `Authorization: Token <t>` (halohome also sends `Accept: application/api.avi-on.v3`;
  plain `application/json` also works, and is what `tools/avion_backup.py` sends)
- `GET /user/locations` → list of `{pid}`
- `GET /locations/{pid}` → `location.passphrase` ← **the asset**
- `GET /locations/{pid}/abstract_devices` → `avid`, `friendly_mac_address`, `product_id`, `type`
- `GET /locations/{pid}/groups`

The passphrase is static. Fetch once, cache forever, never touch the cloud at runtime.

## MASP — offline re-provisioning (the escape hatch)

If the passphrase is ever lost and the cloud is dead, `oyvindkinsey/recsrmesh` implements
**MASP association** (ECDH key exchange + network-key distribution): factory-reset a fixture
and associate it with a key *you* choose, no cloud involved. This means physically resetting
every fixture, so it is strictly a last resort — which is exactly why the current passphrase
backup matters.

## Prior art

Surveyed 2026-07-11 via GitHub code search on the MTL characteristic UUIDs
(`c4edc000-9daf-11e3-800{3,4}-00025b000b00`), which fingerprint any CSRmesh implementation.

### Libraries / protocol

| repo | what it is | last push |
|---|---|---|
| `nayaverdier/halohome` | cleanest cloud-API + send reference (MIT). The code quoted above. | 2024-11 |
| `nkaminski/csrmesh` | the crypto core (LGPL). 71★. | — |
| `oyvindkinsey/recsrmesh` | modern async CSRmesh + MASP + 21 KB spec + replay tests (LGPL) | 2026-02 |
| `oyvindkinsey/avionmesh` | Avi-on device layer on recsrmesh (GPL) | 2026-02 |
| `mjg59/python-avion` | original, `bluepy`-based, dead. What HA core's removed `avion` used. | 2023-07 |

### Home Assistant integrations — none of them are a local-only HALO component

| repo | what it is | last push |
|---|---|---|
| `oyvindkinsey/avionmesh_homeassistant` | a HACS component — but cloud-tethered at runtime, uses the legacy `async_get_scanner` shim, depends on `aiorun`. Reference, not a base. | 2025-11 |
| `futbolpal/home-assistant-halo` | HACS component for *these exact HLB fixtures*, but pure cloud API and **requires the Halo Access Bridge**. Dies with the cloud. Dead. | 2023-07 |
| `jhanssen/halo-mqtt` | local BLE → MQTT discovery → HA. Same goal, bridge shape. Node/TS, no license, dead. | 2023-02 |
| `jhanssen/halo-mqtt-qt` | the same author's C++/Qt rewrite, "so I can run this on a reliable Bluetooth stack". Also dead. | 2023-11 |
| `fusioncha0s/Avi-On-Cooper-HaloHome-Setup-with-MQTT` | prose docs for wiring the above to an MQTT broker. | 2025-04 |

### Same protocol, different brand

| repo | what it is | last push |
|---|---|---|
| `bbruenings/ha-inlite` | **the closest sibling to this component.** Native HA BLE integration for *in-lite* garden lighting over CSRmesh — same GATT chars, same crypto, and independently converged on the same architecture (coordinator + vendored protocol lib + one-time cloud fetch of the passphrase, local thereafter, bluetooth-proxy support, HACS). Its *device* layer is unrelated to ours: hub/transformer/outlet addressing, a block-streaming framing layer (`0x70`–`0x74`), `SET_OUTLET_MODE`, and `ColorMode.ONOFF` only — no brightness, no color temp. Worth reading for its unit tests and idle-disconnect policy. | 2026-06 |
| `derwasp/esphome-in-lite` | ESPHome equivalent of the above. | 2026-04 |
| `oyvindkinsey/esphome-avionmesh` | ESPHome component, fully local. The most actively maintained Avi-on code anywhere. | 2026-04 |
| `fsaris/home-assistant-awox`, `monty68/uniled` | AwoX / BanlanX HA components carrying the same CSRmesh `packetutils` lineage. | — |
| `danisla/iot-homebrite-led-bulb` | Feit/HomeBrite, same chars. | — |

**Conclusion.** Everything HALO-branded *and* HA-native is either dead or cloud-tethered, and the
one person still actively working on Avi-on mesh has moved to ESPHome rather than a HA
integration. A local-only, cloud-free HA component with brightness + color temp + state
read-back does not otherwise exist.
