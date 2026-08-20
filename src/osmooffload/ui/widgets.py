"""Reusable widgets: animated bars, the battery gradient, storage shades."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget,
)

from . import theme


def mib_to_gb(mib: int | None) -> float:
    """Camera reports MiB; its own screen divides by 1024 and labels it GB —
    we match the camera so the numbers agree with what the user sees on it."""
    return (mib or 0) / 1024.0


def battery_color(pct: float) -> QColor:
    """Red -> amber -> green fade; >=50% is greenish ramping to full green.

    Piecewise-linear hue: 0% -> 8 (red), 30% -> 45 (amber), 50% -> 95
    (greenish), 100% -> 135 (full green)."""
    stops = [(0.0, 8.0), (30.0, 45.0), (50.0, 95.0), (100.0, 135.0)]
    pct = max(0.0, min(100.0, pct))
    hue = stops[-1][1]
    for (p0, h0), (p1, h1) in zip(stops, stops[1:]):
        if pct <= p1:
            t = (pct - p0) / (p1 - p0) if p1 > p0 else 1.0
            hue = h0 + t * (h1 - h0)
            break
    sat = 0.70 if pct < 50 else 0.70 + 0.10 * ((pct - 50) / 50)
    return QColor.fromHsvF(hue / 360.0, sat, 0.86)


class AnimatedBar(QProgressBar):
    """Progress bar with eased value animation and a settable chunk color.

    Animations are short (theme.DUR), ease-out, interruptible, and skipped for
    tiny deltas or while hidden — feedback stays immediate under rapid updates.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setRange(0, 1000)
        self.setValue(0)
        self.setFixedHeight(10)
        self.setTextVisible(False)
        self._chunk_hex = ""
        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setDuration(theme.DUR)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.set_chunk_color(QColor(theme.ACCENT))

    def set_chunk_color(self, color: QColor) -> None:
        hexc = color.name()
        if hexc == self._chunk_hex:
            return
        self._chunk_hex = hexc
        self.setStyleSheet(
            f"QProgressBar {{ background: {theme.BG_INSET}; border: none;"
            f" border-radius: 5px; color: transparent; }}"
            f" QProgressBar::chunk {{ background: {hexc}; border-radius: 5px; }}"
        )

    def animate_to(self, permille: int) -> None:
        permille = max(0, min(1000, permille))
        if not self.isVisible() or abs(permille - self.value()) < 12:
            self._anim.stop()
            self.setValue(permille)
            return
        self._anim.stop()
        self._anim.setStartValue(self.value())
        self._anim.setEndValue(permille)
        self._anim.start()


class _LabeledBar(QWidget):
    def __init__(self, name: str, parent: QWidget | None = None):
        super().__init__(parent)
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
        self.bar = AnimatedBar()
        root.addWidget(self.bar)


class StorageBar(_LabeledBar):
    """`<name>  [######----] 62% — 318 GB of 512 GB, 194 GB left`

    Each bar carries its own shade of the storage hue; >=92% full overrides
    to the semantic error red (color + the percentage text carry the state).
    """

    def __init__(self, name: str, shade: str, parent: QWidget | None = None):
        super().__init__(name, parent)
        self._shade = QColor(shade)
        self.bar.set_chunk_color(self._shade)

    def set_absent(self, reason: str = "not present") -> None:
        self.bar.animate_to(0)
        self.label_detail.setText(reason)

    def set_mib(self, total_mib: int | None, free_mib: int | None) -> None:
        if not total_mib:
            self.set_absent("no card")
            return
        free_mib = free_mib or 0
        used = total_mib - free_mib
        pct_used = 100.0 * used / total_mib
        self.bar.set_chunk_color(QColor(theme.ERR) if pct_used >= 92 else self._shade)
        self.bar.animate_to(int(pct_used * 10))
        self.label_detail.setText(
            f"{pct_used:.0f}% — {mib_to_gb(used):.1f} GB of {mib_to_gb(total_mib):.1f} GB, "
            f"{mib_to_gb(free_mib):.1f} GB left"
        )


class BatteryRow(_LabeledBar):
    """Battery with the red->amber->green gradient fill."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Battery", parent)

    def set_state(self, pct: int | None, current_ma: int | None) -> None:
        if pct is None:
            self.bar.animate_to(0)
            self.label_detail.setText("—")
            return
        self.bar.set_chunk_color(battery_color(pct))
        self.bar.animate_to(pct * 10)
        suffix = "  (charging)" if (current_ma or 0) > 50 else ""
        self.label_detail.setText(f"{pct}%{suffix}")
