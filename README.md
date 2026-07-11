# Halo Home for Home Assistant

[![HACS: Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Validate](https://github.com/tinic/ha-halo-home/actions/workflows/validate.yml/badge.svg)](https://github.com/tinic/ha-halo-home/actions/workflows/validate.yml)

Home Assistant integration for **Eaton / Cooper Lighting "HALO Home"** and other
**Avi-on** Bluetooth mesh lights. On/off, brightness, and color temperature — over local
Bluetooth, with **no cloud, no bridge, and no MQTT**.

Home Assistant's built-in `avion` integration was **removed in HA 2026.7**. If you landed
here because your Halo lights stopped working after that upgrade: this replaces it.

---

## ⚠️ Read this first: back up your passphrase

Your entire mesh is controlled by one secret — a **static per-location passphrase**. It is
the only credential needed to command lights that are already installed, and **today the
only copy lives on Avi-on's servers.**

Cooper discontinued HALO Home in **November 2023** and committed to running the app and
cloud for about five years — so expect the servers to go dark around **November 2028.**

**When that happens, anyone who has not saved their passphrase permanently loses the ability
to set these lights up again.** The only recovery is a factory reset and re-provisioning of
every fixture — which means physically pulling every can out of the ceiling.

So, before anything else:

```bash
python3 tools/avion_backup.py     # stdlib only; prompts for your Avi-on login
```

This writes `avion_backup.json` (mode `0600`) containing your passphrase and device list.
**Copy it somewhere durable and offline.** This integration can be set up from that file
alone, with no internet and no Avi-on account — that is the path that still works in 2029.

It is worth doing this today even if you never install this integration.

> Treat the backup like a house key: whoever holds the passphrase can command every fixture
> on your mesh.

---

## What works

| | |
|---|---|
| On / off | ✅ |
| Brightness | ✅ |
| Color temperature (tunable white) | ✅ |
| State read-back from the fixtures | ✅ every light reports its real brightness + color |
| Changes made at the physical wall dimmer | ✅ pushed into HA as they happen |
| Your Avi-on **groups**, as entities | ✅ one broadcast packet — the whole room switches at once |
| Cloud needed at runtime | ❌ never — one connection to one fixture, locally |
| Extra Python packages to install | ❌ none |
| Transitions (`transition:` in a service call) | ❌ not supported — [see below](#why-there-are-no-transitions) |

The mesh is a **flood network**: Home Assistant holds a single Bluetooth connection to
whichever fixture is closest, and that fixture relays commands to all the others. You do not
need every light in radio range of your HA box — just one.

## Groups

Any group you made in the Halo Home app shows up as its own light entity, alongside the
individual fixtures.

This is worth more than a Home Assistant light group would be. A group is a **native mesh
address**: commanding it sends *one* broadcast packet that every member acts on
simultaneously. Iterating the fixtures instead means one packet each, and on a seven-can
kitchen you can watch them come up one by one. The group entity switches the room in a single
frame.

A group only offers what *all* its members support — a group containing one dim-only fixture
does not advertise color temperature, because a broadcast color command would silently skip
that fixture. Kelvin ranges are likewise narrowed to the overlap. Groups report no state of
their own, so a group's state is derived from its members.

## Why there are no transitions

Because the hardware won't do them. This was settled by measurement, not assumption.

The protocol has a `FADE_TIME` noun (`0x19`), which is the obvious way to implement
`transition:`. Tested against seven MicroEdge fixtures on firmware 1.1.13:

- **It reads.** All seven answer with a single byte — six hold `0xFF`, one holds `0x12`. The
  register is real.
- **It will not write.** Eleven candidate encodings (verbs `WRITE`/`UPDATE`/`INSERT`/`PUSH`,
  the value in each of the three slots, with and without the id byte) all read back unchanged,
  on two different fixtures.
- **And nothing fades regardless.** With `FADE_TIME` at `0xFF` — the largest value it holds, so
  the longest fade under any plausible unit — a 255 → 26 brightness step completed in under
  0.4 s with no intermediate levels, polled every 100 ms.

So `transition:` is unsupported rather than supported-but-broken. If you have Avi-on hardware
where a `FADE_TIME` write *does* stick, please open an issue — that would change the answer.

`tools/probe_noun.py` is how this was determined, and it is read-only (it only ever sends
`verb=READ`, so it cannot change a fixture):

```bash
python3 tools/probe_noun.py --backup avion_backup.json --sweep
```

It also turned up something nobody has exposed: **these fixtures report their own temperature**
(noun `0x27`, 26–36 °C across the test mesh), along with their firmware version and an
onboard real-time clock. See [docs/protocol.md](docs/protocol.md).

## Requirements

- Home Assistant **2024.12** or newer, with the **Bluetooth** integration set up.
- A Bluetooth adapter within range of at least one fixture. A built-in adapter or a USB
  dongle works. An [ESPHome Bluetooth Proxy](https://esphome.github.io/bluetooth-proxies/)
  *should* work — the integration uses Home Assistant's standard BLE connection path — but it
  has only been tested against a local USB adapter, so reports are welcome.
- An Avi-on / Halo Home account **for first-time setup only** (or a backup file, see above).

## Installation

**HACS** (recommended) → HACS → ⋮ → *Custom repositories* → add
`https://github.com/tinic/ha-halo-home` as an **Integration** → install → restart HA.

**Manual** → copy `custom_components/halo_home/` into your `config/custom_components/`
directory → restart HA.

## Setup

*Settings → Devices & Services → Add Integration → **Halo Home***. If a fixture is in range,
Home Assistant will likely discover it and prompt you on its own.

You will be offered two ways to get your mesh passphrase:

- **Sign in to Avi-on** — the easy path, while the servers are still up. Your password is
  used once and is not stored.
- **Restore from a backup file** — reads the `avion_backup.json` from
  `tools/avion_backup.py`. No internet, no account. **This one keeps working forever.**

Your lights appear as `light.*` entities. Avi-on tends to give every fixture of a model the
same name, so identically-named lights get their MAC suffix appended to tell them apart —
rename them to something useful in the HA UI.

## Supported hardware

Every load on the Avi-on platform speaks the same protocol, so all of it should work. What
differs is what each fixture *can* do, and the integration resolves that per device.

| `product_id` | Product | Capability |
|---:|---|---|
| 162 | MicroEdge (HLB) recessed downlight | dim + tunable white |
| 93 | Recessed Downlight (RL) | dim + tunable white |
| 137 | Surface Downlight (BLD) | dim + tunable white |
| 134 | Smart Bulb (A19) | dim + tunable white |
| 97 | Smart Dimmer (in-wall) | dim only |
| 90 | Lamp Dimmer (plug-in) | dim only |
| 94 | Light Adapter | dim only |
| 167 | Smart Switch (in-wall) | on/off only |
| *anything else* | — | **resolved automatically, see below** |

**A fixture that is not in that table still works.** The Avi-on cloud records each product's
own `cct_range`, so an uncatalogued model — the outdoor floods, for instance, which are
3000–5000 K rather than the usual 2700–5000 K — gets the *correct* Kelvin range with no code
change. Failing that, an unknown fixture is treated as dimmable, and upgraded to tunable
white if it turns out to report a color temperature when the mesh is polled.

Wall dimmers, scene keypads and the Access Bridge are **inputs**, not loads. They are
correctly excluded rather than turned into dead light entities.

There is no RGB here, and never was: the whole product line is dimmable and tunable-white
only. `COLOR` carries a Kelvin value and nothing else.

Only the MicroEdge (HLB) has been tested on real hardware — 7 of them, on a CSR8510 USB
adapter. Everything else is implemented from the protocol and the cloud's own product data.
**If you have other Halo or Avi-on hardware, please open an issue and say whether it worked**,
and paste the `product_id`. That is the single most useful contribution right now.

## How it works

HALO Home is a rebadge of the **Avi-on** platform, which runs **Qualcomm/CSR CSRmesh 1.x** —
*not* Bluetooth SIG Mesh. There is no pairing and no bonding: all security lives in the
payload, encrypted with a key derived from the location passphrase
(`sha256(passphrase + "\x00MCP")`, reversed, truncated to 128 bits). Commands are AES-OFB
encrypted and authenticated with a reversed, truncated HMAC-SHA256, then written to two GATT
characteristics.

The full wire protocol — crypto, packet framing, MCP opcodes, the addressing scheme, the
cloud API, and the state read-back path — is documented in **[docs/protocol.md](docs/protocol.md)**.
It is written so you could reimplement this from scratch without a Bluetooth sniffer.

## Related projects

Nothing else does *local, cloud-free, native HA* for these lights, which is why this exists.
But credit where it is due, and useful if this integration is not what you want:

- [`nayaverdier/halohome`](https://github.com/nayaverdier/halohome) — the clearest Python
  reference for the cloud API and the send path.
- [`nkaminski/csrmesh`](https://github.com/nkaminski/csrmesh) — the original CSRmesh crypto
  work that everything here descends from.
- [`oyvindkinsey/esphome-avionmesh`](https://github.com/oyvindkinsey/esphome-avionmesh) — an
  ESPHome component. The most actively maintained Avi-on code anywhere; a good choice if you
  would rather put the mesh behind an ESP32.
- [`oyvindkinsey/recsrmesh`](https://github.com/oyvindkinsey/recsrmesh) — modern async CSRmesh
  library, and the only implementation of **MASP**, which can re-provision a factory-reset
  fixture with a key you choose. That is the escape hatch if you lose your passphrase after
  the cloud is gone.
- [`jhanssen/halo-mqtt`](https://github.com/jhanssen/halo-mqtt) — bridges Halo to HA over
  MQTT. Unmaintained since 2023.
- [`mjg59/python-avion`](https://github.com/mjg59/python-avion) — the original, and what HA
  core's now-removed `avion` integration was built on.

## Contributing

Issues and PRs welcome — especially reports from **other Avi-on hardware**, and from anyone
running this through a **Bluetooth proxy**.

`custom_components/halo_home/csrmesh.py` has no Home Assistant or Bluetooth imports, so the
protocol core can be tested with no hardware and no HA:

```bash
pip install pytest cryptography
pytest
```

## Disclaimer

Not affiliated with, endorsed by, or connected to Eaton, Cooper Lighting, or Avi-on. "HALO"
and "HALO Home" are trademarks of their respective owners. This integration was developed
independently through protocol analysis of a product line its manufacturer has discontinued,
so that the hardware people already paid for keeps working after the servers are switched
off.

## License

MIT — see [LICENSE](LICENSE).
