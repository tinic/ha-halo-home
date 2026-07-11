"""One-time Avi-on cloud client, used only by the config flow.

The mesh passphrase is a static, per-location string. Fetch it once, and the
integration never contacts the cloud again — every command and every state read
after setup is local Bluetooth.

Cooper/Eaton discontinued HALO Home in Nov 2023 and committed to running this API
for roughly five years. When it goes dark, this module stops working and there is
no way to recover a passphrase you did not save. See README.md.
"""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from . import products

API_HOST = "https://api.avi-on.com"
_TIMEOUT = aiohttp.ClientTimeout(total=20)


class AvionCloudError(Exception):
    """The cloud could not be reached, or returned something unusable."""


class AvionAuthError(AvionCloudError):
    """Email/password rejected."""


async def _request(
    session: aiohttp.ClientSession,
    path: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Token {token}"
    try:
        async with session.request(
            "POST" if body is not None else "GET",
            f"{API_HOST}/{path}",
            headers=headers,
            json=body,
            timeout=_TIMEOUT,
        ) as resp:
            if resp.status in (401, 403):
                raise AvionAuthError("credentials rejected")
            if resp.status >= 400:
                raise AvionCloudError(f"HTTP {resp.status} on /{path}")
            return await resp.json()
    except AvionCloudError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        raise AvionCloudError(f"cannot reach {API_HOST}: {err}") from err


async def async_fetch_locations(
    session: aiohttp.ClientSession, email: str, password: str
) -> list[dict[str, Any]]:
    """Log in and return every location, with its passphrase and device list.

    Each location: {pid, name, passphrase, devices: [{avid, name, mac}, ...]}.
    """
    auth = await _request(session, "sessions", body={"email": email, "password": password})
    try:
        token = auth["credentials"]["auth_token"]
    except (KeyError, TypeError) as err:
        raise AvionCloudError("login returned no credentials block") from err

    stubs = (await _request(session, "user/locations", token=token)).get("locations", [])
    if not stubs:
        raise AvionCloudError("account has no locations")

    locations: list[dict[str, Any]] = []
    for stub in stubs:
        pid = stub["pid"]
        location = (await _request(session, f"locations/{pid}", token=token))["location"]
        raw = (
            await _request(session, f"locations/{pid}/abstract_devices", token=token)
        )["abstract_devices"]

        raw_groups = (await _request(session, f"locations/{pid}/groups", token=token)).get(
            "groups", []
        )

        passphrase = location.get("passphrase")
        devices = products.dedupe_names(
            [products.parse_device(d) for d in raw if products.is_light(d)]
        )
        if not passphrase or not devices:
            continue

        locations.append(
            {
                "pid": str(pid),
                "name": location.get("name") or f"Location {pid}",
                "passphrase": passphrase,
                "devices": devices,
                "groups": products.parse_groups(raw_groups, devices, raw),
            }
        )

    if not locations:
        raise AvionCloudError("no location had both a passphrase and devices")
    return locations
