"""Milestone-2 test: BLE wake -> camera AP up -> Windows joins -> HTTP reachable.

Uses the credentials saved by probe.py. Expects the already-paired fast path.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from osmooffload import config
from osmooffload.ble import scanner
from osmooffload.ble.link import BleLink
from osmooffload.ble.pairing import pair_and_get_credentials
from osmooffload.duml import commands as cmd
from osmooffload.net import wifi

CAMERA_IP = "192.168.2.1"


def setup_logging() -> Path:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = config.LOG_DIR / f"wake-{time.strftime('%Y%m%d-%H%M%S')}.log"
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, stream=sys.stdout)
    fh = logging.FileHandler(logfile, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt))
    logging.getLogger().addHandler(fh)
    logging.getLogger().setLevel(logging.DEBUG)
    for noisy in ("bleak", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.INFO)
    return logfile


async def http_poke(path: str) -> str:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(CAMERA_IP, 80), timeout=8.0
    )
    writer.write(
        f"GET {path} HTTP/1.1\r\nHost: {CAMERA_IP}\r\nConnection: close\r\n\r\n".encode()
    )
    await writer.drain()
    try:
        data = await asyncio.wait_for(reader.read(600), timeout=8.0)
    finally:
        writer.close()
    return data.decode("latin-1", errors="replace").split("\r\n\r\n")[0]


async def main() -> int:
    logfile = setup_logging()
    log = logging.getLogger("osmo.wake")
    log.info("log file: %s", logfile)

    state = config.load_state()
    cams = state.get("cameras", {})
    if not cams:
        log.error("no saved camera — run probe.py first")
        return 2
    address, cam = next(iter(cams.items()))
    ssid, password = cam["ssid"], cam["password"]
    identifier = config.get_identifier(state)
    log.info("camera: %s (%s), ssid=%s", cam.get("model_name"), address, ssid)

    found = await scanner.scan(6.0)
    target = next((c for c in found if c.address.lower() == address.lower()), None)
    if not target:
        log.error("saved camera not in BLE range")
        return 2

    link = BleLink(target.device)
    try:
        await link.connect()
        creds = await pair_and_get_credentials(link, identifier, approval_timeout=30.0)
        log.info("session up (already_paired=%s) ssid=%s", creds.was_already_paired, creds.ssid)
        ssid, password = creds.ssid, creds.password

        # Wait for the AP; nudge with ConnectToWiFi (fallback) if it's shy.
        t0 = asyncio.get_running_loop().time()
        nudged = False
        visible = False
        while asyncio.get_running_loop().time() - t0 < 45:
            if await wifi.network_visible(ssid):
                visible = True
                log.info("AP visible after %.1fs%s", asyncio.get_running_loop().time() - t0,
                         " (after 07/47 nudge)" if nudged else "")
                break
            if not nudged and asyncio.get_running_loop().time() - t0 > 8:
                log.info("AP not yet visible — sending ConnectToWiFi 07/47 fallback")
                try:
                    r = await link.request(
                        cmd.connect_to_wifi(link.next_msg_id(), ssid, password), timeout=3.0
                    )
                    log.info("07/47 reply: %s", r.payload.hex())
                except asyncio.TimeoutError:
                    log.info("07/47: no reply")
                nudged = True
            await asyncio.sleep(2.0)
        if not visible:
            log.error("camera AP never appeared in scan results")
            return 3

        prev = await wifi.current_profile()
        if prev and prev != ssid:
            log.info("current WiFi profile %r will be restored afterwards", prev)

        await wifi.ensure_profile(ssid, password)
        st = await wifi.connect(ssid, timeout=30.0)
        ip = await wifi.wait_for_ip("192.168.2.", timeout=20.0)
        log.info("joined %s, our IP %s, band=%s signal=%s", ssid, ip,
                 st.get("band") or st.get("radio type"), st.get("signal"))

        for path in ("/v2", "/v2?storage=1&path=DCIM"):
            try:
                head = await http_poke(path)
                log.info("HTTP %s ->\n%s", path, head)
            except Exception as e:
                log.info("HTTP %s -> %s: %s", path, type(e).__name__, e)

        log.info("WAKE+JOIN OK: ap_visible=%s ip=%s", visible, ip)
        return 0
    finally:
        await link.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
