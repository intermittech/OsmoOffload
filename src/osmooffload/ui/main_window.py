"""Main window: camera rail, status card, Media grid, Queue/History/Settings."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QGraphicsOpacityEffect, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QStackedLayout, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
    QWidget,
)

from . import theme
from .widgets import AnimatedBar, BatteryRow, StorageBar


def human_size(n: int | None) -> str:
    if not n:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1000
    return "—"


def placeholder_thumb(size: QSize = QSize(160, 90)) -> QPixmap:
    pm = QPixmap(size)
    pm.fill(QColor(theme.BG_INSET))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor(theme.BORDER))
    p.setBrush(QColor(theme.BG_CARD))
    p.drawRoundedRect(4, 4, size.width() - 8, size.height() - 8, 8, 8)
    p.setPen(QColor(theme.FG_DIM))
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "…")
    p.end()
    return pm


class StatusPill(QLabel):
    def __init__(self, parent: QWidget | None = None):
        super().__init__("offline", parent)
        self.setObjectName("pill")
        self.set_state("idle", "idle")

    def set_state(self, state: str, text: str) -> None:
        self.setText(text)
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)


class CameraCard(QWidget):
    transfer_clicked = Signal()
    refresh_clicked = Signal()
    cancel_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        head = QHBoxLayout()
        self.title = QLabel("No camera")
        self.title.setObjectName("title")
        self.pill = StatusPill()
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.pill)
        root.addLayout(head)

        self.battery = BatteryRow()
        root.addWidget(self.battery)
        self.storage_internal = StorageBar("Internal storage", theme.STORE_INTERNAL)
        root.addWidget(self.storage_internal)
        self.storage_sd = StorageBar("SD card", theme.STORE_SD)
        root.addWidget(self.storage_sd)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.btn_transfer = QPushButton("Transfer new files")
        self.btn_transfer.setObjectName("primary")
        self.btn_transfer.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_transfer.clicked.connect(self.transfer_clicked)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.clicked.connect(self.refresh_clicked)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("danger")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.cancel_clicked)
        self.btn_cancel.hide()
        buttons.addWidget(self.btn_transfer, 1)
        buttons.addWidget(self.btn_refresh)
        buttons.addWidget(self.btn_cancel)
        root.addLayout(buttons)

    def set_busy(self, busy: bool, transferring: bool = False) -> None:
        self.btn_transfer.setEnabled(not busy)
        self.btn_refresh.setEnabled(not busy)
        self.btn_cancel.setVisible(transferring)


class MediaTab(QWidget):
    transfer_selected = Signal(list)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._items: dict[str, QListWidgetItem] = {}
        self._stack = QStackedLayout(self)

        self.empty = QLabel("Refresh to browse what's on the camera.")
        self.empty.setObjectName("empty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        content = QWidget()
        v = QVBoxLayout(content)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(8)

        bar = QHBoxLayout()
        self.btn_new = QPushButton("Select new")
        self.btn_none = QPushButton("Clear selection")
        for b in (self.btn_new, self.btn_none):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_get = QPushButton("Transfer selected")
        self.btn_get.setObjectName("primary")
        self.btn_get.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_get.clicked.connect(
            lambda: self.transfer_selected.emit(self.checked_names())
        )
        bar.addWidget(self.btn_new)
        bar.addWidget(self.btn_none)
        bar.addStretch(1)
        bar.addWidget(self.btn_get)
        v.addLayout(bar)

        self.grid = QListWidget()
        self.grid.setObjectName("mediaGrid")
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setIconSize(QSize(160, 90))
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setSpacing(6)
        self.grid.setUniformItemSizes(True)
        self.grid.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self._just_changed = False
        self.grid.itemChanged.connect(self._on_item_changed)
        self.grid.itemClicked.connect(self._on_item_clicked)
        v.addWidget(self.grid, 1)

        self.btn_new.clicked.connect(lambda: self._check(only_new=True))
        self.btn_none.clicked.connect(lambda: self._check(none=True))

        self._stack.addWidget(self.empty)
        self._stack.addWidget(content)
        self._stack.setCurrentWidget(self.empty)
        self._update_button()

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        # checked == selected, visually (amber border via ::item:selected)
        self._just_changed = True
        item.setSelected(item.checkState() == Qt.CheckState.Checked)
        self._update_button()

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        # Click anywhere on a tile toggles it — unless this click already
        # toggled the checkbox itself (itemChanged fired first).
        if self._just_changed:
            self._just_changed = False
            item.setSelected(item.checkState() == Qt.CheckState.Checked)
            return
        item.setCheckState(
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

    def set_files(self, rows: list[tuple[str, int, str]]) -> None:
        """rows: (name, size, note) — note 'queued' means new/transferable."""
        self.grid.blockSignals(True)
        self.grid.clear()
        self._items.clear()
        ph = placeholder_thumb()
        for name, size, note in rows:
            label = f"{name}\n{human_size(size)}" + ("" if note == "queued" else f" · {note}")
            item = QListWidgetItem(QIcon(ph), label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if note == "queued" else Qt.CheckState.Unchecked
            )
            item.setSizeHint(QSize(176, 140))
            self.grid.addItem(item)
            self._items[name] = item
        self.grid.blockSignals(False)
        for name, item in self._items.items():
            item.setSelected(item.checkState() == Qt.CheckState.Checked)
        self._stack.setCurrentWidget(self.empty if not rows else self._stack.widget(1))
        self._update_button()

    def set_thumb(self, name: str, path: str) -> None:
        item = self._items.get(name)
        if item:
            item.setIcon(QIcon(path))

    def checked_names(self) -> list[str]:
        return [
            self.grid.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.grid.count())
            if self.grid.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _check(self, only_new: bool = False, none: bool = False) -> None:
        self.grid.blockSignals(True)
        for i in range(self.grid.count()):
            item = self.grid.item(i)
            if none:
                item.setCheckState(Qt.CheckState.Unchecked)
            elif only_new:
                is_new = "·" not in item.text()
                item.setCheckState(
                    Qt.CheckState.Checked if is_new else Qt.CheckState.Unchecked
                )
        self.grid.blockSignals(False)
        self._update_button()

    def _update_button(self) -> None:
        n = len(self.checked_names())
        self.btn_get.setText(f"Transfer selected ({n})" if n else "Transfer selected")
        self.btn_get.setEnabled(n > 0)

    def set_busy(self, busy: bool) -> None:
        for b in (self.btn_new, self.btn_none):
            b.setEnabled(not busy)
        self.btn_get.setEnabled(not busy and bool(self.checked_names()))


class TablePane(QWidget):
    def __init__(self, headers: list[str], empty_text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._stack = QStackedLayout(self)
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.empty = QLabel(empty_text)
        self.empty.setObjectName("empty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack.addWidget(self.empty)
        self._stack.addWidget(self.table)
        self._stack.setCurrentWidget(self.empty)

    def show_rows(self, has_rows: bool) -> None:
        self._stack.setCurrentWidget(self.table if has_rows else self.empty)


class SettingsTab(QWidget):
    changed = Signal(dict)

    def __init__(self, settings: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = dict(settings)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

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

        h2 = QHBoxLayout()
        lab2 = QLabel("Naming template")
        lab2.setMinimumWidth(180)
        self.template = QLineEdit(settings.get("template", "{original}"))
        self.template.setPlaceholderText("{original}  or e.g. {date}_{time}_{camera}_{seq}")
        h2.addWidget(lab2)
        h2.addWidget(self.template, 1)
        root.addLayout(h2)

        self.tray_mode = QCheckBox("Run as tray program (closing the window hides to tray)")
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
            "WiFi adapter: system default (picker appears when several are present).\n"
            "Bluetooth: Windows always routes through its default radio."
        )
        note.setObjectName("dim")
        root.addWidget(note)

        save = QPushButton("Save settings")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
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
    transfer_selected_requested = Signal(list)
    refresh_requested = Signal()
    cancel_requested = Signal()
    settings_saved = Signal(dict)

    def __init__(self, settings: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle("Osmo Offload")
        self.resize(1020, 700)
        self._queue_rows: dict[str, int] = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        rail = QWidget()
        rail.setObjectName("leftRail")
        rail.setFixedWidth(232)
        rail_l = QVBoxLayout(rail)
        rail_l.setContentsMargins(8, 16, 8, 12)
        cams_label = QLabel("  Cameras")
        cams_label.setObjectName("dim")
        rail_l.addWidget(cams_label)
        self.camera_list = QListWidget()
        rail_l.addWidget(self.camera_list, 1)
        root.addWidget(rail)

        main = QVBoxLayout()
        main.setContentsMargins(20, 18, 20, 12)
        main.setSpacing(16)
        self.card = CameraCard()
        self.card.transfer_clicked.connect(self.transfer_requested)
        self.card.refresh_clicked.connect(self.refresh_requested)
        self.card.cancel_clicked.connect(self.cancel_requested)
        main.addWidget(self.card)

        self.tabs = QTabWidget()
        self.media = MediaTab()
        self.media.transfer_selected.connect(self.transfer_selected_requested)
        self.queue = TablePane(
            ["File", "Size", "Progress", "Status"],
            "Nothing queued yet.\nRefresh previews what's new; Transfer pulls it in.",
        )
        self.history = TablePane(
            ["When", "Original name", "Saved as", "Size", "Verified"],
            "No transfers recorded yet.",
        )
        self.settings_tab = SettingsTab(settings)
        self.settings_tab.changed.connect(self.settings_saved)
        self.tabs.addTab(self.media, "Media")
        self.tabs.addTab(self.queue, "Queue")
        self.tabs.addTab(self.history, "History")
        self.tabs.addTab(self.settings_tab, "Settings")
        main.addWidget(self.tabs, 1)

        self.status_line = QLabel("Idle")
        self.status_line.setObjectName("dim")
        main.addWidget(self.status_line)
        root.addLayout(main, 1)

        self._fade_in(self.card)

    @staticmethod
    def _fade_in(widget: QWidget) -> None:
        eff = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", widget)
        anim.setDuration(theme.DUR)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: widget.setGraphicsEffect(None))
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    # -- controller-facing helpers ------------------------------------------

    def set_cameras(self, cameras: list[tuple[str, str, bool]]) -> None:
        self.camera_list.clear()
        for cam_id, label, in_range in cameras:
            item = QListWidgetItem(("● " if in_range else "○ ") + label)
            item.setData(Qt.ItemDataRole.UserRole, cam_id)
            self.camera_list.addItem(item)
        if cameras:
            self.camera_list.setCurrentRow(0)

    def set_plan(self, rows: list[tuple[str, int, str]]) -> None:
        t = self.queue.table
        t.setRowCount(len(rows))
        self._queue_rows.clear()
        for r, (name, size, note) in enumerate(rows):
            self._queue_rows[name] = r
            t.setItem(r, 0, QTableWidgetItem(name))
            t.setItem(r, 1, QTableWidgetItem(human_size(size)))
            bar = AnimatedBar()
            bar.setValue(1000 if note in ("already offloaded", "exists on disk") else 0)
            t.setCellWidget(r, 2, bar)
            t.setItem(r, 3, QTableWidgetItem(note))
        self.queue.show_rows(bool(rows))
        self.media.set_files(rows)

    def update_file_progress(self, name: str, done: int, total: int, status: str) -> None:
        r = self._queue_rows.get(name)
        if r is None:
            return
        bar = self.queue.table.cellWidget(r, 2)
        if isinstance(bar, AnimatedBar) and total:
            bar.animate_to(int(1000 * done / total))
        self.queue.table.setItem(r, 3, QTableWidgetItem(status))

    def set_history_rows(self, rows: list[tuple[str, str, str, str, str, str]]) -> None:
        """rows: (when, original, saved_as, size, verified, dest_path_tooltip)"""
        t = self.history.table
        t.setRowCount(len(rows))
        for r, (when, orig, saved_as, size, verified, dest_tip) in enumerate(rows):
            for c, val in enumerate((when, orig, saved_as, size, verified)):
                item = QTableWidgetItem(val)
                if c == 2 and dest_tip:
                    item.setToolTip(dest_tip)
                t.setItem(r, c, item)
        self.history.show_rows(bool(rows))
