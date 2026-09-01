"""GUI <-> core bridge.

A dedicated worker thread runs an asyncio loop (BLE needs one). The controller
schedules one job at a time on it and reports back via Qt signals.

Connection policy: after an action the link is held WARM for WARM_HOLD_S so a
follow-up (refresh / transfer / delete) skips the whole scan+pair+join dance;
the hold then tears down so the camera can sleep. Every job records a history
SESSION row (action, files, bytes, speed, outcome) plus a per-session DEBUG
log and a formatted HTML report with the log folded in.
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
from ..camera.session import CameraDatalink, datalink_configs
from ..core import connect as conn
from ..core.offload import OffloadConfig, Offloader, SessionProgress
from ..history import HistoryDB
from ..net.downloader import CameraHttp
from ..net.wifi import rejoin_sync as wifi_rejoin

log = logging.getLogger("osmo.ui")

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

WARM_HOLD_S = 120.0  # keep the camera connection alive after an action
WARM_ESTIMATE_S = 8.0
DEFAULT_CONNECT_ESTIMATE_S = 40.0


def _keep_awake(on: bool) -> None:
    try:
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


class CameraController(QObject):
    state_changed = Signal(str, str)  # state key, human detail
    connect_started = Signal(float)  # estimated seconds (drives the countdown)
    camera_seen = Signal(bool)
    auto_started = Signal()
    status_updated = Signal(dict)
    plan_ready = Signal(list)  # [(name, size, note)]
    thumb_ready = Signal(str, str)
    file_progress = Signal(str, int, int, int, int, float)
    session_done = Signal(dict)
    approval_needed = Signal()
    error = Signal(str)
    log_line = Signal(str)

    def __init__(self, settings: dict, parent: QObject | None = None):
        super().__init__(parent)
        self.settings = settings
        # Which saved camera the user picked in the sidebar. Restored from
        # state so the startup auto-connect targets the same body as last time
        # instead of whichever camera happens to be first in the saved dict.
        self.active_address: str | None = config.load_state().get("active_camera")
        self._busy = False
        self._cancel = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_session_end = 0.0
        self._auto_backoff_until = 0.0
        self._warm: dict | None = None  # link/creds/target/expires
        self._in_connect = False
        self._almost_sent = False
        self._thread = threading.Thread(target=self._run_loop, name="osmo-worker", daemon=True)
        self._thread.start()
        self.db = HistoryDB(config.CONFIG_DIR / "history.sqlite3")
        while self._loop is None:
            time.sleep(0.01)
        asyncio.run_coroutine_threadsafe(self._watcher(), self._loop)
        asyncio.run_coroutine_threadsafe(self._warm_expiry_loop(), self._loop)

    # -- worker loop ---------------------------------------------------------

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro) -> None:
        if self._busy:
            self.log_line.emit("Busy with another camera session.")
            return
        self._busy = True
        self._cancel = False

        # every job gets its own DEBUG log file, linked from history
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.current_log_path = str(
            config.LOG_DIR / f"session-{time.strftime('%Y%m%d-%H%M%S')}.log"
        )
        handler = logging.FileHandler(self.current_log_path, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
        )
        self._log_handler = handler
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
                await self._drop_warm()
                self.state_changed.emit("error", str(e))
                self.error.emit(str(e))
            except Exception as e:
                log.exception("session job failed")
                await self._drop_warm()
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

    def shutdown(self) -> None:
        """App quit: release the camera synchronously-ish."""
        if self._loop and self._warm:
            asyncio.run_coroutine_threadsafe(self._drop_warm(), self._loop)
            time.sleep(0.4)

    @property
    def busy(self) -> bool:
        return self._busy

    # -- public actions ------------------------------------------------------

    def refresh(self) -> None:
        self._submit(self._session(transfer=False))

    def transfer(self) -> None:
        self._submit(self._session(transfer=True))

    def transfer_selected(self, names: list) -> None:
        self._submit(self._session(transfer=True, selected=set(names)))

    def delete_selected(self, names: list) -> None:
        """Delete exactly these files from the camera. The UI has already
        confirmed (twice, for never-transferred files)."""
        self._submit(self._delete_session(set(names)))

    def usb_ingest(self) -> None:
        self._submit(self._usb_session())

    # -- warm connection -----------------------------------------------------

    async def _warm_expiry_loop(self) -> None:
        while True:
            await asyncio.sleep(5.0)
            if self._warm and not self._busy and time.monotonic() > self._warm["expires"]:
                await self._drop_warm()
                self.state_changed.emit("idle", "Camera released (it will sleep)")

    async def _drop_warm(self) -> None:
        warm, self._warm = self._warm, None
        if warm:
            if warm.get("dl"):
                try:
                    await asyncio.to_thread(warm["dl"].close)
                except Exception:
                    pass
            try:
                await conn.teardown(warm["link"])
            except Exception:
                pass

    def _hold_warm(self, link, creds, target, dl=None) -> None:
        if dl is not None and dl.alive:
            dl.on_status = self._emit_status
            dl.start_keepalive()
        else:
            dl = None
        self._warm = {
            "link": link, "creds": creds, "target": target, "dl": dl,
            "expires": time.monotonic() + WARM_HOLD_S,
        }
        self.state_changed.emit(
            "connected", f"Ready — connection held for {WARM_HOLD_S / 60:.0f} min"
        )

    async def _acquire(self, state: dict):
        """Warm reuse when possible, else full establish. Returns
        (link, creds, target, warm_dl). Emits connect_started with the ETA."""
        self._almost_sent = False
        warm = self._warm
        if warm and self.active_address:
            held = (getattr(warm.get("target"), "address", "") or "").lower()
            if held and held != self.active_address.lower():
                log.info("held connection is %s but %s is selected — dropping it",
                         held, self.active_address)
                self._warm = None
                if warm.get("dl"):
                    try:
                        await asyncio.to_thread(warm["dl"].close)
                    except Exception:
                        pass
                try:
                    await conn.teardown(warm["link"])
                except Exception:
                    pass
                warm = None
        if warm and warm["link"].is_connected:
            self._warm = None  # in use; re-held on success
            self.connect_started.emit(WARM_ESTIMATE_S)
            self.state_changed.emit("connecting", "Reusing the held connection…")
            self._in_connect = True
            try:
                if await conn.rejoin_ap(warm["link"], warm["creds"]):
                    log.info("warm reuse: AP ready")
                    warm_dl = warm.get("dl")
                    if warm_dl is not None:
                        warm_dl.stop_keepalive()  # caller owns the socket now
                        warm_dl.on_status = None
                        if not warm_dl.alive:
                            await asyncio.to_thread(warm_dl.close)
                            warm_dl = None
                    return warm["link"], warm["creds"], warm["target"], warm_dl
            finally:
                self._in_connect = False
            log.info("warm reuse failed — falling back to full connect")
            if warm.get("dl"):
                try:
                    await asyncio.to_thread(warm["dl"].close)
                except Exception:
                    pass
            try:
                await conn.teardown(warm["link"])
            except Exception:
                pass
        elif warm:
            self._warm = None  # BLE dropped silently
            if warm.get("dl"):
                try:
                    await asyncio.to_thread(warm["dl"].close)
                except Exception:
                    pass

        est = float(state.get("connect_avg_s", DEFAULT_CONNECT_ESTIMATE_S))
        self.connect_started.emit(est)
        self._in_connect = True
        t0 = time.monotonic()
        try:
            link, creds, target = await self._establish(state)
        finally:
            self._in_connect = False
        dur = time.monotonic() - t0
        avg = float(state.get("connect_avg_s", dur))
        state["connect_avg_s"] = round(0.7 * avg + 0.3 * dur, 1)
        config.save_state(state)
        log.info("connect took %.1fs (rolling avg now %.1fs)", dur, state["connect_avg_s"])
        return link, creds, target, None

    async def _establish(self, state: dict):
        address = self.active_address or next(iter(state.get("cameras", {})), None)
        for attempt in (1, 2):
            try:
                return await conn.establish(
                    state, address,
                    on_approval_needed=lambda: self.approval_needed.emit(),
                    on_phase=lambda key, text: self.state_changed.emit("connecting", text),
                )
            except conn.ConnectError as e:
                if attempt == 1 and "not found" not in str(e).lower():
                    self.log_line.emit("Connection hiccup — retrying once…")
                    log.warning("establish failed (%s) — retrying", e)
                    await asyncio.sleep(2.0)
                    continue
                raise

    # -- auto-transfer watcher ----------------------------------------------

    AUTO_COOLDOWN_S = 15 * 60
    AUTO_ERROR_BACKOFF_S = 30 * 60
    WATCH_INTERVAL_S = 45.0

    async def _watcher(self) -> None:
        from ..ble import scanner as ble_scanner

        while True:
            await asyncio.sleep(self.WATCH_INTERVAL_S)
            if not self.settings.get("auto_transfer") or self._busy or self._warm:
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
                self._auto_backoff_until = time.monotonic() + self.AUTO_ERROR_BACKOFF_S

    # -- shared helpers ------------------------------------------------------

    def _on_ble_frame(self, frame) -> None:
        if frame.key == (0x0D, 0x02) and len(frame.payload) >= 21:
            cur = int.from_bytes(frame.payload[5:9], "little", signed=True)
            self.status_updated.emit({"battery_pct": frame.payload[20], "current_ma": cur})
            if self._in_connect and not self._almost_sent:
                self._almost_sent = True
                self.state_changed.emit("almost", "Almost connected…")

    def _emit_status(self, cam_status) -> None:
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

    def _cam_folder(self, target, creds) -> str:
        return (
            target.name or creds.ssid
            or f"{target.model_name}-{target.address[-5:].replace(':', '')}"
        ).replace(" ", "")

    def _make_cfg(self, cam_folder: str) -> OffloadConfig:
        kinds = ["other"]
        if self.settings.get("kinds_video", True):
            kinds.append("video")
        if self.settings.get("kinds_photo", True):
            kinds.append("photo")
        return OffloadConfig(
            base_dir=Path(self.settings.get("base_dir", r"D:\DJI-Offload")),
            camera_folder=cam_folder,
            template=self.settings.get("template", "{original}"),
            kinds=tuple(kinds),
            log_path=getattr(self, "current_log_path", None),
        )

    def _read_session_log(self) -> str:
        try:
            self._log_handler.flush()
            return Path(self.current_log_path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    def _finalize(self, session_id: int, action: str, cam_folder: str,
                  base_dir: Path, rep, summary: dict, outcome: str) -> None:
        """Write the session report (HTML with folded debug log + CSV), stamp
        the session row, run open-folder/hook for transfers."""
        import os
        import subprocess

        from ..core.report import write_reports

        report_path = None
        try:
            html_path, _csv = write_reports(
                rep, base_dir / cam_folder / "_reports", log_text=self._read_session_log()
            )
            report_path = str(html_path)
            summary["report"] = report_path
            log.info("session report: %s", report_path)
        except Exception:
            log.exception("report writing failed")

        self.db.finish_session(
            session_id,
            files_done=summary.get("files", len(rep.files)),
            bytes_done=summary.get("bytes", rep.total_bytes),
            note="; ".join(summary.get("failures", [])[:5]),
            seconds=rep.seconds, rate_mbs=rep.rate_mbs,
            outcome=outcome, report_path=report_path,
        )

        if action in ("transfer", "usb") and rep.files:
            if self.settings.get("open_folder", False):
                try:
                    os.startfile(str(Path(rep.files[0].dest_path).parent))
                except Exception as e:
                    log.warning("open folder failed: %s", e)
            hook = (self.settings.get("hook_cmd") or "").strip()
            if hook:
                env = {
                    **os.environ,
                    "OSMO_CAMERA": cam_folder,
                    "OSMO_COUNT": str(len(rep.files)),
                    "OSMO_BYTES": str(rep.total_bytes),
                    "OSMO_DEST": str(base_dir / cam_folder),
                    "OSMO_REPORT": report_path or "",
                    "OSMO_FILES": os.pathsep.join(f.dest_path for f in rep.files),
                }
                try:
                    subprocess.Popen(hook, shell=True, env=env,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
                    summary["hook_launched"] = True
                except Exception as e:
                    log.warning("hook failed to launch: %s", e)

    def _report_files_from_results(self, results: list) -> list:
        from ..core import mp4meta
        from ..core.report import ReportFile

        files = []
        for r in results:
            meta = mp4meta.probe(Path(r["dest_path"])) if r["kind"] == "video" else None
            files.append(ReportFile(
                original=r["original"], saved_as=r["saved_as"], dest_path=r["dest_path"],
                size=r["size"], hash_hex=r["hash"], storage=r["storage"], kind=r["kind"],
                verified=r["verified"],
                duration_s=meta.duration_s if meta else None,
                resolution=meta.resolution if meta else None,
                speed_mbs=r.get("speed_mbs"),
            ))
        return files

    def _emit_fresh_plan(self, off: Offloader, records) -> list:
        """Re-plan against the now-updated history and refresh the UI lists."""
        plan = off.plan(records)
        self.plan_ready.emit(
            [(p.record.name, p.size or 0, p.skipped or "queued") for p in plan]
        )
        return plan

    # -- WiFi session (refresh / transfer) -----------------------------------

    async def _session(self, transfer: bool, selected: set | None = None) -> None:
        from ..camera import manifest as mf
        from ..core.report import SessionReport

        state = config.load_state()
        link, creds, target, warm_dl = await self._acquire(state)
        link.on_frame = self._on_ble_frame
        cam_folder = self._cam_folder(target, creds)
        action = "transfer" if transfer else "refresh"
        session_id = self.db.start_session(
            cam_folder, transport="wifi", action=action,
            log_path=getattr(self, "current_log_path", None),
        )
        t_all = time.monotonic()
        dl = None
        try:
            identifier = config.get_identifier(state)
            records: list = []
            answered = False
            cam_status = None
            if warm_dl is not None:
                self.state_changed.emit("almost", "Reading the camera…")
                records, raw = await asyncio.to_thread(warm_dl.query_newest_page)
                answered = bool(records) or mf.list_answered(raw)
                if answered:
                    dl = warm_dl
                    cam_status = dl.status
                    log.info("warm datalink reused for the list")
                else:
                    await asyncio.to_thread(warm_dl.close)
            if dl is None:
                self.state_changed.emit("almost", "Opening data session…")
                records, cam_status, answered, dl = await asyncio.to_thread(
                    self._fetch_records_keep, identifier
                )
            self._emit_status(cam_status)
            cfg = self._make_cfg(cam_folder)

            if not records:
                if answered:
                    self.plan_ready.emit([])
                    rep = SessionReport(camera=cam_folder, started_at=time.time(),
                                        seconds=time.monotonic() - t_all, rate_mbs=0,
                                        action=action, notes=["camera is empty"])
                    summary = {"mode": action, "files": 0, "bytes": 0,
                               "failures": [], "empty_camera": True, "new": 0, "total": 0}
                    self._finalize(session_id, action, cam_folder, cfg.base_dir,
                                   rep, summary, outcome="empty")
                    self._hold_warm(link, creds, target, dl)
                    self.state_changed.emit("connected", "Camera is empty — nothing to transfer")
                    self.session_done.emit(summary)
                    return
                raise conn.ConnectError("Connected, but the camera never answered the media list.")

            http = CameraHttp(conn.CAMERA_IP)
            off = Offloader(cfg, self.db, http)
            off.on_network_lost = lambda: wifi_rejoin(creds.ssid)

            self.state_changed.emit("almost", "Planning…")
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

            if not transfer or not todo:
                secs = time.monotonic() - t_all
                from ..core.report import ReportFile

                rep = SessionReport(
                    camera=cam_folder, started_at=time.time() - secs, seconds=secs,
                    rate_mbs=0, action="refresh",
                    files=[ReportFile(p.record.name, "", "", p.size or 0, None,
                                      {0: "sd", 1: "internal", None: None}.get(p.record.storage),
                                      "video" if p.record.name.upper().endswith((".MP4", ".MOV")) else "photo",
                                      True, note=p.skipped or "new")
                           for p in plan],
                )
                summary = {"mode": action, "new": len(todo), "total": len(plan),
                           "files": 0, "bytes": 0, "failures": []}
                outcome = "ok"
                self._finalize(session_id, "refresh", cam_folder, cfg.base_dir,
                               rep, summary, outcome)
                http.close()
                self._hold_warm(link, creds, target, dl)
                self.state_changed.emit(
                    "connected",
                    f"{len(todo)} new of {len(plan)} on camera — connection held"
                    if not transfer else "Nothing new to transfer — connection held",
                )
                self.session_done.emit(summary)
                return

            self.state_changed.emit("transferring", f"{len(todo)} files…")
            if dl is not None:
                dl.start_keepalive()  # holds playback/AP through the HTTP phase
            if self.settings.get("keep_awake", True):
                _keep_awake(True)
            t0 = time.monotonic()
            last_emit = [0.0]

            def on_progress(prog: SessionProgress) -> None:
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
                off.run, plan, on_progress, lambda: self._cancel, session_id
            )
            secs = time.monotonic() - t0
            rate = result.done_bytes / max(secs, 0.01) / 1e6
            summary = {
                "mode": "transfer", "files": result.done_files,
                "files_total": result.total_files, "bytes": result.done_bytes,
                "seconds": secs, "rate_mbs": rate, "failures": result.failures,
            }
            rep = SessionReport(
                camera=cam_folder, started_at=time.time() - secs, seconds=secs,
                rate_mbs=rate, action="transfer",
                files=await asyncio.to_thread(self._report_files_from_results, result.results),
                notes=result.failures,
            )
            summary["summary_line"] = rep.summary_line()
            outcome = ("ok" if not result.failures else
                       "partial" if result.done_files else "failed")
            await asyncio.to_thread(
                self._finalize, session_id, "transfer", cam_folder, cfg.base_dir,
                rep, summary, outcome,
            )
            # post-action refresh so the UI never shows stale notes
            await asyncio.to_thread(self._emit_fresh_plan, off, records)
            http.close()
            if not result.failures:
                self._auto_backoff_until = 0.0
            self._hold_warm(link, creds, target, dl)
            self.state_changed.emit(
                "connected",
                f"Done: {result.done_files}/{result.total_files} files "
                f"({result.done_bytes / 1e6:.0f} MB, {rate:.0f} MB/s) — connection held",
            )
            self.session_done.emit(summary)
        except BaseException:
            await self._drop_warm_link(link, dl)
            raise
        finally:
            _keep_awake(False)

    async def _drop_warm_link(self, link, dl=None) -> None:
        self._warm = None
        if dl is not None:
            try:
                await asyncio.to_thread(dl.close)
            except Exception:
                pass
        try:
            await conn.teardown(link)
        except Exception:
            pass
        self.state_changed.emit("idle", "Camera released (it will sleep)")

    # -- delete session ------------------------------------------------------

    async def _delete_session(self, names: set) -> None:
        from ..core.report import ReportFile, SessionReport

        state = config.load_state()
        link, creds, target, warm_dl = await self._acquire(state)
        link.on_frame = self._on_ble_frame
        cam_folder = self._cam_folder(target, creds)
        session_id = self.db.start_session(
            cam_folder, transport="wifi", action="delete",
            log_path=getattr(self, "current_log_path", None),
        )
        t0 = time.monotonic()
        try:
            if warm_dl is not None:
                # deletes are WRITES: aged sessions silently drop them, so
                # always run the delete on a fresh datalink session
                await asyncio.to_thread(warm_dl.close)
            self.state_changed.emit("almost", "Opening data session…")
            identifier = config.get_identifier(state)
            summary, deleted_files, records_after, cam_status = await asyncio.to_thread(
                self._delete_blocking, identifier, cam_folder, names
            )
            self._emit_status(cam_status)
            secs = time.monotonic() - t0
            cfg = self._make_cfg(cam_folder)
            rep = SessionReport(
                camera=cam_folder, started_at=time.time() - secs, seconds=secs,
                rate_mbs=0, action="delete",
                files=[ReportFile(name, "", "", size, None, store, kind, True,
                                  note="deleted from camera")
                       for name, size, store, kind in deleted_files],
                notes=summary["failures"],
            )
            outcome = ("ok" if summary["deleted"] and not summary["failures"] else
                       "partial" if summary["deleted"] else
                       "empty" if not summary["candidates"] else "failed")
            await asyncio.to_thread(
                self._finalize, session_id, "delete", cam_folder, cfg.base_dir,
                rep, summary, outcome,
            )
            # post-action refresh from the verification re-list
            if records_after is not None:
                http = CameraHttp(conn.CAMERA_IP)
                off = Offloader(cfg, self.db, http)
                await asyncio.to_thread(self._emit_fresh_plan, off, records_after)
                http.close()
            self._hold_warm(link, creds, target)
            self.state_changed.emit(
                "connected",
                (f"Freed {summary['freed'] / 1e6:.0f} MB — deleted "
                 f"{summary['deleted']}/{summary['candidates']} file(s) — connection held")
                if summary["candidates"]
                else "No selected files were deletable — connection held",
            )
            self.session_done.emit(summary)
        except BaseException:
            await self._drop_warm_link(link)
            raise
        finally:
            _keep_awake(False)

    def _active_cam(self) -> dict:
        cams = config.load_state().get("cameras", {})
        if self.active_address:
            for addr, cam in cams.items():
                if addr.lower() == self.active_address.lower():
                    return cam
        return next(iter(cams.values()), {})

    def _model_id(self) -> int | None:
        return self._active_cam().get("model_id")

    def _datalink_configs(self) -> list[tuple[int, bool]]:
        """(port, tcp_poke) candidates for the saved camera, most likely first.

        Keyed off the BLE model id, and the last transport that actually worked
        is remembered so a body that needs the alternate pays the probe once."""
        cam = self._active_cam()
        configs = datalink_configs(cam.get("model_id"))
        last = cam.get("datalink_port")
        if last is not None:
            configs.sort(key=lambda c: c[0] != last)
        return configs

    def _remember_datalink(self, port: int, poke: bool) -> None:
        state = config.load_state()
        address = self.active_address or next(iter(state.get("cameras", {})), None)
        if address:
            config.remember_camera(state, address, datalink_port=port,
                                   datalink_poke=poke)

    def _fetch_records_keep(self, identifier: str):
        """Open a datalink, register, list. On success the session is KEPT
        OPEN and returned so it can be held warm. Returns
        (records, status, answered, dl|None). Transport is chosen per body —
        an Xtra Edge Pro / Action 5 Pro answers on udp/10004 only, so a
        9004-only ladder would never reach it."""
        from ..camera import manifest as mf

        last_status = None
        candidates = [c for c in self._datalink_configs() for _ in (1, 2, 3)]
        for attempt, (port, poke) in enumerate(candidates, start=1):
            dl = CameraDatalink(conn.CAMERA_IP, port=port, tcp_poke=poke,
                                identifier=identifier, model_id=self._model_id())
            ok = False
            try:
                if not dl.open():
                    continue
                dl.register()
                dl.enter_playback()
                records, raw = dl.query_newest_page()
                last_status = dl.status
                if records or mf.list_answered(raw):
                    ok = True
                    self._remember_datalink(port, poke)
                    return records, dl.status, True, dl
                log.warning("list unanswered on attempt %d", attempt)
            finally:
                if not ok:
                    dl.close()
            time.sleep(0.8)
        return [], last_status, False, None

    # Writes stop being answered on a session older than ~40-70 s (hardware
    # finding) — refresh the datalink before that window closes.
    DELETE_SESSION_MAX_AGE_S = 28.0

    @staticmethod
    def _seq_of(name: str) -> int | None:
        import re

        m = re.search(r"_(\d{4})(?:_D)?[._]", name)
        return int(m.group(1)) if m else None

    def _derive_missing_handles(self, plan) -> dict:
        """Fit handle = base + seq*step per store from the records that DO
        expose a handle, then derive handles for records that don't (some
        stills). Returns {name: handle}. Only fits when >=2 known handles in a
        store agree on a step and the fit reproduces EVERY known handle
        exactly — so a bad guess can't be produced silently. Each derived
        handle is still verified by re-list before the next delete (caller),
        so this is a candidate, never a trusted value."""
        from collections import defaultdict

        by_store: dict = defaultdict(list)
        for p in plan:
            r = p.record
            s = self._seq_of(r.name)
            if r.handle and s is not None:
                by_store[r.storage].append((s, r.handle))

        fits: dict = {}
        for store, pairs in by_store.items():
            pairs = sorted(set(pairs))
            # >=3 collinear points is a real signal; 2 points always "fit" a
            # line, so requiring 3 keeps a wrong step from being manufactured
            if len(pairs) < 3:
                continue
            (s0, h0), (s1, h1) = pairs[0], pairs[-1]
            if s1 == s0:
                continue
            step, rem = divmod(h1 - h0, s1 - s0)
            if rem != 0 or step <= 0:
                continue
            base = h0 - s0 * step
            if all(base + s * step == h for s, h in pairs):
                fits[store] = (base, step)

        known_handles = {p.record.handle for p in plan if p.record.handle}
        out: dict = {}
        for p in plan:
            r = p.record
            if r.handle:
                continue
            s = self._seq_of(r.name)
            fit = fits.get(r.storage)
            if s is not None and fit:
                h = fit[0] + s * fit[1]
                if h not in known_handles:  # never collide with a known file
                    out[r.name] = h
        return out

    def _fresh_delete_dl(self, identifier: str):
        for port, poke in [c for c in self._datalink_configs() for _ in (1, 2)]:
            dl = CameraDatalink(conn.CAMERA_IP, port=port, tcp_poke=poke,
                                identifier=identifier, model_id=self._model_id())
            if dl.open():
                dl.register()
                dl.enter_playback()
                return dl, time.monotonic()
            dl.close()
            time.sleep(0.8)
        return None, 0.0

    def _delete_blocking(self, identifier: str, cam_folder: str, names: set):
        """Fresh datalink session: list, delete the SELECTED files one at a
        time (re-opening the session before the write window ages out), then
        re-list to confirm. Returns (summary, deleted_files, records_after,
        status)."""
        summary = {"mode": "delete", "candidates": 0, "deleted": 0, "freed": 0, "failures": []}
        deleted_files: list[tuple] = []
        dl, opened_at = self._fresh_delete_dl(identifier)
        if dl is None:
            summary["failures"].append("could not open a data session")
            return summary, deleted_files, None, None
        try:
            from ..camera import manifest as mf

            records = []
            for attempt in (1, 2, 3):
                records, raw = dl.query_newest_page()
                if records or mf.list_answered(raw):
                    break
                # stale session (camera still holding the previous one):
                # reopen fresh and try again
                log.warning("delete: list unanswered (attempt %d) — reopening", attempt)
                dl.close()
                time.sleep(0.8)
                dl, opened_at = self._fresh_delete_dl(identifier)
                if dl is None:
                    summary["failures"].append("could not open a data session")
                    return summary, deleted_files, None, None
            if not records:
                summary["failures"].append("camera returned no media list")
                return summary, deleted_files, None, dl.status

            http = CameraHttp(conn.CAMERA_IP)
            cfg = self._make_cfg(cam_folder)
            plan = Offloader(cfg, self.db, http).plan(records)
            http.close()

            handle_counts: dict[int, int] = {}
            for p in plan:
                if p.record.handle:
                    handle_counts[p.record.handle] = handle_counts.get(p.record.handle, 0) + 1
            derived = self._derive_missing_handles(plan)
            todo = []  # (plan_item, handle, is_derived)
            for p in plan:
                rec = p.record
                if rec.name not in names:
                    continue
                if rec.handle and handle_counts[rec.handle] == 1:
                    todo.append((p, rec.handle, False))
                elif rec.name in derived:
                    todo.append((p, derived[rec.name], True))
                else:
                    summary["failures"].append(f"{rec.name}: no safe delete handle")
            summary["candidates"] = len(todo)
            if not todo:
                return summary, deleted_files, records, dl.status

            # direct handles first, derived (verify-each) last
            todo.sort(key=lambda t: t[2])
            record_ok: list = []
            aborted = False
            for p, handle, is_derived in todo:
                if aborted:
                    summary["failures"].append(f"{p.record.name}: skipped (aborted)")
                    continue
                if time.monotonic() - opened_at > self.DELETE_SESSION_MAX_AGE_S:
                    log.info("delete session aged %.0fs — refreshing before next write",
                             time.monotonic() - opened_at)
                    dl.close()
                    dl, opened_at = self._fresh_delete_dl(identifier)
                    if dl is None:
                        summary["failures"].append("session refresh failed mid-delete")
                        return summary, deleted_files, None, None
                if is_derived:
                    # a derived handle is a calculated guess: verify by re-list
                    # BEFORE touching the next file; abort all on any surprise
                    before = {r.media_path for r in dl.query_newest_page()[0]}
                    dl.delete_files([handle])
                    after = {r.media_path for r in dl.query_newest_page()[0]}
                    gone = before - after
                    if gone == {p.record.media_path}:
                        record_ok.append(p)
                    elif not gone:
                        summary["failures"].append(f"{p.record.name}: derived handle rejected")
                    else:
                        summary["failures"].append(
                            f"ABORT: derived handle removed {sorted(gone)} instead of "
                            f"{p.record.media_path} — stopping all deletes"
                        )
                        log.error("derived-handle delete removed unexpected file(s): %s", gone)
                        aborted = True
                else:
                    status = dl.delete_files([handle])
                    if status not in (None, 0x0000):
                        summary["failures"].append(f"{p.record.name}: status 0x{status:04x}")

            records_after, _ = dl.query_newest_page()
            remaining = {r.media_path for r in records_after}
            for p, _h, is_derived in todo:
                if p.record.media_path in remaining:
                    if not any(p.record.name in f for f in summary["failures"]):
                        summary["failures"].append(f"{p.record.name}: still on camera")
                else:
                    self.db.mark_deleted_from_camera(cam_folder, p.record.media_path)
                    summary["deleted"] += 1
                    summary["freed"] += p.size or 0
                    deleted_files.append((
                        p.record.name, p.size or 0,
                        {0: "sd", 1: "internal", None: None}.get(p.record.storage),
                        "video" if p.record.name.upper().endswith((".MP4", ".MOV")) else "photo",
                    ))
            return summary, deleted_files, records_after, dl.status
        finally:
            if dl is not None:
                dl.close()

    # -- USB session ---------------------------------------------------------

    async def _usb_session(self) -> None:
        from ..core.report import SessionReport
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
        cfg = self._make_cfg(cam_folder)
        session_id = self.db.start_session(
            cam_folder, transport="usb", action="usb",
            log_path=getattr(self, "current_log_path", None),
        )

        def blocking() -> SessionProgress:
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
                res = ingester.run(items, on_prog, lambda: self._cancel, session_id)
                agg.done_files += res.done_files
                agg.done_bytes += res.done_bytes
                agg.results.extend(res.results)
                agg.failures.extend(res.failures)
                if self._cancel:
                    break
            return agg

        self.state_changed.emit("transferring", f"USB ingest from {len(vols)} volume(s)…")
        if self.settings.get("keep_awake", True):
            _keep_awake(True)
        try:
            t0 = time.monotonic()
            result = await asyncio.to_thread(blocking)
            secs = time.monotonic() - t0
            rate = result.done_bytes / max(secs, 0.01) / 1e6
            summary = {
                "mode": "transfer", "transport": "usb",
                "files": result.done_files, "files_total": result.total_files,
                "bytes": result.done_bytes, "seconds": secs,
                "rate_mbs": rate, "failures": result.failures,
            }
            rep = SessionReport(
                camera=cam_folder, started_at=time.time() - secs, seconds=secs,
                rate_mbs=rate, action="usb",
                files=await asyncio.to_thread(self._report_files_from_results, result.results),
                notes=result.failures,
            )
            summary["summary_line"] = rep.summary_line()
            outcome = ("ok" if not result.failures else
                       "partial" if result.done_files else "failed")
            await asyncio.to_thread(
                self._finalize, session_id, "usb", cam_folder, cfg.base_dir,
                rep, summary, outcome,
            )
            self.session_done.emit(summary)
            self.state_changed.emit(
                "idle",
                f"USB ingest done: {result.done_files}/{result.total_files} files"
                if result.total_files else "USB: nothing new on the camera volumes",
            )
        finally:
            _keep_awake(False)

    # -- blocking helpers ----------------------------------------------------

    def _fetch_thumbs(self, http: CameraHttp, plan, cam_folder: str) -> None:
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
            for cand in (rec.thumb_path + ".scr", rec.thumb_path + ".thm", rec.thumb_path):
                data = http.fetch_small(item.storage_idx, cand)
                if data and data[:2] == b"\xff\xd8":
                    cache.write_bytes(data)
                    self.thumb_ready.emit(rec.name, str(cache))
                    break

