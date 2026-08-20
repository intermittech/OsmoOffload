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
from ..net.wifi import rejoin_sync as wifi_rejoin

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

        # every camera session gets its own DEBUG log file, linked from history
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.current_log_path = str(
            config.LOG_DIR / f"session-{time.strftime('%Y%m%d-%H%M%S')}.log"
        )
        handler = logging.FileHandler(self.current_log_path, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
        )
        root = logging.getLogger()
        if root.level > logging.DEBUG:
            root.setLevel(logging.DEBUG)
        for noisy in ("bleak", "asyncio"):
            logging.getLogger(noisy).setLevel(logging.INFO)
        root.addHandler(handler)

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
                root.removeHandler(handler)
                handler.close()

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

    def delete_offloaded(self) -> None:
        """Delete files from the camera that have a VERIFIED copy on disk."""
        self._submit(self._delete_session())

    def usb_ingest(self) -> None:
        """Ingest from cabled DJI volumes (both banks) — same pipeline, no radio."""
        self._submit(self._usb_session())

    async def _usb_session(self) -> None:
        from ..core.usb_ingest import UsbIngester, find_dji_volumes

        self.state_changed.emit("connecting", "Looking for DJI volumes on USB…")
        vols = await asyncio.to_thread(find_dji_volumes)
        if not vols:
            raise conn.ConnectError(
                "No DJI camera volumes found. Connect the camera by USB "
                "(it must present as a drive) and try again."
            )
        state = config.load_state()
        saved = state.get("cameras", {})
        if len(saved) == 1:
            cam = next(iter(saved.values()))
            cam_folder = (cam.get("name") or cam.get("ssid") or "Camera").replace(" ", "")
        else:
            cam_folder = f"USB-{vols[0].label}".replace(" ", "")

        kinds = ["other"]
        if self.settings.get("kinds_video", True):
            kinds.append("video")
        if self.settings.get("kinds_photo", True):
            kinds.append("photo")
        cfg = OffloadConfig(
            base_dir=Path(self.settings.get("base_dir", "D:/DJI-Offload")),
            camera_folder=cam_folder,
            template=self.settings.get("template", "{original}"),
            kinds=tuple(kinds),
            log_path=getattr(self, "current_log_path", None),
        )

        def blocking() -> tuple[SessionProgress, list]:
            ingester = UsbIngester(cfg, self.db)
            plans = [(v, ingester.plan(v)) for v in vols]
            rows = [
                (uf.name, uf.size, skip or "queued")
                for _v, items in plans
                for uf, _d, skip in items
            ]
            self.plan_ready.emit(rows)
            agg = SessionProgress()
            t0 = time.monotonic()
            last_emit = [0.0]

            def on_prog(p: SessionProgress) -> None:
                now = time.monotonic()
                complete = p.current_total and p.current_done >= p.current_total
                if now - last_emit[0] < 0.1 and not complete:
                    return
                last_emit[0] = now
                done = agg.done_bytes + p.done_bytes + p.current_done
                rate = done / max(now - t0, 0.01) / 1e6
                self.file_progress.emit(
                    p.current_name, p.current_done, p.current_total,
                    agg.done_files + p.done_files,
                    agg.total_files + p.total_files, rate,
                )

            for _v, items in plans:
                agg.total_files += sum(1 for _u, _d, s in items if not s)
                agg.total_bytes += sum(u.size for u, _d, s in items if not s)
            for _v, items in plans:
                res = ingester.run(items, on_prog, lambda: self._cancel)
                agg.done_files += res.done_files
                agg.done_bytes += res.done_bytes
                agg.results.extend(res.results)
                agg.failures.extend(res.failures)
                if self._cancel:
                    break
            return agg, plans

        self.state_changed.emit("transferring", f"USB ingest from {len(vols)} volume(s)…")
        if self.settings.get("keep_awake", True):
            _keep_awake(True)
        t0 = time.monotonic()
        result, _plans = await asyncio.to_thread(blocking)
        secs = time.monotonic() - t0
        summary = {
            "mode": "transfer", "transport": "usb",
            "files": result.done_files, "files_total": result.total_files,
            "bytes": result.done_bytes, "seconds": secs,
            "rate_mbs": result.done_bytes / max(secs, 0.01) / 1e6,
            "failures": result.failures,
        }
        if result.results:
            extras = await asyncio.to_thread(
                self._post_transfer, result, cam_folder, cfg.base_dir, secs,
                summary["rate_mbs"],
            )
            summary.update(extras)
        self.state_changed.emit(
            "idle",
            f"USB ingest done: {result.done_files}/{result.total_files} files"
            if result.total_files else "USB: nothing new on the camera volumes",
        )
        self.session_done.emit(summary)

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

    async def _establish(self, state: dict, address: str | None):
        """conn.establish with one automatic retry for transient failures
        (AP no-show, association timeouts) — not for camera-not-found."""
        for attempt in (1, 2):
            try:
                return await conn.establish(
                    state, address, on_approval_needed=lambda: self.approval_needed.emit()
                )
            except conn.ConnectError as e:
                if attempt == 1 and "not found" not in str(e).lower():
                    self.log_line.emit("Connection hiccup — retrying once…")
                    log.warning("establish failed (%s) — retrying", e)
                    await asyncio.sleep(2.0)
                    continue
                raise

    async def _session(self, transfer: bool, selected: set | None = None) -> None:
        state = config.load_state()
        saved = state.get("cameras", {})
        address = next(iter(saved), None)

        self.state_changed.emit("connecting", "Scanning for camera…")
        link, creds, target = await self._establish(state, address)
        link.on_frame = self._on_ble_frame
        try:
            self.state_changed.emit("connecting", "Opening data session…")
            identifier = config.get_identifier(state)
            records, cam_status, answered = await asyncio.to_thread(
                self._fetch_records, identifier
            )
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
                if answered:
                    self.plan_ready.emit([])
                    self.state_changed.emit("connected", "Camera is empty — nothing to transfer")
                    self.session_done.emit(
                        {"mode": "transfer" if transfer else "refresh",
                         "files": 0, "bytes": 0, "failures": [], "empty_camera": True,
                         "new": 0, "total": 0}
                    )
                    return
                raise conn.ConnectError("Connected, but the camera never answered the media list.")

            cam_folder = (
                target.name or creds.ssid
                or f"{target.model_name}-{target.address[-5:].replace(':', '')}"
            ).replace(" ", "")
            kinds = ["other"]
            if self.settings.get("kinds_video", True):
                kinds.append("video")
            if self.settings.get("kinds_photo", True):
                kinds.append("photo")
            cfg = OffloadConfig(
                base_dir=Path(self.settings.get("base_dir", "D:/DJI-Offload")),
                camera_folder=cam_folder,
                template=self.settings.get("template", "{original}"),
                kinds=tuple(kinds),
                log_path=getattr(self, "current_log_path", None),
            )
            http = CameraHttp(conn.CAMERA_IP)
            off = Offloader(cfg, self.db, http)
            off.on_network_lost = lambda: wifi_rejoin(creds.ssid)

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
            if result.results:
                extras = await asyncio.to_thread(
                    self._post_transfer, result, cam_folder, cfg.base_dir, secs,
                    summary["rate_mbs"],
                )
                summary.update(extras)
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

    def _post_transfer(self, result: SessionProgress, cam_folder: str,
                       base_dir: Path, secs: float, rate_mbs: float) -> dict:
        """Shoot summary (local MP4 probe), session report, folder-open, hook."""
        import os
        import subprocess

        from ..core import mp4meta
        from ..core.report import ReportFile, SessionReport, write_reports

        files = []
        for r in result.results:
            meta = mp4meta.probe(Path(r["dest_path"])) if r["kind"] == "video" else None
            files.append(ReportFile(
                original=r["original"], saved_as=r["saved_as"], dest_path=r["dest_path"],
                size=r["size"], hash_hex=r["hash"], storage=r["storage"], kind=r["kind"],
                verified=r["verified"],
                duration_s=meta.duration_s if meta else None,
                resolution=meta.resolution if meta else None,
            ))
        rep = SessionReport(
            camera=cam_folder, started_at=time.time() - secs,
            seconds=secs, rate_mbs=rate_mbs, files=files,
        )
        extras: dict = {"summary_line": rep.summary_line()}

        if self.settings.get("write_reports", True):
            try:
                html_path, csv_path = write_reports(rep, base_dir / cam_folder / "_reports")
                extras["report"] = str(html_path)
                log.info("session report: %s", html_path)
            except Exception:
                log.exception("report writing failed")

        if self.settings.get("open_folder", False) and files:
            try:
                os.startfile(str(Path(files[0].dest_path).parent))
            except Exception as e:
                log.warning("open folder failed: %s", e)

        hook = (self.settings.get("hook_cmd") or "").strip()
        if hook:
            env = {
                **os.environ,
                "OSMO_CAMERA": cam_folder,
                "OSMO_COUNT": str(len(files)),
                "OSMO_BYTES": str(rep.total_bytes),
                "OSMO_DEST": str(base_dir / cam_folder),
                "OSMO_REPORT": extras.get("report", ""),
                "OSMO_FILES": os.pathsep.join(f.dest_path for f in files),
            }
            try:
                subprocess.Popen(hook, shell=True, env=env,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
                log.info("post-transfer hook launched: %s", hook)
                extras["hook_launched"] = True
            except Exception as e:
                log.warning("hook failed to launch: %s", e)
        return extras

    async def _delete_session(self) -> None:
        state = config.load_state()
        address = next(iter(state.get("cameras", {})), None)
        self.state_changed.emit("connecting", "Scanning for camera…")
        link, creds, target = await self._establish(state, address)
        link.on_frame = self._on_ble_frame
        try:
            self.state_changed.emit("connecting", "Opening data session…")
            identifier = config.get_identifier(state)
            cam_folder = (
                target.name or creds.ssid
                or f"{target.model_name}-{target.address[-5:].replace(':', '')}"
            ).replace(" ", "")
            summary = await asyncio.to_thread(self._delete_blocking, identifier, cam_folder)
            self.state_changed.emit(
                "connected",
                f"Freed {summary['freed'] / 1e6:.0f} MB — deleted "
                f"{summary['deleted']}/{summary['candidates']} offloaded file(s)"
                if summary["candidates"]
                else "Nothing on the camera is safe to delete yet",
            )
            self.session_done.emit(summary)
        finally:
            _keep_awake(False)
            await conn.teardown(link)
            self.state_changed.emit("idle", "Camera released (it will sleep)")

    def _delete_blocking(self, identifier: str, cam_folder: str) -> dict:
        """Open a fresh session, list, delete only verified-offloaded files
        (one at a time), then RE-LIST to confirm what is actually gone."""
        from ..core.offload import OffloadConfig, Offloader
        from pathlib import Path

        summary = {"mode": "delete", "candidates": 0, "deleted": 0, "freed": 0, "failures": []}
        for attempt, (port, poke) in enumerate([(9004, True), (9004, True)], 1):
            dl = CameraDatalink(conn.CAMERA_IP, port=port, tcp_poke=poke, identifier=identifier)
            try:
                if not dl.open():
                    continue
                dl.register()
                dl.enter_playback()
                records, _ = dl.query_newest_page()
                if not records:
                    if attempt == 1:
                        continue
                    summary["failures"].append("camera returned no media list")
                    return summary

                http = CameraHttp(conn.CAMERA_IP)
                cfg = OffloadConfig(
                    base_dir=Path(self.settings.get("base_dir", "D:/DJI-Offload")),
                    camera_folder=cam_folder,
                    template=self.settings.get("template", "{original}"),
                )
                cfg.log_path = getattr(self, "current_log_path", None)
                plan = Offloader(cfg, self.db, http).plan(records)
                http.close()
                del_session = self.db.start_session(
                    cam_folder, transport="delete", log_path=cfg.log_path
                )

                # eligibility: verified transfer on record + a unique non-zero handle
                handle_counts: dict[int, int] = {}
                for p in plan:
                    handle_counts[p.record.handle] = handle_counts.get(p.record.handle, 0) + 1
                todo = []
                for p in plan:
                    rec = p.record
                    if not rec.handle or handle_counts[rec.handle] != 1:
                        continue
                    if not p.size or not self.db.deletable(cam_folder, rec.media_path, p.size):
                        continue
                    todo.append(p)
                summary["candidates"] = len(todo)
                if not todo:
                    return summary

                for p in todo:  # one at a time — deliberate, per hardware guidance
                    status = dl.delete_files([p.record.handle])
                    if status not in (None, 0x0000):
                        summary["failures"].append(f"{p.record.name}: status 0x{status:04x}")

                # authoritative verification: what does the camera list NOW?
                remaining_paths = set()
                records_after, _ = dl.query_newest_page()
                remaining_paths = {r.media_path for r in records_after}
                for p in todo:
                    if p.record.media_path in remaining_paths:
                        if not any(p.record.name in f for f in summary["failures"]):
                            summary["failures"].append(f"{p.record.name}: still on camera")
                    else:
                        self.db.mark_deleted_from_camera(cam_folder, p.record.media_path)
                        summary["deleted"] += 1
                        summary["freed"] += p.size or 0
                self.db.finish_session(
                    del_session, summary["deleted"], summary["freed"],
                    note="delete: " + ("; ".join(summary["failures"][:3]) or "ok"),
                )
                return summary
            finally:
                dl.close()
            time.sleep(1.0)
        summary["failures"].append("could not open a data session")
        return summary

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
        """Returns (records, status, answered). answered=True with an empty
        list means the camera really is empty — not a dead session."""
        from ..camera import manifest as mf

        last_status = None
        for attempt, (port, poke) in enumerate([(9004, True), (10004, False), (9004, True)], 1):
            dl = CameraDatalink(conn.CAMERA_IP, port=port, tcp_poke=poke, identifier=identifier)
            try:
                if not dl.open():
                    continue
                dl.register()
                dl.enter_playback()
                records, raw = dl.query_newest_page()
                last_status = dl.status
                if records:
                    return records, dl.status, True
                if mf.list_answered(raw):
                    log.info("camera answered the list with zero records — it is empty")
                    return [], dl.status, True
                log.warning("list unanswered on attempt %d (port %d)", attempt, port)
            finally:
                dl.close()
            time.sleep(1.0)
        return [], last_status, False
