"""GUI <-> core bridge.

A dedicated worker thread runs an asyncio loop (BLE needs one). The controller
schedules one job at a time on it and reports back via Qt signals, which are
queued across threads automatically. Blocking phases (datalink, HTTP) run in
executor threads via asyncio.to_thread inside the job.

Session policy: connect -> work -> teardown. Holding the BLE keepalive between
sessions would keep the camera awake and drain its battery, so we let it sleep
and show last-known status instead.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .. import config
from ..camera.session import CameraDatalink
from ..core import connect as conn
from ..core.offload import OffloadConfig, Offloader, SessionProgress
from ..history import HistoryDB
from ..net.downloader import CameraHttp

log = logging.getLogger("osmo.ui")

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _keep_awake(on: bool) -> None:
    try:
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


class CameraController(QObject):
    state_changed = Signal(str, str)  # state key, human detail
    camera_seen = Signal(bool)  # watcher presence ping (auto-transfer mode)
    auto_started = Signal()
    status_updated = Signal(dict)  # battery/storage fields
    plan_ready = Signal(list)  # [(name, size, note)] for the queue
    thumb_ready = Signal(str, str)  # file name, cached thumbnail path
    file_progress = Signal(str, int, int, int, int, float)  # name, done, total, fdone, ftotal, MB/s
    session_done = Signal(dict)
    approval_needed = Signal()
    error = Signal(str)
    log_line = Signal(str)

    def __init__(self, settings: dict, parent: QObject | None = None):
        super().__init__(parent)
        self.settings = settings
        self._busy = False
        self._cancel = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_session_end = 0.0
        self._auto_backoff_until = 0.0
        self._thread = threading.Thread(target=self._run_loop, name="osmo-worker", daemon=True)
        self._thread.start()
        self.db = HistoryDB(config.CONFIG_DIR / "history.sqlite3")
        while self._loop is None:
            time.sleep(0.01)
        asyncio.run_coroutine_threadsafe(self._watcher(), self._loop)

    # -- worker loop ---------------------------------------------------------

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro) -> None:
        if self._busy:
            self.log_line.emit("Busy with another camera session.")
            return
        while self._loop is None:
            time.sleep(0.01)
        self._busy = True
        self._cancel = False

        async def wrapped():
            try:
                await coro
            except conn.ConnectError as e:
                self.state_changed.emit("error", str(e))
                self.error.emit(str(e))
            except Exception as e:  # surface anything else honestly
                log.exception("session job failed")
                self.state_changed.emit("error", f"{type(e).__name__}: {e}")
                self.error.emit(f"{type(e).__name__}: {e}")
            finally:
                self._busy = False
                self._last_session_end = time.monotonic()
                _keep_awake(False)

        asyncio.run_coroutine_threadsafe(wrapped(), self._loop)

    def cancel(self) -> None:
        self._cancel = True
        self.log_line.emit("Cancelling after the current chunk…")

    @property
    def busy(self) -> bool:
        return self._busy

    # -- public actions ------------------------------------------------------

    def refresh(self) -> None:
        """Connect, read status + media list, populate the plan — no downloads."""
        self._submit(self._session(transfer=False))

    def transfer(self) -> None:
        self._submit(self._session(transfer=True))

    def transfer_selected(self, names: list) -> None:
        """Transfer exactly the named files (Media tab cherry-pick)."""
        self._submit(self._session(transfer=True, selected=set(names)))

    # -- auto-transfer watcher ----------------------------------------------

    AUTO_COOLDOWN_S = 15 * 60  # min gap between auto sessions
    AUTO_ERROR_BACKOFF_S = 30 * 60  # after a failed auto run
    WATCH_INTERVAL_S = 45.0

    async def _watcher(self) -> None:
        """Passive BLE presence scan; auto-starts a transfer when the saved
        camera appears, respecting cooldown and error backoff. Never runs
        while a session is busy, never wakes the camera by itself."""
        from ..ble import scanner as ble_scanner

        while True:
            await asyncio.sleep(self.WATCH_INTERVAL_S)
            if not self.settings.get("auto_transfer") or self._busy:
                continue
            now = time.monotonic()
            if now - self._last_session_end < self.AUTO_COOLDOWN_S:
                continue
            if now < self._auto_backoff_until:
                continue
            try:
                cams = await ble_scanner.scan(6.0)
            except Exception as e:
                log.debug("watcher scan failed: %s", e)
                continue
            saved = {a.lower() for a in config.load_state().get("cameras", {})}
            present = any(c.address.lower() in saved for c in cams)
            self.camera_seen.emit(present)
            if present and not self._busy:
                log.info("watcher: camera in range — auto-transfer")
                self.auto_started.emit()
                self.transfer()
                # a failed auto session backs off so we don't retry-loop
                self._auto_backoff_until = time.monotonic() + self.AUTO_ERROR_BACKOFF_S

    # -- the session job -----------------------------------------------------

    def _on_ble_frame(self, frame) -> None:
        # battery push over BLE (0x0D/0x02): percent @20, current i32-LE @5
        if frame.key == (0x0D, 0x02) and len(frame.payload) >= 21:
            cur = int.from_bytes(frame.payload[5:9], "little", signed=True)
            self.status_updated.emit({"battery_pct": frame.payload[20], "current_ma": cur})

    async def _session(self, transfer: bool, selected: set | None = None) -> None:
        state = config.load_state()
        saved = state.get("cameras", {})
        address = next(iter(saved), None)

        self.state_changed.emit("connecting", "Scanning for camera…")
        link, creds, target = await conn.establish(
            state, address,
            on_approval_needed=lambda: self.approval_needed.emit(),
        )
        link.on_frame = self._on_ble_frame
        try:
            self.state_changed.emit("connecting", "Opening data session…")
            identifier = config.get_identifier(state)
            records, cam_status = await asyncio.to_thread(self._fetch_records, identifier)
            if cam_status:
                self.status_updated.emit(
                    {
                        "battery_pct": cam_status.battery_pct,
                        "current_ma": cam_status.current_ma,
                        "sd_total_mib": cam_status.sd_total_mib,
                        "sd_free_mib": cam_status.sd_free_mib,
                        "internal_total_mib": cam_status.internal_total_mib,
                        "internal_free_mib": cam_status.internal_free_mib,
                    }
                )
            if not records:
                raise conn.ConnectError("Connected, but the camera returned no media list.")

            cam_folder = (
                target.name or creds.ssid
                or f"{target.model_name}-{target.address[-5:].replace(':', '')}"
            ).replace(" ", "")
            cfg = OffloadConfig(
                base_dir=Path(self.settings.get("base_dir", "D:/DJI-Offload")),
                camera_folder=cam_folder,
                template=self.settings.get("template", "{original}"),
            )
            http = CameraHttp(conn.CAMERA_IP)
            off = Offloader(cfg, self.db, http)

            self.state_changed.emit("connected", "Planning…")
            plan = await asyncio.to_thread(off.plan, records)
            if selected is not None:
                for p in plan:
                    if not p.skipped and p.record.name not in selected:
                        p.skipped = "not selected"
            self.plan_ready.emit(
                [(p.record.name, p.size or 0, p.skipped or "queued") for p in plan]
            )
            await asyncio.to_thread(self._fetch_thumbs, http, plan, cam_folder)
            todo = [p for p in plan if not p.skipped]
            if not transfer:
                self.state_changed.emit(
                    "connected",
                    f"{len(todo)} new of {len(plan)} on camera",
                )
                self.session_done.emit(
                    {"mode": "refresh", "new": len(todo), "total": len(plan)}
                )
                return
            if not todo:
                self.state_changed.emit("connected", "Nothing new to transfer")
                self.session_done.emit({"mode": "transfer", "files": 0, "bytes": 0, "failures": []})
                return

            self.state_changed.emit("transferring", f"{len(todo)} files…")
            if self.settings.get("keep_awake", True):
                _keep_awake(True)
            t0 = time.monotonic()
            last_emit = [0.0]

            def on_progress(prog: SessionProgress) -> None:
                # Throttle: raw callbacks fire per 256 KB chunk (~240/s at
                # 60 MB/s) — emitting each would flood the Qt event loop.
                now = time.monotonic()
                complete = prog.current_total and prog.current_done >= prog.current_total
                if now - last_emit[0] < 0.1 and not complete:
                    return
                last_emit[0] = now
                done = prog.done_bytes + prog.current_done
                rate = done / max(now - t0, 0.01) / 1e6
                self.file_progress.emit(
                    prog.current_name, prog.current_done, prog.current_total,
                    prog.done_files, prog.total_files, rate,
                )

            result = await asyncio.to_thread(
                off.run, plan, on_progress, lambda: self._cancel
            )
            http.close()
            secs = time.monotonic() - t0
            summary = {
                "mode": "transfer",
                "files": result.done_files,
                "files_total": result.total_files,
                "bytes": result.done_bytes,
                "seconds": secs,
                "rate_mbs": result.done_bytes / max(secs, 0.01) / 1e6,
                "failures": result.failures,
            }
            self.state_changed.emit(
                "connected",
                f"Done: {result.done_files}/{result.total_files} files "
                f"({result.done_bytes / 1e6:.0f} MB, {summary['rate_mbs']:.0f} MB/s)",
            )
            if not result.failures:
                self._auto_backoff_until = 0.0  # clean run: only cooldown gates the next auto
            self.session_done.emit(summary)
        finally:
            _keep_awake(False)
            await conn.teardown(link)
            self.state_changed.emit("idle", "Camera released (it will sleep)")

    def _fetch_thumbs(self, http: CameraHttp, plan, cam_folder: str) -> None:
        """Fetch + cache camera thumbnails (.scr JPEGs, ~tens of KB each) and
        announce each one. Cache hits cost nothing and emit immediately."""
        thumb_dir = config.CONFIG_DIR / "thumbs" / cam_folder
        thumb_dir.mkdir(parents=True, exist_ok=True)
        for item in plan:
            rec = item.record
            cache = thumb_dir / f"{rec.name}.jpg"
            if cache.exists():
                self.thumb_ready.emit(rec.name, str(cache))
                continue
            if not rec.thumb_path or item.storage_idx is None:
                continue
            # the manifest thumb path has no extension; bodies serve .scr or .thm
            for cand in (rec.thumb_path + ".scr", rec.thumb_path + ".thm", rec.thumb_path):
                data = http.fetch_small(item.storage_idx, cand)
                if data and data[:2] == b"\xff\xd8":  # JPEG magic
                    cache.write_bytes(data)
                    self.thumb_ready.emit(rec.name, str(cache))
                    break

    def _fetch_records(self, identifier: str):
        last_status = None
        for attempt, (port, poke) in enumerate([(9004, True), (10004, False), (9004, True)], 1):
            dl = CameraDatalink(conn.CAMERA_IP, port=port, tcp_poke=poke, identifier=identifier)
            try:
                if not dl.open():
                    continue
                dl.register()
                dl.enter_playback()
                records, _raw = dl.query_newest_page()
                last_status = dl.status
                if records:
                    return records, dl.status
                log.warning("empty list on attempt %d (port %d)", attempt, port)
            finally:
                dl.close()
            time.sleep(1.0)
        return [], last_status
