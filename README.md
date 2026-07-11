# Halo Home for Home Assistant

[![HACS: Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Validate](https://github.com/tinic/ha-halo-home/actions/workflows/validate.yml/badge.svg)](https://github.com/tinic/ha-halo-home/actions/workflows/validate.yml)

Local Home Assistant integration for **Eaton / Cooper Lighting "HALO Home"** and other
**Avi-on** Bluetooth mesh lights. On/off, brightness, and color temperature over Bluetooth —
**no cloud, no bridge, no MQTT**.

Home Assistant's built-in `avion` integration was **removed in HA 2026.7**. If your Halo
lights stopped working after that upgrade, this replaces it.

- 💡 On/off, brightness, tunable-white color temperature
- 🔄 Live state, including changes made at the physical wall dimmer
- 👥 Your Halo Home groups appear as entities — switch a whole room at once
- 📡 Fully local — one Bluetooth connection reaches your whole mesh
- 🧩 No extra Python packages; uses only what Home Assistant already ships

## ⚠️ Back up your passphrase first

Your lights are controlled by a single secret — a per-location passphrase — and **the only
copy lives on Avi-on's servers.** Cooper discontinued HALO Home in 2023 and is expected to
shut the servers down **around November 2028**. After that, anyone without a saved passphrase
can no longer set these lights up at all, short of factory-resetting every fixture.

Save it now, even if you set up the integration later:

```bash
python3 tools/avion_backup.py     # prompts for your Avi-on login; needs only Python
```

This writes `avion_backup.json` — your passphrase and device list. **Keep a copy somewhere
safe and offline.** The integration can be set up from this file with no account and no
internet, so it keeps working after the servers are gone. Treat the file like a house key.

## Requirements

- Home Assistant **2024.12** or newer, with the **Bluetooth** integration enabled.
- A Bluetooth adapter (built-in, USB, or an
  [ESPHome Bluetooth Proxy](https://esphome.github.io/bluetooth-proxies/)) within range of at
  least one fixture. You don't need every light in range — the mesh relays from any one.
- An Avi-on / Halo Home account for first-time setup — or a backup file (see above).

## Installation

**HACS** → ⋮ → *Custom repositories* → add `https://github.com/tinic/ha-halo-home` as an
**Integration** → install → restart Home Assistant.

**Manual** → copy `custom_components/halo_home/` into your `config/custom_components/` folder
→ restart.

## Setup

*Settings → Devices & Services → Add Integration → **Halo Home*** (Home Assistant may
discover a fixture and prompt you automatically). Choose how to provide your passphrase:

- **Sign in to Avi-on** — the easy path while the servers are up. Your password is used once
  and not stored.
- **Restore from a backup file** — reads the `avion_backup.json` from `tools/avion_backup.py`.
  No account, no internet.

Your lights then appear as entities. Avi-on gives every fixture of a model the same name, so
lights are suffixed with their MAC to tell them apart — rename them in the UI as you like.

## Supported hardware

All Avi-on-platform lights use the same protocol and should work. Capabilities are detected
per device:

| Product | Capability |
|---|---|
| MicroEdge (HLB), Recessed Downlight (RL), Surface Downlight (BLD), Smart Bulb (A19) | dim + tunable white |
| Smart Dimmer, Lamp Dimmer, Light Adapter | dim only |
| Smart Switch | on/off only |
| Other Avi-on lights | detected automatically |

Wall dimmers, scene keypads, and the Access Bridge are controls, not lights, and are skipped.
There is no full-color (RGB) hardware in this product line.

Only the MicroEdge (HLB) has been tested on real hardware. **If you have other Halo or Avi-on
devices, please [open an issue](https://github.com/tinic/ha-halo-home/issues) and say whether
they worked** — that's the most helpful thing you can contribute.

> **Note:** transitions (the `transition:` parameter) are not supported — the hardware doesn't
> implement fading.

## Contributing

Issues and PRs welcome — especially reports from other Avi-on hardware and from Bluetooth-proxy
setups. Protocol details are in [docs/protocol.md](docs/protocol.md). The protocol core has no
Home Assistant or Bluetooth dependencies and can be tested without hardware:

```bash
pip install pytest cryptography && pytest
```

## Disclaimer

Not affiliated with or endorsed by Eaton, Cooper Lighting, or Avi-on. "HALO" and "HALO Home"
are trademarks of their respective owners. This integration was developed independently so
that hardware people already own keeps working after its servers are shut off.

## License

MIT — see [LICENSE](LICENSE).
