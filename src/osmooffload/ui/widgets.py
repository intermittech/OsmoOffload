"""Reusable widgets: the storage bar (locked display format) and battery row."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget,
)


def mib_to_gb(mib: int | None) -> float:
    """Camera reports MiB; its own screen divides by 1024 and labels it GB —
    we match the camera so the numbers agree with what the user sees on it."""
    return (mib or 0) / 1024.0


class StorageBar(QWidget):
    """`<name>  [######----] 62% — 318 GB of 512 GB, 194 GB left`"""

    def __init__(self, name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._name = name
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(3)

        top = QHBoxLayout()
        self.label_name = QLabel(name)
        self.label_name.setObjectName("big")
        self.label_detail = QLabel("—")
        self.label_detail.setObjectName("dim")
        top.addWidget(self.label_name)
        top.addStretch(1)
        top.addWidget(self.label_detail)
        root.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setObjectName("storage")
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setFixedHeight(10)
        root.addWidget(self.bar)

    def set_absent(self, reason: str = "not present") -> None:
        self.bar.setValue(0)
        self.bar.setObjectName("storage")
        self.label_detail.setText(reason)
        self._restyle()

    def set_mib(self, total_mib: int | None, free_mib: int | None) -> None:
        if not total_mib:
            self.set_absent("no card")
            return
        free_mib = free_mib or 0
        used = total_mib - free_mib
        pct_used = 100.0 * used / total_mib
        self.bar.setValue(int(pct_used * 10))
        self.bar.setObjectName(
            "storageFull" if pct_used >= 90 else "storageWarn" if pct_used >= 75 else "storage"
        )
        total_gb = mib_to_gb(total_mib)
        used_gb = mib_to_gb(used)
        free_gb = mib_to_gb(free_mib)
        self.label_detail.setText(
            f"{pct_used:.0f}% — {used_gb:.1f} GB of {total_gb:.1f} GB, {free_gb:.1f} GB left"
        )
        self._restyle()

    def _restyle(self) -> None:
        # re-polish so the objectName-based chunk colour applies
        self.bar.style().unpolish(self.bar)
        self.bar.style().polish(self.bar)


class BatteryRow(QWidget):
    """`Battery  86%  (charging)` with a slim bar."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(3)
        top = QHBoxLayout()
        name = QLabel("Battery")
        name.setObjectName("big")
        self.label_detail = QLabel("—")
        self.label_detail.setObjectName("dim")
        top.addWidget(name)
        top.addStretch(1)
        top.addWidget(self.label_detail)
        root.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setObjectName("storage")
        self.bar.setRange(0, 100)
        self.bar.setFixedHeight(10)
        root.addWidget(self.bar)

    def set_state(self, pct: int | None, current_ma: int | None) -> None:
        if pct is None:
            self.bar.setValue(0)
            self.label_detail.setText("—")
            return
        self.bar.setValue(pct)
        self.bar.setObjectName("storageFull" if pct <= 15 else "storageWarn" if pct <= 30 else "storage")
        suffix = ""
        if current_ma is not None:
            suffix = "  (charging)" if current_ma > 50 else ""
        self.label_detail.setText(f"{pct}%{suffix}")
        self.bar.style().unpolish(self.bar)
        self.bar.style().polish(self.bar)
