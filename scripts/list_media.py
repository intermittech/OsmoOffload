"""Milestone-3 test: full pipeline to a decoded media list + live status.

BLE session (wake + keepalive) -> WiFi join -> UDP datalink -> playback ->
0x00/0x26 list -> decoded records, plus battery/storage from the pushes.
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
from osmooffload.camera.session import CameraDatalink, datalink_configs
from osmooffload.duml import commands as cmd
from osmooffload.net import wifi

CAMERA_IP = "192.168.2.1"


def setup_logging() -> Path:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = config.LOG_DIR / f"list-{time.strftime('%Y%m%d-%H%M%S')}.log"
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


def run_datalink(identifier: str, model_id: int | None = None):
    """Blocking; runs in a thread. Returns (records, status, raw_blob_len)."""
    log = logging.getLogger("osmo.list")
    # 9004+poke is the family default; the Xtra Edge Pro / Action 5 Pro is
    # udp/10004-only, so order the candidates by the saved model id.
    port_configs = datalink_configs(model_id) + datalink_configs(model_id)[:1]
    for attempt, (port, poke) in enumerate(port_configs, start=1):
        dl = CameraDatalink(CAMERA_IP, port=port, tcp_poke=poke,
                            identifier=identifier, model_id=model_id)
        try:
            if not dl.open():
                if attempt < len(port_configs):
                    log.warning("no handshake on udp/%d — trying alternate config", port)
                    continue
                return [], dl.status, 0
            dl.register()
            dl.enter_playback()
            records, raw = dl.query_newest_page()
            if records or attempt == len(port_configs):
                return records, dl.status, len(raw)
            log.warning("empty list (channel_mismatch=%s) — retrying with a fresh session",
                        dl.channel_mismatch)
        finally:
            dl.close()
        time.sleep(1.0)
    return [], dl.status, 0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", help="BLE address of the saved camera to use")
    args = ap.parse_args()

    logfile = setup_logging()
    log = logging.getLogger("osmo.list")
    log.info("log file: %s", logfile)

    state = config.load_state()
    cams = state.get("cameras", {})
    if not cams:
        log.error("no saved camera — run probe.py first")
        return 2
    if args.address:
        match = {a: c for a, c in cams.items() if a.lower() == args.address.lower()}
        if not match:
            log.error("address %s is not a saved camera — run probe.py for it first", args.address)
            return 2
        address, cam = next(iter(match.items()))
    else:
        address, cam = next(iter(cams.items()))
    model_id = cam.get("model_id")
    log.info("using saved camera %s (%s, model_id=%s)",
             address, cam.get("model_name", "?"), model_id)
    identifier = config.get_identifier(state)

    target = None
    for attempt in range(3):
        found = await scanner.scan(12.0)
        target = next((c for c in found if c.address.lower() == address.lower()), None)
        if target:
            break
        log.info("camera not seen yet (scan %d/3)...", attempt + 1)
    if not target:
        log.error("camera not in BLE range")
        return 2

    link = BleLink(target.device)
    try:
        await link.connect()
        creds = await pair_and_get_credentials(link, identifier, approval_timeout=30.0)
        ssid, password = creds.ssid, creds.password

        t0 = asyncio.get_running_loop().time()
        nudged = False
        while asyncio.get_running_loop().time() - t0 < 45:
            if await wifi.network_visible(ssid):
                break
            if not nudged and asyncio.get_running_loop().time() - t0 > 8:
                try:
                    await link.request(cmd.connect_to_wifi(link.next_msg_id(), ssid, password), timeout=3.0)
                except asyncio.TimeoutError:
                    pass
                nudged = True
            await asyncio.sleep(2.0)
        else:
            log.error("camera AP never appeared")
            return 3

        await wifi.ensure_profile(ssid, password)
        await wifi.connect(ssid, timeout=30.0)
        await wifi.wait_for_ip("192.168.2.", timeout=20.0)
        log.info("WiFi joined — starting datalink")

        records, status, blob_len = await asyncio.to_thread(
            run_datalink, identifier, model_id)

        log.info("=== STATUS ===")
        log.info(
            "battery %s%% (%s mV, %s mA)  playback=%s stores=%s",
            status.battery_pct, status.voltage_mv, status.current_ma,
            status.playback, status.store_count,
        )
        log.info(
            "SD: %s/%s MiB free  internal: %s/%s MiB free  active: %s/%s",
            status.sd_free_mib, status.sd_total_mib,
            status.internal_free_mib, status.internal_total_mib,
            status.active_free_mib, status.active_total_mib,
        )
        log.info("=== MEDIA (%d records, blob %d B) ===", len(records), blob_len)
        for r in records:
            log.info(
                "%-42s %10s B  handle=0x%08x store=%s star=%s thumb=%s",
                f"{r.media_path}::{r.ext}", r.size, r.handle,
                {0: "SD", 1: "int", None: "?"}[r.storage], r.star,
                (r.thumb_path or "-")[:40],
            )
        print(f"\nLIST OK: {len(records)} files")
        return 0
    finally:
        await link.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
