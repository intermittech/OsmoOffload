"""App bootstrap: theme, tray icon, window/tray mode, mock data mode."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import config
from . import theme
from .main_window import MainWindow


def make_icon() -> QIcon:
    """Programmatic app icon: accent ring on dark — replaced by real art later."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(theme.BG_CARD))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(4, 4, 56, 56)
    p.setBrush(Qt.BrushStyle.NoBrush)
    pen = p.pen()
    from PySide6.QtGui import QPen

    pen = QPen(QColor(theme.ACCENT), 7)
    p.setPen(pen)
    p.drawEllipse(12, 12, 40, 40)
    p.setPen(QPen(QColor(theme.OK), 7))
    p.drawArc(12, 12, 40, 40, 45 * 16, 120 * 16)
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
            "base_dir": self.state.get("base_dir", "D:/DJI-Offload"),
            "template": self.state.get("template", "{original}"),
            "tray_mode": self.state.get("tray_mode", True),
            "start_minimized": self.state.get("start_minimized", False),
            "auto_transfer": self.state.get("auto_transfer", False),
            "keep_awake": self.state.get("keep_awake", True),
        }

        self.icon = make_icon()
        self.window = MainWindow(self.settings)
        self.window.setWindowIcon(self.icon)
        self.window.settings_saved.connect(self._on_settings_saved)

        self.tray = QSystemTrayIcon(self.icon)
        menu = QMenu()
        act_open = QAction("Open Osmo Offload", menu)
        act_open.triggered.connect(self.show_window)
        act_transfer = QAction("Transfer new files", menu)
        act_transfer.triggered.connect(self.window.transfer_requested)
        act_quit = QAction("Quit", menu)
        act_quit.triggered.connect(self.qt.quit)
        menu.addAction(act_open)
        menu.addAction(act_transfer)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("Osmo Offload")
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        self.window.closeEvent = self._on_close  # close-to-tray in tray mode

        if mock:
            self._fill_mock()

    # -- behavior ------------------------------------------------------------

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
        self.window.status_line.setText("Settings saved")

    # -- mock ----------------------------------------------------------------

    def _fill_mock(self) -> None:
        w = self.window
        w.set_cameras([("EC:72:F7:C4:88:A7", "Osmo Pocket 4 Pro", True)])
        w.card.title.setText("Osmo Pocket 4 Pro")
        w.card.state.setText("in range")
        w.card.battery.set_state(86, -240)
        w.card.storage_internal.set_mib(524288, 198656)  # 512 GB, 194 free
        w.card.storage_sd.set_mib(491520, 398336)
        w.set_history_rows(
            [
                ("2026-08-20 13:02", "DJI_20260820125512_0004_D.MP4", "video", "3.98 GB",
                 "D:/DJI-Offload/OsmoPocket4P/2026-08-20/video", "yes"),
                ("2026-08-20 13:01", "DJI_20260820125101_0003_D.JPG", "photo", "8.1 MB",
                 "D:/DJI-Offload/OsmoPocket4P/2026-08-20/photo", "yes"),
            ]
        )
        w.status_line.setText("Mock mode — no camera I/O")

    def run(self) -> int:
        if not (self.settings.get("tray_mode") and self.settings.get("start_minimized")):
            self.window.show()
        return self.qt.exec()


def run_app(mock: bool = False) -> int:
    return OsmoApp(mock=mock).run()
