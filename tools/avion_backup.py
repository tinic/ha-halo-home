#!/usr/bin/env python3
"""Back up everything Avi-on's cloud knows about your Halo Home mesh.

The mesh passphrase is the irreplaceable bit: it is a static per-location
string, and it is the only thing that lets you command lights that are
already provisioned. Once you have it you never need the cloud again.

Stdlib only. Credentials are read interactively and never written to disk.
Output: avion_backup.json (mode 0600) in the current directory.
"""

import getpass
import json
import os
import sys
import urllib.error
import urllib.request

HOST = "https://api.avi-on.com"


def call(path, token=None, body=None):
    req = urllib.request.Request(f"{HOST}/{path}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", f"Token {token}")
    if body is not None:
        req.data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        sys.exit(f"FAIL {e.code} on /{path}\n{detail}")
    except urllib.error.URLError as e:
        sys.exit(f"Cannot reach {HOST}: {e.reason}")


def main():
    email = input("Avi-on / Halo Home email: ").strip()
    password = getpass.getpass("Password: ")

    session = call("sessions", body={"email": email, "password": password})
    if "credentials" not in session:
        sys.exit("Login succeeded but returned no credentials block.")
    token = session["credentials"]["auth_token"]
    print("authenticated")

    dump = {"locations": []}
    for stub in call("user/locations", token)["locations"]:
        pid = stub["pid"]
        location = call(f"locations/{pid}", token)["location"]
        devices = call(f"locations/{pid}/abstract_devices", token)["abstract_devices"]
        groups = call(f"locations/{pid}/groups", token)["groups"]
        dump["locations"].append(
            {"location": location, "abstract_devices": devices, "groups": groups}
        )

        real = [d for d in devices if d.get("type") == "device"]
        passphrase = location.get("passphrase") or ""
        print(f"\nlocation {pid}  {location.get('name')!r}")
        print(f"  passphrase : {len(passphrase)} chars, "
              f"starts {passphrase[:2]!r} ends {passphrase[-2:]!r}")
        print(f"  devices    : {len(real)} ({len(devices) - len(real)} non-device entries)")
        print(f"  groups     : {len(groups)}")
        for d in real:
            print(f"    avid={d.get('avid'):<6} pid={d.get('product_id')!s:<5} "
                  f"mac={d.get('friendly_mac_address')} {d.get('name')!r}")

    path = os.path.abspath("avion_backup.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(dump, f, indent=2, sort_keys=True)

    print(f"\nfull dump -> {path} (mode 0600)")
    print("The passphrase is in there. Copy it somewhere durable and offline.")


if __name__ == "__main__":
    main()
