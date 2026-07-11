"""Constants for the Halo Home (Avi-on CSRmesh) integration."""

DOMAIN = "halo_home"

# GATT write/notify characteristics (Legacy MTL). Packet is split at 20 bytes:
# low -> CHAR_LOW, overflow -> CHAR_HIGH. Notifications also arrive here.
CHAR_LOW = "c4edc000-9daf-11e3-8003-00025b000b00"
CHAR_HIGH = "c4edc000-9daf-11e3-8004-00025b000b00"

# BLE advertised name of every Avi-on/Halo node.
ADVERTISED_NAME = "Avi-on"

# Per-fixture capabilities (dimmable / tunable white, and over what Kelvin range)
# live in products.py — they vary by product and are resolved per device.

# Config entry data keys.
CONF_PASSPHRASE = "passphrase"
CONF_DEVICES = "devices"
CONF_GROUPS = "groups"
CONF_MACS = "macs"
CONF_PID = "pid"

# Poll cadence. State also arrives as push notifications between polls (e.g. wall
# dimmer changes), so this is a backstop, not the only update path.
POLL_INTERVAL_SECONDS = 60
