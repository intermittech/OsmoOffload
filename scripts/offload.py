"""End-to-end offload CLI: everything new on the camera -> dated folders on disk.

Usage:
  python scripts/offload.py --base-dir "D:/DJI-Offload"          # transfer new files
  python scripts/offload.py --dry-run                            # plan only
  python scripts/offload.py --kinds video                        # filter
  python scripts/offload.py --template "{date}_{time}_{camera}_{seq}"

Base dir and template persist in config/appstate.json after first use.
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
from osmooffload.camera.session import CameraDatalink
from osmooffload.core import connect as conn
from osmooffload.core.offload import OffloadConfig, Offloader
from osmooffload.history import HistoryDB
from osmooffload.net.downloader import CameraHttp


def setup_logging(tag: str) -> Path:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = config.LOG_DIR / f"{tag}-{time.strftime('%Y%m%d-%H%M%S')}.log"
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


def fetch_records(identifier: str):
    """Blocking datalink phase with the documented port fallback."""
    log = logging.getLogger("osmo.offload")
    for attempt, (port, poke) in enumerate([(9004, True), (10004, False), (9004, True)], 1):
        dl = CameraDatalink(conn.CAMERA_IP, port=port, tcp_poke=poke, identifier=identifier)
        try:
            if not dl.open():
                continue
            dl.register()
            dl.enter_playback()
            records, _raw = dl.query_newest_page()
            if records:
                return records, dl.status
            log.warning("empty list on attempt %d (port %d)", attempt, port)
        finally:
            dl.close()
        time.sleep(1.0)
    return [], None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dir", type=Path, default=None)
    ap.add_argument("--template", default=None)
    ap.add_argument("--kinds", default="video,photo,other",
                    help="comma list of: video,photo,other")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-kind-folders", action="store_true")
    args = ap.parse_args()

    logfile = setup_logging("offload")
    log = logging.getLogger("osmo.offload")
    log.info("log file: %s", logfile)

    state = config.load_state()
    base_dir = args.base_dir or Path(state.get("base_dir", "D:/DJI-Offload"))
    template = args.template or state.get("template", "{original}")
    state["base_dir"] = str(base_dir)
    state["template"] = template
    config.save_state(state)

    saved = state.get("cameras", {})
    address = next(iter(saved), None)

    link, creds, target = await conn.establish(
        state, address,
        on_approval_needed=lambda: print("\n>>> TAP APPROVE ON THE CAMERA SCREEN <<<\n", flush=True),
    )
    try:
        identifier = config.get_identifier(state)
        records, cam_status = await asyncio.to_thread(fetch_records, identifier)
        if not records:
            log.error("no media records — see log for datalink details")
            return 3
        cam_folder = (target.name or f"{target.model_name}-{target.address[-5:].replace(':', '')}").replace(" ", "")
        cfg = OffloadConfig(
            base_dir=base_dir,
            camera_folder=cam_folder,
            template=template,
            kinds=tuple(k.strip() for k in args.kinds.split(",") if k.strip()),
            split_kind_folders=not args.no_kind_folders,
        )
        db = HistoryDB(config.CONFIG_DIR / "history.sqlite3")
        http = CameraHttp(conn.CAMERA_IP)
        off = Offloader(cfg, db, http)

        plan = await asyncio.to_thread(off.plan, records)
        new = [p for p in plan if not p.skipped]
        log.info("=== PLAN: %d on camera, %d to transfer, %d skipped ===",
                 len(plan), len(new), len(plan) - len(new))
        for p in plan:
            log.info("  %-46s %11s B  %s", p.record.name, p.size if p.size is not None else "?",
                     p.skipped or f"-> {p.dest}")
        if cam_status:
            log.info("battery %s%%  SD %s/%s MiB  internal %s/%s MiB",
                     cam_status.battery_pct, cam_status.sd_free_mib, cam_status.sd_total_mib,
                     cam_status.internal_free_mib, cam_status.internal_total_mib)
        if args.dry_run or not new:
            print(f"\nPLAN OK: {len(new)} to transfer, {len(plan) - len(new)} skipped (dry-run)")
            return 0

        t0 = time.monotonic()
        last_print = 0.0

        def on_progress(prog) -> None:
            nonlocal last_print
            now = time.monotonic()
            if now - last_print >= 1.0:
                last_print = now
                pct = 100 * (prog.done_bytes + prog.current_done) / prog.total_bytes if prog.total_bytes else 0
                rate = (prog.done_bytes + prog.current_done) / max(now - t0, 0.01) / 1e6
                print(f"  [{pct:5.1f}%] {prog.current_name} "
                      f"({prog.done_files}/{prog.total_files} files, {rate:.1f} MB/s)", flush=True)

        result = await asyncio.to_thread(off.run, plan, on_progress)
        db.close()
        http.close()
        secs = time.monotonic() - t0
        log.info("=== DONE: %d/%d files, %.1f MB in %.0fs (%.1f MB/s avg), %d failures ===",
                 result.done_files, result.total_files, result.done_bytes / 1e6, secs,
                 result.done_bytes / max(secs, 0.01) / 1e6, len(result.failures))
        for f in result.failures:
            log.error("failure: %s", f)
        print(f"\nOFFLOAD {'OK' if not result.failures else 'PARTIAL'}: "
              f"{result.done_files}/{result.total_files} files, "
              f"{result.done_bytes / 1e6:.1f} MB -> {base_dir}")
        return 0 if not result.failures else 4
    finally:
        await conn.teardown(link)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
