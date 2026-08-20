"""Main window: camera rail, status card, Media grid, Queue/History/Settings."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
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


class Legend(QWidget):
    """Tiny key at the bottom of the camera rail explaining the visuals."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("legend")
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 6, 8, 4)
        v.setSpacing(4)

        def swatch(color: str) -> QLabel:
            s = QLabel()
            s.setFixedSize(12, 12)
            s.setStyleSheet(
                f"background: transparent; border: 2px solid {color}; border-radius: 3px;"
            )
            return s

        def row(widget: QWidget, text: str) -> None:
            h = QHBoxLayout()
            h.setSpacing(6)
            h.addWidget(widget)
            lab = QLabel(text)
            lab.setWordWrap(True)
            h.addWidget(lab, 1)
            v.addLayout(h)

        title = QLabel("Legend")
        title.setStyleSheet("font-weight: 600;")
        v.addWidget(title)
        row(swatch(theme.KIND_VIDEO), "video")
        row(swatch(theme.KIND_PHOTO), "photo")
        badge = QLabel()
        badge.setPixmap(disk_badge(14))
        row(badge, "grayed = already on disk")
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {theme.OK}; font-size: 9pt;")
        dot.setFixedWidth(12)
        row(dot, "camera in range")
        check = QLabel("☑")
        check.setStyleSheet(f"color: {theme.ACCENT}; font-size: 10pt;")
        check.setFixedWidth(12)
        row(check, "selected to transfer")


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
    usb_clicked = Signal()
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
        self.btn_usb = QPushButton("From USB")
        self.btn_usb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_usb.setToolTip("Ingest from a cable-connected camera through the same pipeline")
        self.btn_usb.clicked.connect(self.usb_clicked)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("danger")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.cancel_clicked)
        self.btn_cancel.hide()
        buttons.addWidget(self.btn_transfer)
        buttons.addWidget(self.btn_refresh)
        buttons.addWidget(self.btn_usb)
        buttons.addWidget(self.btn_cancel)
        buttons.addStretch(1)
        root.addLayout(buttons)

    def set_busy(self, busy: bool, transferring: bool = False) -> None:
        self.btn_transfer.setEnabled(not busy)
        self.btn_refresh.setEnabled(not busy)
        self.btn_usb.setEnabled(not busy)
        self.btn_cancel.setVisible(transferring)


