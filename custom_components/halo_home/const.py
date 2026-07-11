"""Constants for the Halo Home (Avi-on CSRmesh) integration."""

DOMAIN = "halo_home"

# GATT write/notify characteristics (Legacy MTL). Packet is split at 20 bytes:
# low -> CHAR_LOW, overflow -> CHAR_HIGH. Notifications also arrive here.
CHAR_LOW = "c4edc000-9daf-11e3-8003-00025b000b00"
CHAR_HIGH = "c4edc000-9daf-11e3-8004-00025b000b00"

# BLE advertised name of every Avi-on/Halo node.
ADVERTISED_NAME = "Avi-on"

# Tunable-white range of the MicroEdge (HLB) downlights this was developed against.
# Fixtures that report outside it still work — the host clamps to this range.
MIN_KELVIN = 2700
MAX_KELVIN = 5000

# Config entry data keys.
CONF_PASSPHRASE = "passphrase"
CONF_DEVICES = "devices"
CONF_MACS = "macs"
CONF_PID = "pid"

# Poll cadence. State also arrives as push notifications between polls (e.g. wall
# dimmer changes), so this is a backstop, not the only update path.
POLL_INTERVAL_SECONDS = 60
