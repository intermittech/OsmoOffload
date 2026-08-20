"""App bootstrap: theme, tray, controller wiring, toasts, mock mode."""

from __future__ import annotations

import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import config
from . import theme
from .main_window import MainWindow, human_size


def make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(theme.BG_CARD))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(4, 4, 56, 56)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(theme.ACCENT_DIM), 7))
    p.drawEllipse(12, 12, 40, 40)
    p.setPen(QPen(QColor(theme.ACCENT_HOVER), 7))
    p.drawArc(12, 12, 40, 40, 30 * 16, 150 * 16)
    p.end()
    return QIcon(pm)


class OsmoApp:
    def __init__(self, mock: bool = False):
        self.qt = QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setStyleSheet(theme.QSS)
        self.qt.setApplicationName("Osmo Offload")

        self.state = config.load_state()
        self.settings = {
            "base_dir": self.state.get("base_dir", r"D:\DJI-Offload"),
            "template": self.state.get("template", "{original}"),
            "tray_mode": self.state.get("tray_mode", True),
            "start_minimized": self.state.get("start_minimized", False),
            "auto_transfer": self.state.get("auto_transfer", False),
            "keep_awake": self.state.get("keep_awake", True),
            "kinds_video": self.state.get("kinds_video", True),
            "kinds_photo": self.state.get("kinds_photo", True),
            "write_reports": self.state.get("write_reports", True),
            "open_folder": self.state.get("open_folder", False),
            "hook_cmd": self.state.get("hook_cmd", ""),
            "update_repo": self.state.get("update_repo") or "intermittech/OsmoOffload",
        }

        self.icon = make_icon()
        self.window = MainWindow(self.settings)
        self.window.setWindowIcon(self.icon)
        self.window.settings_saved.connect(self._on_settings_saved)

        self.tray = QSystemTrayIcon(self.icon)
        menu = QMenu()
        act_open = QAction("Open Osmo Offload", menu)
        act_open.triggered.connect(self.show_window)
        self.act_transfer = QAction("Transfer new files", menu)
        act_quit = QAction("Quit", menu)
        act_quit.triggered.connect(self.qt.quit)
        menu.addAction(act_open)
        menu.addAction(self.act_transfer)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("Osmo Offload")
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        self.window.closeEvent = self._on_close

        self.controller = None
        if mock:
            self._fill_mock()
        else:
            self._wire_controller()
            self._maybe_wizard()
            QTimer.singleShot(6000, self._check_update_quiet)
            # opening the app interactively = the user wants to see the camera:
            # connect right away (not when boot-started minimized to tray)
            if not (self.settings.get("tray_mode") and self.settings.get("start_minimized")):
                if self.state.get("cameras"):
                    QTimer.singleShot(900, self._auto_open_refresh)

    def _auto_open_refresh(self) -> None:
        if self.controller and not self.controller.busy:
            self.window.status_line.setText("Connecting to the camera…")
            self.controller.refresh()

    # -- controller wiring ---------------------------------------------------

    def _wire_controller(self) -> None:
        from .controller import CameraController  # deferred: pulls in bleak

        c = CameraController(self.settings)
        self.controller = c
        w = self.window

        w.transfer_requested.connect(c.transfer)
        w.transfer_selected_requested.connect(c.transfer_selected)
        w.delete_offloaded_requested.connect(self._confirm_delete)
        w.card.usb_clicked.connect(c.usb_ingest)
        w.settings_tab.check_update.connect(self._check_update_loud)
        w.refresh_requested.connect(c.refresh)
        w.cancel_requested.connect(c.cancel)
        self.act_transfer.triggered.connect(c.transfer)

        c.state_changed.connect(self._on_state)
        c.connect_started.connect(self._on_connect_started)
        c.camera_seen.connect(self._on_camera_seen)
        self.qt.aboutToQuit.connect(c.shutdown)
        c.auto_started.connect(
            lambda: self._toast("Osmo Offload", "Camera detected — transferring new files.")
        )
        c.status_updated.connect(self._on_status)
        c.plan_ready.connect(w.set_plan)
        c.thumb_ready.connect(w.media.set_thumb)
        c.file_progress.connect(self._on_file_progress)
        c.session_done.connect(self._on_session_done)
        c.approval_needed.connect(self._on_approval_needed)
        c.error.connect(lambda msg: self._toast("Osmo Offload", msg, error=True))
        c.log_line.connect(w.status_line.setText)

        saved = self.state.get("cameras", {})
        w.set_cameras(
            [(addr, cam.get("model_name") or cam.get("ssid") or addr, False)
             for addr, cam in saved.items()]
        )
        if saved:
            first = next(iter(saved.values()))
            w.card.title.setText(first.get("model_name") or first.get("ssid") or "Camera")
        self._reload_history()

    def _on_state(self, state: str, detail: str) -> None:
        w = self.window
        pill_text = {
            "idle": "idle", "connecting": "connecting…",
            "almost": "almost connected", "connected": "connected",
            "transferring": "transferring", "error": "error",
        }.get(state, state)
        pill_state = "connecting" if state == "almost" else state
        w.card.pill.set_state(pill_state, pill_text)
        self._conn_phase = detail if state in ("connecting", "almost") else ""
        if state not in ("connecting", "almost"):
            self._stop_countdown()
            w.status_line.setText(detail)
        busy = state in ("connecting", "almost", "transferring")
        w.card.set_busy(busy, transferring=(state == "transferring"))
        w.media.set_busy(busy)
        # camera list dot: in-range while a session is live
        saved = self.state.get("cameras", {})
        w.set_cameras(
            [(addr, cam.get("model_name") or cam.get("ssid") or addr,
              state in ("connecting", "connected", "transferring"))
             for addr, cam in saved.items()]
        )

    # -- connecting countdown ------------------------------------------------

    def _on_connect_started(self, estimate_s: float) -> None:
        import time as _time

        self._conn_est = max(estimate_s, 3.0)
        self._conn_t0 = _time.monotonic()
        if not hasattr(self, "_conn_timer"):
            self._conn_timer = QTimer()
            self._conn_timer.setInterval(1000)
            self._conn_timer.timeout.connect(self._tick_countdown)
        self._conn_timer.start()
        self._tick_countdown()

    def _tick_countdown(self) -> None:
        import time as _time

        elapsed = _time.monotonic() - getattr(self, "_conn_t0", _time.monotonic())
        remaining = self._conn_est - elapsed
        phase = getattr(self, "_conn_phase", "") or "Connecting…"
        if remaining > 0:
            text = f"{phase}  ·  usually ~{self._conn_est:.0f}s — about {remaining:.0f}s left"
        else:
            text = f"{phase}  ·  taking a little longer than usual ({elapsed:.0f}s)"
        self.window.status_line.setText(text)

    def _stop_countdown(self) -> None:
        if hasattr(self, "_conn_timer"):
            self._conn_timer.stop()

    def _on_camera_seen(self, present: bool) -> None:
        saved = self.state.get("cameras", {})
        self.window.set_cameras(
            [(addr, cam.get("model_name") or cam.get("ssid") or addr, present)
             for addr, cam in saved.items()]
        )

    def _on_status(self, st: dict) -> None:
        card = self.window.card
        if "battery_pct" in st and st["battery_pct"] is not None:
            card.battery.set_state(st.get("battery_pct"), st.get("current_ma"))
        if "internal_total_mib" in st:
            card.storage_internal.set_mib(st.get("internal_total_mib"), st.get("internal_free_mib"))
        if "sd_total_mib" in st:
            if st.get("sd_total_mib"):
                card.storage_sd.set_mib(st.get("sd_total_mib"), st.get("sd_free_mib"))
            else:
                card.storage_sd.set_absent("no card")

    def _on_file_progress(self, name: str, done: int, total: int,
                          fdone: int, ftotal: int, rate: float) -> None:
        self.window.update_file_progress(name, done, total, "downloading")
        self.window.card.pill.set_state("transferring", f"transferring · {rate:.0f} MB/s")
        pct = 100 * done / total if total else 0
        self.window.status_line.setText(
            f"{name} — {pct:.0f}% · file {fdone + 1}/{ftotal} · {rate:.1f} MB/s"
        )
        if done >= total and total:
            self.window.update_file_progress(name, done, total, "done")

    def _maybe_wizard(self) -> None:
        if self.state.get("wizard_done") or self.state.get("cameras"):
            return
        from .wizard import FirstRunWizard

        wiz = FirstRunWizard(self.settings)
        wiz.setWindowIcon(self.icon)
        wiz.pair_requested.connect(lambda: self.controller and self.controller.refresh())
        wiz.exec()
        self.state.update(
            base_dir=self.settings.get("base_dir"), wizard_done=True
        )
        config.save_state(self.state)

    # -- update check --------------------------------------------------------

    def _run_update_check(self, loud: bool) -> None:
        import threading

        from .. import __version__, update

        repo = (self.settings.get("update_repo") or "").strip()
        if not repo:
            if loud:
                self._toast("Updates", "Set a GitHub repo (owner/repo) in Settings first.")
            return

        def work() -> None:
            info = update.check(__version__, repo)

            def apply() -> None:
                if info:
                    self.window.status_line.setText(
                        f"Update available: v{info.latest} — {info.url}"
                    )
                    self._toast("Update available",
                                f"Osmo Offload v{info.latest} is out. Opening: {info.url}"
                                if loud else f"Osmo Offload v{info.latest} is available — see Settings.")
                    if loud:
                        import webbrowser

                        webbrowser.open(info.url)
                elif loud:
                    self._toast("Updates", f"You're on the latest version (v{__version__}).")

            QTimer.singleShot(0, apply)  # hop back to the GUI thread

        threading.Thread(target=work, daemon=True).start()

    def _check_update_quiet(self) -> None:
        self._run_update_check(loud=False)

    def _check_update_loud(self) -> None:
        # pick up an unsaved repo field so "Check now" works immediately
        self.settings["update_repo"] = self.window.settings_tab.update_repo.text().strip()
        self._run_update_check(loud=True)

    def _confirm_delete(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        names = self.window.media.checked_names()
        if not names:
            return
        unverified = self.window.media.unverified_checked_names()

        box = QMessageBox(self.window)
        box.setWindowTitle("Delete files on camera")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"Delete the {len(names)} selected file(s) from the camera?")
        box.setInformativeText("This cannot be undone on the camera.")
        box.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        if unverified:
            # second, louder gate: these files exist NOWHERE else
            box2 = QMessageBox(self.window)
            box2.setWindowTitle("Files not in the destination folder!")
            box2.setIcon(QMessageBox.Icon.Critical)
            box2.setText(
                f"{len(unverified)} of the selected file(s) have no copy at the "
                "current destination folder."
            )
            listing = "\n".join(unverified[:8]) + ("\n…" if len(unverified) > 8 else "")
            box2.setInformativeText(
                f"If they were never transferred anywhere, deleting them destroys "
                f"the only copy:\n\n{listing}\n\nDelete them anyway?"
            )
            box2.setStandardButtons(
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes
            )
            box2.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if box2.exec() != QMessageBox.StandardButton.Yes:
                return

        if self.controller:
            self.controller.delete_selected(names)

    def _on_session_done(self, summary: dict) -> None:
        self._reload_history()
        if summary.get("mode") == "delete":
            if summary.get("deleted"):
                self._toast(
                    "Camera freed up",
                    f"Deleted {summary['deleted']} file(s), "
                    f"{human_size(summary.get('freed', 0))} freed on the camera.",
                )
            elif summary.get("failures"):
                self._toast("Free up camera", "; ".join(summary["failures"][:3]), error=True)
            else:
                self._toast("Free up camera", "Nothing on the camera is safe to delete yet.")
            return
        if summary.get("mode") == "transfer":
            files = summary.get("files", 0)
            if files:
                self._toast(
                    "Transfer complete",
                    summary.get("summary_line")
                    or f"{files} file(s), {human_size(summary.get('bytes', 0))} "
                       f"at {summary.get('rate_mbs', 0):.0f} MB/s",
                )
                if summary.get("summary_line"):
                    self.window.status_line.setText("Done — " + summary["summary_line"])
            elif not summary.get("failures"):
                self._toast("Osmo Offload", "Nothing new to transfer.")
            if summary.get("failures"):
                self._toast("Transfer finished with failures",
                            "; ".join(summary["failures"][:3]), error=True)

    def _on_approval_needed(self) -> None:
        self.show_window()
        self.window.status_line.setText("Approve the pairing prompt on the camera screen (reads OSMO)")
        self._toast("Camera pairing", "Tap approve on the camera screen.")

    def _reload_history(self) -> None:
        if not self.controller:
            return
        action_labels = {"transfer": "Transfer", "refresh": "Refresh",
                         "delete": "Delete", "usb": "USB ingest"}
        outcome_labels = {"ok": "✔ ok", "partial": "△ partial",
                          "failed": "✘ failed", "empty": "— nothing to do"}
        rows = []
        for s in self.controller.db.recent_sessions(limit=300):
            when = time.strftime(
                "%Y-%m-%d %H:%M",
                time.localtime(s.finished_at or s.started_at),
            )
            speed = f"{s.rate_mbs:.0f} MB/s" if s.rate_mbs else "—"
            result = outcome_labels.get(s.outcome or "", s.outcome or "…")
            if s.note:
                result_tip = s.note
            else:
                result_tip = ""
            rows.append((
                when,
                action_labels.get(s.action, s.action),
                str(s.files_done) if s.files_done else "—",
                human_size(s.bytes_done) if s.bytes_done else "—",
                speed,
                result,
                s.report_path or s.log_path,
                result_tip,
            ))
        self.window.set_history_rows(rows)

    def _toast(self, title: str, msg: str, error: bool = False) -> None:
        icon = QSystemTrayIcon.MessageIcon.Critical if error else QSystemTrayIcon.MessageIcon.Information
        self.tray.showMessage(title, msg, icon, 4000)

    # -- window/tray behavior ------------------------------------------------

    def _on_close(self, event) -> None:
        if self.settings.get("tray_mode", True):
            event.ignore()
            self.window.hide()
            self.tray.showMessage(
                "Osmo Offload", "Still running in the tray.",
                QSystemTrayIcon.MessageIcon.Information, 2500,
            )
        else:
            event.accept()
            self.qt.quit()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_window()

    def show_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _on_settings_saved(self, settings: dict) -> None:
        self.settings.update(settings)
        self.state.update(settings)
        config.save_state(self.state)
        if self.controller:
            self.controller.settings = self.settings
        self.window.status_line.setText("Settings saved")

    # -- mock ----------------------------------------------------------------

    def _fill_mock(self) -> None:
        w = self.window
        w.set_cameras([("EC:72:F7:C4:88:A7", "Osmo Pocket 4 Pro", True)])
        w.card.title.setText("Osmo Pocket 4 Pro")
        w.card.pill.set_state("connected", "connected")
        w.card.battery.set_state(44, -240)
        w.card.storage_internal.set_mib(105510, 42000)
        w.card.storage_sd.set_mib(121659, 98410)
        w.set_plan(
            [
                ("DJI_20260820125927_0001_D.MP4", 252541325, "already on disk"),
                ("DJI_20260820143000_0002_D.MP4", 1934567890, "queued"),
                ("DJI_20260820143120_0003_D.JPG", 8123456, "queued"),
            ]
        )
        w.update_file_progress("DJI_20260820143000_0002_D.MP4", 1350000000, 1934567890, "downloading")
        w.set_history_rows(
            [
                ("2026-08-20 13:28", "Transfer", "5", "6.12 GB", "62 MB/s", "✔ ok",
                 None, ""),
                ("2026-08-20 13:12", "Refresh", "—", "—", "—", "✔ ok", None, ""),
                ("2026-08-20 12:55", "Delete", "2", "1.75 GB", "—", "✔ ok", None, ""),
            ]
        )
        w.status_line.setText("Mock mode — no camera I/O")

    def run(self, screenshot: str | None = None) -> int:
        if screenshot:
            self.window.show()

            def grab() -> None:
                self.window.grab().save(screenshot)
                self.qt.quit()

            QTimer.singleShot(1500, grab)
        elif not (self.settings.get("tray_mode") and self.settings.get("start_minimized")):
            self.window.show()
        return self.qt.exec()


def run_app(mock: bool = False, screenshot: str | None = None) -> int:
    return OsmoApp(mock=mock).run(screenshot=screenshot)
