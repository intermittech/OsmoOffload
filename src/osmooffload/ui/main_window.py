"""Main window: camera rail, status card (battery + the two storage bars),
transfer button, and the Queue / History / Settings tabs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from .widgets import BatteryRow, StorageBar


class CameraCard(QWidget):
    transfer_clicked = Signal()
    refresh_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        self.title = QLabel("No camera")
        self.title.setObjectName("title")
        self.state = QLabel("offline")
        self.state.setObjectName("dim")
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.state)
        root.addLayout(head)

        self.battery = BatteryRow()
        root.addWidget(self.battery)
        self.storage_internal = StorageBar("Internal storage")
        root.addWidget(self.storage_internal)
        self.storage_sd = StorageBar("SD card")
        root.addWidget(self.storage_sd)

        buttons = QHBoxLayout()
        self.btn_transfer = QPushButton("Transfer new files")
        self.btn_transfer.setObjectName("primary")
        self.btn_transfer.clicked.connect(self.transfer_clicked)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh_clicked)
        buttons.addWidget(self.btn_transfer, 1)
        buttons.addWidget(self.btn_refresh)
        root.addLayout(buttons)


class SettingsTab(QWidget):
    changed = Signal(dict)

    def __init__(self, settings: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = dict(settings)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        def row(label: str, widget: QWidget) -> None:
            h = QHBoxLayout()
            lab = QLabel(label)
            lab.setMinimumWidth(180)
            h.addWidget(lab)
            h.addWidget(widget, 1)
            root.addLayout(h)

        self.base_dir = QLineEdit(settings.get("base_dir", "D:/DJI-Offload"))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._pick_dir)
        h = QHBoxLayout()
        lab = QLabel("Base folder")
        lab.setMinimumWidth(180)
        h.addWidget(lab)
        h.addWidget(self.base_dir, 1)
        h.addWidget(browse)
        root.addLayout(h)

        self.template = QLineEdit(settings.get("template", "{original}"))
        row("Naming template", self.template)

        self.tray_mode = QCheckBox("Run as tray program (close hides to tray)")
        self.tray_mode.setChecked(bool(settings.get("tray_mode", True)))
        root.addWidget(self.tray_mode)

        self.start_minimized = QCheckBox("Start minimized to tray")
        self.start_minimized.setChecked(bool(settings.get("start_minimized", False)))
        root.addWidget(self.start_minimized)

        self.auto_transfer = QCheckBox("Auto-transfer when the camera appears (while app is running)")
        self.auto_transfer.setChecked(bool(settings.get("auto_transfer", False)))
        root.addWidget(self.auto_transfer)

        self.keep_awake = QCheckBox("Keep PC awake during transfers")
        self.keep_awake.setChecked(bool(settings.get("keep_awake", True)))
        root.addWidget(self.keep_awake)

        root.addStretch(1)
        note = QLabel(
            "WiFi adapter: system default (selectable when multiple are present).\n"
            "Bluetooth adapter: Windows always uses the default radio."
        )
        note.setObjectName("dim")
        root.addWidget(note)

        save = QPushButton("Save settings")
        save.clicked.connect(self._save)
        root.addWidget(save, 0, Qt.AlignmentFlag.AlignRight)

    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose base folder", self.base_dir.text())
        if d:
            self.base_dir.setText(d)

    def _save(self) -> None:
        self._settings.update(
            base_dir=self.base_dir.text().strip(),
            template=self.template.text().strip() or "{original}",
            tray_mode=self.tray_mode.isChecked(),
            start_minimized=self.start_minimized.isChecked(),
            auto_transfer=self.auto_transfer.isChecked(),
            keep_awake=self.keep_awake.isChecked(),
        )
        self.changed.emit(dict(self._settings))


class MainWindow(QWidget):
    transfer_requested = Signal()
    refresh_requested = Signal()
    settings_saved = Signal(dict)

    def __init__(self, settings: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Osmo Offload")
        self.resize(980, 640)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        rail = QWidget()
        rail.setObjectName("leftRail")
        rail.setFixedWidth(230)
        rail_l = QVBoxLayout(rail)
        rail_l.setContentsMargins(8, 14, 8, 10)
        cams_label = QLabel("  Cameras")
        cams_label.setObjectName("dim")
        rail_l.addWidget(cams_label)
        self.camera_list = QListWidget()
        rail_l.addWidget(self.camera_list, 1)
        root.addWidget(rail)

        main = QVBoxLayout()
        main.setContentsMargins(18, 16, 18, 12)
        main.setSpacing(14)
        self.card = CameraCard()
        self.card.transfer_clicked.connect(self.transfer_requested)
        self.card.refresh_clicked.connect(self.refresh_requested)
        main.addWidget(self.card)

        self.tabs = QTabWidget()
        self.queue_table = self._make_table(["File", "Size", "Progress", "Status"])
        self.history_table = self._make_table(["When", "File", "Kind", "Size", "Destination", "Verified"])
        self.settings_tab = SettingsTab(settings)
        self.settings_tab.changed.connect(self.settings_saved)
        self.tabs.addTab(self.queue_table, "Queue")
        self.tabs.addTab(self.history_table, "History")
        self.tabs.addTab(self.settings_tab, "Settings")
        main.addWidget(self.tabs, 1)

        self.status_line = QLabel("Idle")
        self.status_line.setObjectName("dim")
        main.addWidget(self.status_line)
        root.addLayout(main, 1)

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        return t

    # -- helpers the controller calls ---------------------------------------

    def set_cameras(self, cameras: list[tuple[str, str, bool]]) -> None:
        """cameras: (id, label, in_range)"""
        self.camera_list.clear()
        for cam_id, label, in_range in cameras:
            item = QListWidgetItem(("● " if in_range else "○ ") + label)
            item.setData(Qt.ItemDataRole.UserRole, cam_id)
            self.camera_list.addItem(item)
        if cameras:
            self.camera_list.setCurrentRow(0)

    def set_history_rows(self, rows: list[tuple[str, str, str, str, str, str]]) -> None:
        self.history_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                self.history_table.setItem(r, c, QTableWidgetItem(val))
