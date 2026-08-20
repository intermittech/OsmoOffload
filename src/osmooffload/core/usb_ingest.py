"""USB ingest: same pipeline (dedup/naming/history/reports), wired transport.

A DJI Pocket presents as mass storage when cabled. We only ingest volumes that
positively look like a DJI camera card — a `DCIM` folder containing a DJI-style
subfolder AND either a `MISC` folder or DJI-named media — never a random stick.
Both banks appear as separate volumes; each is scanned and ingested.
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import naming
from ..history import HistoryDB
from .offload import OffloadConfig, SessionProgress

log = logging.getLogger("osmo.usb")

DRIVE_REMOVABLE = 2
DJI_DIR_RE = re.compile(r"^(DJI|OSMO|\d{3}\w*)", re.IGNORECASE)
DJI_FILE_RE = re.compile(r"^(DJI|CAM)_\d{8,14}", re.IGNORECASE)
SKIP_EXTS = {"LRF", "LRV", "XRF", "SCR", "THM"}


@dataclass
class UsbFile:
    src: Path
    media_path: str  # camera-style relative path, no extension
    name: str
    size: int


@dataclass
class UsbVolume:
    root: Path
    label: str
    files: list[UsbFile] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)


def _volume_label(root: str) -> str:
    buf = ctypes.create_unicode_buffer(261)
    ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(root), buf, 261, None, None, None, None, 0
    )
    return buf.value or root.rstrip(":\\/")


def looks_like_dji(root: Path) -> bool:
    dcim = root / "DCIM"
    if not dcim.is_dir():
        return False
    try:
        subs = [d for d in dcim.iterdir() if d.is_dir()]
    except OSError:
        return False
    dji_dirs = [d for d in subs if DJI_DIR_RE.match(d.name)]
    if not dji_dirs:
        return False
    if (root / "MISC").is_dir():
        return True
    # no MISC: require actual DJI-named media inside
    for d in dji_dirs:
        try:
            for f in d.iterdir():
                if f.is_file() and DJI_FILE_RE.match(f.name):
                    return True
        except OSError:
            continue
    return False


def enumerate_volume(root: Path) -> UsbVolume:
    vol = UsbVolume(root=root, label=_volume_label(str(root)))
    dcim = root / "DCIM"
    for folder in sorted(d for d in dcim.iterdir() if d.is_dir()):
        try:
            entries = sorted(f for f in folder.iterdir() if f.is_file())
        except OSError:
            continue
        for f in entries:
            parsed = naming.parse_name(f.name)
            if parsed.ext in SKIP_EXTS or f.name.startswith("."):
                continue
            try:
                size = f.stat().st_size
            except OSError:
                continue
            media_path = f"DCIM/{folder.name}/{parsed.base}"
            vol.files.append(
                UsbFile(src=f, media_path=media_path, name=f.name, size=size)
            )
    return vol


def find_dji_volumes() -> list[UsbVolume]:
    out: list[UsbVolume] = []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if not mask & (1 << i):
            continue
        root_s = f"{chr(65 + i)}:\\"
        if ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root_s)) != DRIVE_REMOVABLE:
            continue
        root = Path(root_s)
        try:
            if looks_like_dji(root):
                out.append(enumerate_volume(root))
        except OSError as e:
            log.debug("volume %s skipped: %s", root_s, e)
    return out


class UsbIngester:
    """Copy new files off a DJI volume with the same dedup/layout/history."""

    def __init__(self, cfg: OffloadConfig, db: HistoryDB):
        self.cfg = cfg
        self.db = db

    def _dest_for(self, name: str) -> Path:
        parsed = naming.parse_name(name)
        out_name = naming.apply_template(self.cfg.template, parsed, self.cfg.camera_folder, name)
        parts = [naming._sanitize(self.cfg.camera_folder), naming.date_folder(parsed)]
        if self.cfg.split_kind_folders:
            parts.append(parsed.kind)
        return self.cfg.base_dir.joinpath(*parts, out_name)

    def plan(self, vol: UsbVolume) -> list[tuple[UsbFile, Path, str | None]]:
        items = []
        for uf in vol.files:
            parsed = naming.parse_name(uf.name)
            skip = None
            if parsed.kind not in self.cfg.kinds:
                skip = f"filtered ({parsed.kind})"
            elif self.db.is_offloaded(self.cfg.camera_folder, uf.media_path, uf.size):
                skip = "already offloaded"
            dest = self._dest_for(uf.name)
            if skip is None and dest.exists() and dest.stat().st_size == uf.size:
                skip = "exists on disk"
            items.append((uf, dest, skip))
        return items

    def run(
        self,
        items: list[tuple[UsbFile, Path, str | None]],
        progress: Callable[[SessionProgress], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> SessionProgress:
        todo = [(uf, dest) for uf, dest, skip in items if not skip]
        prog = SessionProgress(
            total_files=len(todo), total_bytes=sum(uf.size for uf, _ in todo)
        )
        session_id = self.db.start_session(
            self.cfg.camera_folder, transport="usb", log_path=self.cfg.log_path
        )
        prog.session_id = session_id
        try:
            for uf, dest in todo:
                if cancelled and cancelled():
                    prog.failures.append("cancelled")
                    break
                parsed = naming.parse_name(uf.name)
                prog.current_name = uf.name
                prog.current_total = uf.size
                prog.current_done = 0
                tid = self.db.start_transfer(
                    session_id, self.cfg.camera_folder, uf.media_path, uf.name,
                    parsed.kind, uf.size, "usb", str(dest), dest_name=dest.name,
                )
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    part = dest.with_suffix(dest.suffix + ".part")
                    h = hashlib.blake2b(digest_size=16)
                    copied = 0
                    with uf.src.open("rb") as fin, part.open("wb") as fout:
                        while chunk := fin.read(4 << 20):
                            if cancelled and cancelled():
                                raise OSError("cancelled")
                            fout.write(chunk)
                            h.update(chunk)
                            copied += len(chunk)
                            prog.current_done = copied
                            if progress:
                                progress(prog)
                    if copied != uf.size:
                        raise OSError(f"short copy {copied}/{uf.size}")
                    part.replace(dest)
                    self.db.finish_transfer(tid, "done", h.hexdigest(), True)
                    prog.done_files += 1
                    prog.done_bytes += copied
                    prog.results.append(
                        {"original": uf.name, "saved_as": dest.name,
                         "dest_path": str(dest), "size": copied, "hash": h.hexdigest(),
                         "storage": "usb", "kind": parsed.kind, "verified": True}
                    )
                    log.info("usb done %s -> %s (%d B)", uf.name, dest.name, copied)
                except OSError as e:
                    self.db.finish_transfer(tid, "failed", None, False)
                    prog.failures.append(f"{uf.name}: {e}")
                    log.error("usb FAILED %s: %s", uf.name, e)
                    if str(e) == "cancelled":
                        break
                if progress:
                    progress(prog)
        finally:
            self.db.finish_session(session_id, prog.done_files, prog.done_bytes,
                                   note="; ".join(prog.failures[:5]))
        return prog
