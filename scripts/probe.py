"""Milestone-1 probe: find the camera over BLE, pair, pull WiFi credentials.

Usage:
  python scripts/probe.py --scan-only          # just list DJI cameras in range
  python scripts/probe.py                      # full probe (pair + credentials)
  python scripts/probe.py --address AA:BB:...  # target a specific camera

First run shows an approval prompt (reading `OSMO`) on the camera screen —
tap approve there. Everything is logged to logs/probe-*.log (hex included).
"""

from __future__ import annotations

import argparse
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


def setup_logging() -> Path:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = config.LOG_DIR / f"probe-{time.strftime('%Y%m%d-%H%M%S')}.log"
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


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-only", action="store_true")
    ap.add_argument("--address", help="BLE address of the camera to use")
    ap.add_argument("--scan-time", type=float, default=8.0)
    ap.add_argument("--approve-timeout", type=float, default=120.0)
    args = ap.parse_args()

    logfile = setup_logging()
    log = logging.getLogger("osmo.probe")
    log.info("log file: %s", logfile)

    log.info("scanning %.0fs for DJI cameras...", args.scan_time)
    cams = await scanner.scan(args.scan_time)
    if not cams:
        log.error("no DJI cameras found. Is the camera on and in range?")
        return 2
    for c in cams:
        log.info(
            "found: %-24s %s rssi=%-4d model=%s mfr=[%s]",
            c.name or "(no name)", c.address, c.rssi, c.model_name, c.mfr_hex,
        )
    if args.scan_only:
        return 0

    target = None
    if args.address:
        target = next((c for c in cams if c.address.lower() == args.address.lower()), None)
        if not target:
            log.error("address %s not among scan results", args.address)
            return 2
    else:
        pocket = [c for c in cams if "Pocket" in c.model_name or c.name.startswith("OsmoPocket")]
        target = (pocket or cams)[0]
    log.info("target: %s (%s, %s)", target.name or target.address, target.model_name, target.address)

    state = config.load_state()
    identifier = config.get_identifier(state)
    log.info("pairing identifier: %s", identifier)

    link = BleLink(target.device)
    try:
        await link.connect()
        log.info("*** negotiated MTU: %d (docs say >500 can break the camera) ***", link.mtu)
        for svc in link._client.services:
            chars = ", ".join(f"{ch.uuid[4:8]}[{'|'.join(ch.properties)}]" for ch in svc.characteristics)
            log.debug("service %s: %s", svc.uuid, chars)

        creds = await pair_and_get_credentials(
            link,
            identifier,
            approval_timeout=args.approve_timeout,
            on_approval_needed=lambda: print(
                "\n>>> TAP APPROVE ON THE CAMERA SCREEN NOW (prompt reads OSMO) <<<\n",
                flush=True,
            ),
        )

        config.remember_camera(
            state,
            target.address,
            name=target.name,
            model_id=target.model_id,
            model_name=target.model_name,
            ssid=creds.ssid,
            password=creds.password,
            wifi_mac=creds.mac,
        )
        log.info("credentials saved to %s", config.STATE_FILE)
        log.info(
            "PROBE OK: ssid=%s password_len=%d mac=%s already_paired=%s mtu=%d",
            creds.ssid, len(creds.password), creds.mac, creds.was_already_paired, link.mtu,
        )
        # hold the session a few beats so the wake can take effect (AP bring-up)
        await asyncio.sleep(3)
        return 0
    finally:
        await link.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
