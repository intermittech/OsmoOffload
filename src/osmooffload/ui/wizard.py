"""First-run wizard: base folder -> radio check -> pair the camera."""

from __future__ import annotations

import subprocess

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QStackedLayout, QVBoxLayout, QWidget,
)

RADIO_PS = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name.StartsWith('IAsyncOperation') })[0]
function Await($t, $rt) { $nt = $asTask.MakeGenericMethod($rt).Invoke($null, @($t)); $nt.Wait(-1) | Out-Null; $nt.Result }
[Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType = WindowsRuntime] | Out-Null
$null = Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
foreach ($r in $radios) {
    if ($r.State -ne 'On') { $null = Await ($r.SetStateAsync('On')) ([Windows.Devices.Radios.RadioAccessStatus]) }
}
$radios2 = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
($radios2 | ForEach-Object { "$($_.Kind)=$($_.State)" }) -join ';'
"""


def radio_states() -> str:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", RADIO_PS],
            capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return out.stdout.decode("mbcs", "replace").strip().splitlines()[-1] if out.stdout else ""
    except Exception:
        return ""


class FirstRunWizard(QDialog):
    pair_requested = Signal()

    def __init__(self, settings: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Welcome to Osmo Offload")
        self.setModal(True)
        self.resize(560, 340)

        outer = QVBoxLayout(self)
        self._stack = QStackedLayout()
        outer.addLayout(self._stack, 1)

        # page 1 — welcome + base folder
        p1 = QWidget()
        v1 = QVBoxLayout(p1)
        title = QLabel("Welcome")
        title.setObjectName("title")
        v1.addWidget(title)
        intro = QLabel(
            "Osmo Offload pulls footage off your DJI Osmo wirelessly — no phone, "
            "no cable, no account.\n\nWhere should your footage land?"
        )
        intro.setWordWrap(True)
        v1.addWidget(intro)
        row = QHBoxLayout()
        self.base_dir = QLineEdit(
            str(settings.get("base_dir", r"D:\DJI-Offload")).replace("/", "\\")
        )
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._pick)
        row.addWidget(self.base_dir, 1)
        row.addWidget(browse)
        v1.addLayout(row)
        v1.addStretch(1)

        # page 2 — radios
        p2 = QWidget()
        v2 = QVBoxLayout(p2)
        t2 = QLabel("Bluetooth && WiFi")
        t2.setObjectName("title")
        v2.addWidget(t2)
        e2 = QLabel(
            "The app needs Bluetooth (to wake and pair the camera) and WiFi "
            "(to join the camera's own network for the transfer).\n\n"
            "Click Check to verify both radios are on — they'll be switched on "
            "if Windows allows it."
        )
        e2.setWordWrap(True)
        v2.addWidget(e2)
        self.radio_status = QLabel("Not checked yet.")
        self.radio_status.setObjectName("dim")
        v2.addWidget(self.radio_status)
        btn_check = QPushButton("Check / enable radios")
        btn_check.clicked.connect(self._check_radios)
        v2.addWidget(btn_check, 0, Qt.AlignmentFlag.AlignLeft)
        v2.addStretch(1)

        # page 3 — pair
        p3 = QWidget()
        v3 = QVBoxLayout(p3)
        t3 = QLabel("Pair your camera")
        t3.setObjectName("title")
        v3.addWidget(t3)
        e3 = QLabel(
            "1. Power on your Osmo and keep it within a couple of meters.\n"
            "2. Click Finish && pair below.\n"
            "3. When the camera shows a prompt reading OSMO, tap approve on "
            "its screen.\n\nYou can also skip this and pair later with Refresh."
        )
        e3.setWordWrap(True)
        v3.addWidget(e3)
        v3.addStretch(1)

        for p in (p1, p2, p3):
            self._stack.addWidget(p)

        nav = QHBoxLayout()
        self.btn_skip = QPushButton("Skip setup")
        self.btn_skip.clicked.connect(self._finish_no_pair)
        self.btn_back = QPushButton("Back")
        self.btn_back.clicked.connect(lambda: self._go(-1))
        self.btn_next = QPushButton("Next")
        self.btn_next.setObjectName("primary")
        self.btn_next.clicked.connect(lambda: self._go(+1))
        nav.addWidget(self.btn_skip)
        nav.addStretch(1)
        nav.addWidget(self.btn_back)
        nav.addWidget(self.btn_next)
        outer.addLayout(nav)
        self._sync_nav()

    def _pick(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose base folder", self.base_dir.text())
        if d:
            self.base_dir.setText(d.replace("/", "\\"))  # Qt returns / on Windows

    def _check_radios(self) -> None:
        self.radio_status.setText("Checking…")
        self.radio_status.repaint()
        states = radio_states()
        self.radio_status.setText(states.replace(";", "   ") or
                                  "Could not query radios — check them in Windows quick settings.")

    def _go(self, step: int) -> None:
        i = self._stack.currentIndex() + step
        if i >= self._stack.count():
            self._finish_pair()
            return
        self._stack.setCurrentIndex(max(0, i))
        self._sync_nav()

    def _sync_nav(self) -> None:
        i = self._stack.currentIndex()
        self.btn_back.setEnabled(i > 0)
        self.btn_next.setText("Finish && pair" if i == self._stack.count() - 1 else "Next")

    def _save(self) -> None:
        base = self.base_dir.text().strip().replace("/", "\\")
        self.settings["base_dir"] = base or r"D:\DJI-Offload"
        self.settings["wizard_done"] = True

    def _finish_no_pair(self) -> None:
        self._save()
        self.reject()

    def _finish_pair(self) -> None:
        self._save()
        self.accept()
        self.pair_requested.emit()
