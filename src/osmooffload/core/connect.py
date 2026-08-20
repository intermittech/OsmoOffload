"""Shared bring-up: BLE session (pair + wake + creds) then WiFi AP join.

Async; returns a live BleLink whose keepalive holds the camera awake. Callers
run the blocking datalink/HTTP work in a thread while this link stays up.
"""

from __future__ import annotations

import asyncio
import logging

from .. import config
from ..ble import scanner
from ..ble.link import BleLink
from ..ble.pairing import WifiCredentials, pair_and_get_credentials
from ..duml import commands as cmd
from ..net import wifi

log = logging.getLogger("osmo.connect")

CAMERA_IP = "192.168.2.1"


class ConnectError(Exception):
    pass


async def find_camera(address: str | None, scans: int = 3, scan_time: float = 12.0):
    """Scan for the saved (or any) camera, tolerating sleep-mode advert gaps."""
    for attempt in range(scans):
        found = await scanner.scan(scan_time)
        if address:
            target = next((c for c in found if c.address.lower() == address.lower()), None)
        else:
            pockets = [c for c in found if "Pocket" in c.model_name]
            target = (pockets or found or [None])[0]
        if target:
            return target
        log.info("camera not seen (scan %d/%d)...", attempt + 1, scans)
    raise ConnectError(
        "Camera not found over Bluetooth. Is it powered on (or sleeping upright) and in range?"
    )


async def establish(
    state: dict,
    address: str | None = None,
    approval_timeout: float = 120.0,
    on_approval_needed=None,
) -> tuple[BleLink, WifiCredentials, object]:
    """BLE connect + pair + wake + creds + AP join. Returns (link, creds, found)."""
    identifier = config.get_identifier(state)
    target = await find_camera(address)
    log.info("camera: %s (%s, rssi %d)", target.model_name, target.address, target.rssi)

    link = BleLink(target.device)
    await link.connect()
    try:
        creds = await pair_and_get_credentials(
            link, identifier, approval_timeout=approval_timeout,
            on_approval_needed=on_approval_needed,
        )
        config.remember_camera(
            state, target.address,
            name=target.name or None, model_id=target.model_id,
            model_name=target.model_name, ssid=creds.ssid,
            password=creds.password, wifi_mac=creds.mac,
        )

        loop = asyncio.get_running_loop()
        t0 = loop.time()
        nudged = False
        while loop.time() - t0 < 45:
            if await wifi.network_visible(creds.ssid):
                log.info("camera AP visible after %.1fs", loop.time() - t0)
                break
            if not nudged and loop.time() - t0 > 8:
                try:
                    await link.request(
                        cmd.connect_to_wifi(link.next_msg_id(), creds.ssid, creds.password),
                        timeout=3.0,
                    )
                except asyncio.TimeoutError:
                    pass
                nudged = True
            await asyncio.sleep(2.0)
        else:
            raise ConnectError("The camera's WiFi network never appeared.")

        await wifi.ensure_profile(creds.ssid, creds.password)
        st = await wifi.connect(creds.ssid, timeout=30.0)
        ip = await wifi.wait_for_ip("192.168.2.", timeout=20.0)
        log.info("WiFi joined (%s, our IP %s, signal %s)", creds.ssid, ip, st.get("signal"))
        return link, creds, target
    except BaseException:
        await link.disconnect()
        raise


async def teardown(link: BleLink, restore_profile: str | None = None) -> None:
    await link.disconnect()
    if restore_profile:
        try:
            await wifi.connect(restore_profile, timeout=20.0)
            log.info("restored WiFi profile %r", restore_profile)
        except Exception as e:
            log.warning("could not restore WiFi profile %r: %s", restore_profile, e)