def disk_badge(size: int = 18) -> QPixmap:
    """'Saved to disk' glyph: a database/HDD cylinder stack with green rings."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    w = size - 4
    x = 2
    ell_h = max(3, size // 4)  # ellipse height for the cylinder look
    top = 2
    bottom = size - 2
    body = QColor("#c9ced4")
    dark = QColor("#8a9199")
    ring = QColor(theme.OK)

    # cylinder body
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(body)
    p.drawRect(x, top + ell_h // 2, w, bottom - top - ell_h)
    # bottom cap
    p.setBrush(dark)
    p.drawEllipse(x, bottom - ell_h, w, ell_h)
    # separator rings (the colored platter gaps)
    p.setBrush(ring)
    third = (bottom - top - ell_h) // 3
    for i in (1, 2):
        p.drawEllipse(x, top + ell_h // 2 + i * third - 1, w, ell_h - 1)
        p.setBrush(body)
        p.drawEllipse(x, top + ell_h // 2 + i * third - 2, w, ell_h - 1)
        p.setBrush(ring)
    # re-draw ring slivers so a crisp colored edge shows under each platter
    for i in (1, 2):
        p.drawEllipse(x + 1, top + ell_h // 2 + i * third, w - 2, ell_h - 2)
        p.setBrush(body)
        p.drawEllipse(x + 1, top + ell_h // 2 + i * third - 2, w - 2, ell_h - 2)
        p.setBrush(ring)
    # top cap
    p.setBrush(QColor("#e8ebee"))
    p.drawEllipse(x, top, w, ell_h)
    p.setPen(QColor(dark))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(x, top, w, ell_h)
    p.end()
    return pm


class MediaTile(QFrame):
    """One media file: kind-colored border, overlay checkbox (the only
    selection mechanism), disk badge + grayscale when already on disk.
    On-disk files stay selectable — selection also drives deletion."""

    toggled = Signal()

    THUMB = QSize(160, 90)

    def __init__(self, name: str, size: int, note: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.name = name
        self.note = note
        upper = name.upper()
        self.kind = "video" if upper.endswith((".MP4", ".MOV", ".LRF")) else "photo"
        self.on_disk = note == "already on disk"
        self.timestamp = self._parse_ts(name)

        self.setObjectName("tile")
        self.setFixedSize(176, 148)
        self.setProperty("kind", self.kind)
        self.setProperty("ondisk", "true" if self.on_disk else "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            f"{name} · {human_size(size)}"
            + (" · already on disk" if self.on_disk else " · NOT yet transferred")
        )

        self.thumb = QLabel(self)
        self.thumb.setGeometry(8, 8, self.THUMB.width(), self.THUMB.height())
        self.thumb.setPixmap(placeholder_thumb(self.THUMB))

        self.check = QCheckBox(self)
        self.check.move(12, 12)
        self.check.setChecked(note == "queued")
        self.check.toggled.connect(self._on_toggle)

        if self.on_disk:
            badge = QLabel(self)
            badge.setPixmap(disk_badge())
            badge.move(self.width() - 30, 12)
            badge.setToolTip("Already on disk")

    @staticmethod
    def _parse_ts(name: str) -> float | None:
        import re
        import time as _time

        m = re.search(r"_(\d{14})_", name)
        if not m:
            return None
        try:
            return _time.mktime(_time.strptime(m.group(1), "%Y%m%d%H%M%S"))
        except ValueError:
            return None

        self.label = QLabel(self)
        self.label.setObjectName("tileName")
        self.label.setGeometry(8, 102, self.width() - 16, 40)
        self.label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        metrics = self.label.fontMetrics()
        elided = metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, self.width() - 20)
        self.label.setText(f"{elided}\n{human_size(size)}")
        self._sync_style()

    # -- behavior ------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # click anywhere toggles
        self.check.toggle()
        event.accept()

    def _on_toggle(self, _checked: bool) -> None:
        self._sync_style()
        self.toggled.emit()

    def _sync_style(self) -> None:
        self.setProperty("checked", "true" if self.check.isChecked() else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    @property
    def checked(self) -> bool:
        return self.check.isChecked()

    def set_checked(self, on: bool) -> None:
        self.check.setChecked(on)

    def set_thumb_pixmap(self, src: QPixmap) -> None:
        canvas = QPixmap(self.THUMB)
        canvas.fill(QColor(theme.BG_INSET))
        scaled = src.scaled(self.THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        p = QPainter(canvas)
        if self.on_disk:
            p.setOpacity(0.35)  # grayed: already safe on the PC
            gray = scaled.toImage().convertToFormat(QImage.Format.Format_Grayscale8)
            scaled = QPixmap.fromImage(gray)
        p.drawPixmap((self.THUMB.width() - scaled.width()) // 2,
                     (self.THUMB.height() - scaled.height()) // 2, scaled)
        p.end()
        self.thumb.setPixmap(canvas)


class MediaTab(QWidget):
    transfer_selected = Signal(list)
    delete_offloaded = Signal()
    selection_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._tiles: dict[str, MediaTile] = {}
        self._pix_cache: dict[str, QPixmap] = {}  # survives grid rebuilds
        self._stack = QStackedLayout(self)

        self.empty = QLabel("Refresh to browse what's on the camera.")
        self.empty.setObjectName("empty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        content = QWidget()
        v = QVBoxLayout(content)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(8)

        bar = QHBoxLayout()
        self.btn_all = QPushButton("All")
        self.btn_all.setObjectName("selAll")
        self.btn_video = QPushButton("All video")
        self.btn_video.setObjectName("selVideo")
        self.btn_photo = QPushButton("All photo")
        self.btn_photo.setObjectName("selPhoto")
        self.btn_24h = QPushButton("Last 24 h")
        self.btn_none = QPushButton("None")
        for b in (self.btn_all, self.btn_video, self.btn_photo, self.btn_24h, self.btn_none):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_all.clicked.connect(lambda: self._select(lambda t: True))
        # additive: kind/time buttons build up the selection
        self.btn_video.clicked.connect(
            lambda: self._select(lambda t: t.kind == "video", additive=True)
        )
        self.btn_photo.clicked.connect(
            lambda: self._select(lambda t: t.kind == "photo", additive=True)
        )
        self.btn_24h.clicked.connect(self._select_last_24h)
        self.btn_none.clicked.connect(lambda: self._select(lambda t: False))

        self.btn_get = QPushButton("Transfer selected")
        self.btn_get.setObjectName("primary")
        self.btn_get.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_get.clicked.connect(
            lambda: self.transfer_selected.emit(self.checked_names())
        )
        self.btn_free = QPushButton("Delete files on camera…")
        self.btn_free.setObjectName("danger")
        self.btn_free.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_free.setToolTip(
            "Delete files from the camera that have a verified copy on this PC."
        )
        self.btn_free.clicked.connect(self.delete_offloaded)
        for b in (self.btn_all, self.btn_video, self.btn_photo, self.btn_24h, self.btn_none):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.btn_free)
        bar.addWidget(self.btn_get)
        v.addLayout(bar)

        self.grid = QListWidget()
        self.grid.setObjectName("mediaGrid")
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setSpacing(8)
        self.grid.setUniformItemSizes(True)
        self.grid.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.grid.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        v.addWidget(self.grid, 1)

        self._stack.addWidget(self.empty)
        self._stack.addWidget(content)
        self._stack.setCurrentWidget(self.empty)
        self._update_button()

    def set_files(self, rows: list[tuple[str, int, str]]) -> None:
        """rows: (name, size, note). Default: new files ('queued') checked,
        files already on disk unchecked, grayed and badge-marked."""
        self.grid.clear()
        self._tiles.clear()
        for name, size, note in rows:
            tile = MediaTile(name, size, note)
            tile.toggled.connect(self._update_button)
            item = QListWidgetItem()
            item.setSizeHint(QSize(184, 156))
            self.grid.addItem(item)
            self.grid.setItemWidget(item, tile)
            self._tiles[name] = tile
            cached = self._pix_cache.get(name)
            if cached is not None:
                tile.set_thumb_pixmap(cached)
        self._stack.setCurrentWidget(self.empty if not rows else self._stack.widget(1))
        self._update_button()

    def set_thumb(self, name: str, path: str) -> None:
        src = QPixmap(path)
        if src.isNull():
            return
        self._pix_cache[name] = src
        tile = self._tiles.get(name)
        if tile:
            tile.set_thumb_pixmap(src)

    def checked_names(self) -> list[str]:
        return [t.name for t in self._tiles.values() if t.checked]

    def unverified_checked_names(self) -> list[str]:
        """Selected files that have NO copy on disk — deleting these loses them."""
        return [t.name for t in self._tiles.values() if t.checked and not t.on_disk]

    def _select(self, predicate, additive: bool = False) -> None:
        for t in self._tiles.values():
            if predicate(t):
                t.set_checked(True)
            elif not additive:
                t.set_checked(False)
        self._update_button()

    def _select_last_24h(self) -> None:
        import time as _time

        cutoff = _time.time() - 24 * 3600
        self._select(
            lambda t: t.timestamp is not None and t.timestamp >= cutoff, additive=True
        )

    def _update_button(self) -> None:
        n = len(self.checked_names())
        self.btn_get.setText(f"Transfer selected ({n})" if n else "Transfer selected")
        self.btn_get.setEnabled(n > 0)
        self.btn_free.setEnabled(n > 0)
        self.selection_changed.emit()

    def set_busy(self, busy: bool) -> None:
        for b in (self.btn_all, self.btn_video, self.btn_photo, self.btn_24h, self.btn_none):
            b.setEnabled(not busy)
        n = bool(self.checked_names())
        self.btn_get.setEnabled(not busy and n)
        self.btn_free.setEnabled(not busy and n)


class TablePane(QWidget):
    def __init__(self, headers: list[str], empty_text: str,
                 widths: list[int] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._stack = QStackedLayout(self)
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        header = self.table.horizontalHeader()
        # user-adjustable: drag to resize, drag headers to reorder
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionsMovable(True)
        header.setStretchLastSection(True)
        if widths:
            for i, w in enumerate(widths):
                if i < len(headers):
                    self.table.setColumnWidth(i, w)
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
    check_update = Signal()

    def __init__(self, settings: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = dict(settings)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        self.base_dir = QLineEdit(
            str(settings.get("base_dir", r"D:\DJI-Offload")).replace("/", "\\")
        )
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

        kinds_row = QHBoxLayout()
        kinds_lab = QLabel("Transfer")
        kinds_lab.setMinimumWidth(180)
        self.kinds_video = QCheckBox("Videos")
        self.kinds_video.setChecked(bool(settings.get("kinds_video", True)))
        self.kinds_photo = QCheckBox("Photos")
        self.kinds_photo.setChecked(bool(settings.get("kinds_photo", True)))
        kinds_row.addWidget(kinds_lab)
        kinds_row.addWidget(self.kinds_video)
        kinds_row.addWidget(self.kinds_photo)
        kinds_row.addStretch(1)
        root.addLayout(kinds_row)

        self.write_reports = QCheckBox("Write a session report after each transfer (HTML + CSV)")
        self.write_reports.setChecked(bool(settings.get("write_reports", True)))
        root.addWidget(self.write_reports)

        self.open_folder = QCheckBox("Open the destination folder when a transfer finishes")
        self.open_folder.setChecked(bool(settings.get("open_folder", False)))
        root.addWidget(self.open_folder)

        h3 = QHBoxLayout()
        lab3 = QLabel("After-transfer command")
        lab3.setMinimumWidth(180)
        self.hook_cmd = QLineEdit(settings.get("hook_cmd", ""))
        self.hook_cmd.setPlaceholderText(
            "optional — e.g. powershell -File ingest.ps1  (gets OSMO_DEST, OSMO_REPORT, OSMO_FILES…)"
        )
        h3.addWidget(lab3)
        h3.addWidget(self.hook_cmd, 1)
        root.addLayout(h3)

        h4 = QHBoxLayout()
        lab4 = QLabel("Update source (GitHub)")
        lab4.setMinimumWidth(180)
        self.update_repo = QLineEdit(settings.get("update_repo", ""))
        self.update_repo.setPlaceholderText("owner/repo — enables the update check")
        self.btn_update = QPushButton("Check now")
        self.btn_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_update.clicked.connect(self.check_update)
        h4.addWidget(lab4)
        h4.addWidget(self.update_repo, 1)
        h4.addWidget(self.btn_update)
        root.addLayout(h4)

        root.addStretch(1)
        from .. import __version__

        note = QLabel(
            f"Osmo Offload v{__version__}\n"
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
            self.base_dir.setText(d.replace("/", "\\"))  # Qt returns / on Windows

    def _save(self) -> None:
        self._settings.update(
            base_dir=self.base_dir.text().strip().replace("/", "\\"),
            template=self.template.text().strip() or "{original}",
            tray_mode=self.tray_mode.isChecked(),
            start_minimized=self.start_minimized.isChecked(),
            auto_transfer=self.auto_transfer.isChecked(),
            keep_awake=self.keep_awake.isChecked(),
            kinds_video=self.kinds_video.isChecked(),
            kinds_photo=self.kinds_photo.isChecked(),
            write_reports=self.write_reports.isChecked(),
            open_folder=self.open_folder.isChecked(),
            hook_cmd=self.hook_cmd.text().strip(),
            update_repo=self.update_repo.text().strip(),
        )
        self.changed.emit(dict(self._settings))


class MainWindow(QWidget):
    transfer_requested = Signal()
    transfer_selected_requested = Signal(list)
    delete_offloaded_requested = Signal()
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
        rail_l.addWidget(Legend())
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
        self.media.delete_offloaded.connect(self.delete_offloaded_requested)
        self.media.selection_changed.connect(self._sync_queue)
        self._plan_rows: list[tuple[str, int, str]] = []
        self.queue = TablePane(
            ["File", "Size", "Progress", "Status"],
            "Nothing queued yet.\nRefresh previews what's new; Transfer pulls it in.",
            widths=[320, 90, 180, 140],
        )
        self.history = TablePane(
            ["When", "Action", "Files", "Data", "Speed", "Result", "Report"],
            "No sessions recorded yet.",
            widths=[130, 90, 55, 90, 80, 110, 110],
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
        self._plan_rows = list(rows)
        self.media.set_files(rows)
        self._sync_queue()

    def _sync_queue(self) -> None:
        """The Queue shows exactly the SELECTED files — nothing selected,
        nothing listed."""
        checked = set(self.media.checked_names())
        rows = [r for r in self._plan_rows if r[0] in checked]
        t = self.queue.table
        t.setRowCount(len(rows))
        self._queue_rows.clear()
        for r, (name, size, note) in enumerate(rows):
            self._queue_rows[name] = r
            t.setItem(r, 0, QTableWidgetItem(name))
            t.setItem(r, 1, QTableWidgetItem(human_size(size)))
            bar = AnimatedBar()
            bar.setValue(1000 if note == "already on disk" else 0)
            t.setCellWidget(r, 2, bar)
            t.setItem(r, 3, QTableWidgetItem(note))
        self.queue.show_rows(bool(rows))

    def _ensure_queue_row(self, name: str) -> int:
        r = self._queue_rows.get(name)
        if r is not None:
            return r
        size = next((s for n, s, _ in self._plan_rows if n == name), 0)
        t = self.queue.table
        r = t.rowCount()
        t.insertRow(r)
        t.setItem(r, 0, QTableWidgetItem(name))
        t.setItem(r, 1, QTableWidgetItem(human_size(size)))
        t.setCellWidget(r, 2, AnimatedBar())
        t.setItem(r, 3, QTableWidgetItem(""))
        self._queue_rows[name] = r
        self.queue.show_rows(True)
        return r

    def update_file_progress(self, name: str, done: int, total: int, status: str) -> None:
        r = self._ensure_queue_row(name)
        bar = self.queue.table.cellWidget(r, 2)
        if isinstance(bar, AnimatedBar) and total:
            bar.animate_to(int(1000 * done / total))
        self.queue.table.setItem(r, 3, QTableWidgetItem(status))

    def set_history_rows(self, rows: list[tuple]) -> None:
        """One row per SESSION: (when, action, files, data, speed, result,
        open_path|None, tooltip). The button opens the session report (which
        embeds the debug log in a fold)."""
        import os

        t = self.history.table
        t.setRowCount(len(rows))
        for r, (when, action, files, data, speed, result, open_path, tip) in enumerate(rows):
            for c, val in enumerate((when, action, files, data, speed, result)):
                item = QTableWidgetItem(val)
                if tip:
                    item.setToolTip(tip)
                t.setItem(r, c, item)
            if open_path and os.path.exists(open_path):
                btn = QPushButton("Open report")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setToolTip(open_path)
                btn.setStyleSheet("padding: 3px 10px; font-size: 9pt;")
                btn.clicked.connect(lambda _=False, p=open_path: os.startfile(p))
                t.setCellWidget(r, 6, btn)
            else:
                t.setItem(r, 6, QTableWidgetItem("—"))
        self.history.show_rows(bool(rows))
