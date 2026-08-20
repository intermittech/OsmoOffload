"""Offload orchestration: manifest records -> planned transfers -> verified files.

Folder layout (locked spec): <base>\\<CameraNameOrSerial>\\<YYYY-MM-DD>\\video|photo\\<name>
Dedup against the history DB by (camera, media_path, size). Storage index for
the /v2 URL is probed per store-group (mount indices are per-body, not fixed).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import naming
from ..camera.manifest import MediaRecord
from ..history import HistoryDB
from ..net.downloader import CameraHttp, DownloadError

log = logging.getLogger("osmo.offload")


@dataclass
class OffloadConfig:
    base_dir: Path
    camera_folder: str
    template: str = "{original}"
    kinds: tuple[str, ...] = ("video", "photo", "other")
    split_kind_folders: bool = True
    log_path: str | None = None  # session log file, recorded in history


@dataclass
class PlanItem:
    record: MediaRecord
    url_path: str  # media path WITH extension, as /v2 wants it
    dest: Path
    storage_idx: int | None = None
    size: int | None = None
    skipped: str | None = None  # reason, when not transferring


@dataclass
class SessionProgress:
    total_files: int = 0
    total_bytes: int = 0
    done_files: int = 0
    done_bytes: int = 0
    current_name: str = ""
    current_done: int = 0
    current_total: int = 0
    session_id: int | None = None
    results: list[dict] = field(default_factory=list)  # per completed file
    failures: list[str] = field(default_factory=list)


class Offloader:
    def __init__(self, cfg: OffloadConfig, db: HistoryDB, http: CameraHttp):
        self.cfg = cfg
        self.db = db
        self.http = http
        # optional hook: called when a download hits network loss; should try
        # to restore connectivity (e.g. rejoin the camera AP) and return
        self.on_network_lost = None

    # -- planning ------------------------------------------------------------

    def _dest_for(self, rec: MediaRecord) -> Path:
        parsed = naming.parse_name(rec.name)
        out_name = naming.apply_template(self.cfg.template, parsed, self.cfg.camera_folder, rec.name)
        parts = [naming._sanitize(self.cfg.camera_folder), naming.date_folder(parsed)]
        if self.cfg.split_kind_folders:
            parts.append(parsed.kind)
        return self.cfg.base_dir.joinpath(*parts, out_name)

    def plan(self, records: list[MediaRecord]) -> list[PlanItem]:
        """Resolve storage indices per store-group, sizes for the sizeless
        (photos), and skip what history says is already offloaded."""
        items: list[PlanItem] = []
        # group by the record's store label (0/1/None) to probe mounts once per group
        mount_for_group: dict[int | None, int] = {}
        for rec in records:
            parsed = naming.parse_name(rec.name)
            url_path = f"{rec.media_path}.{rec.ext}" if rec.ext else rec.media_path
            item = PlanItem(record=rec, url_path=url_path, dest=self._dest_for(rec))
            if parsed.kind not in self.cfg.kinds:
                item.skipped = f"filtered ({parsed.kind})"
                items.append(item)
                continue
            group = rec.storage
            if group not in mount_for_group:
                probe = self.http.probe_storage(url_path)
                if probe is None:
                    item.skipped = "unreachable (probe failed)"
                    items.append(item)
                    continue
                mount_for_group[group] = probe[0]
                log.info("store group %s serves at /v2 storage=%d", group, probe[0])
            item.storage_idx = mount_for_group[group]
            size = rec.size or 0
            if size <= 0:
                size = self.http.head_size(item.storage_idx, url_path) or 0
            item.size = size
            if size and self.db.is_offloaded(self.cfg.camera_folder, rec.media_path, size):
                # user-facing label is ONE thing; the reason lives in the log
                log.debug("%s: skip (transfer history says done)", rec.name)
                item.skipped = "already on disk"
            elif item.dest.exists() and item.dest.stat().st_size == size:
                log.debug("%s: skip (matching file already at destination)", rec.name)
                item.skipped = "already on disk"
            items.append(item)
        return items

    # -- execution -----------------------------------------------------------

    def run(
        self,
        items: list[PlanItem],
        progress: Callable[[SessionProgress], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        session_id: int | None = None,
    ) -> SessionProgress:
        todo = [i for i in items if not i.skipped]
        prog = SessionProgress(
            total_files=len(todo),
            total_bytes=sum(i.size or 0 for i in todo),
        )
        own_session = session_id is None
        if own_session:
            session_id = self.db.start_session(
                self.cfg.camera_folder, log_path=self.cfg.log_path
            )
        prog.session_id = session_id
        try:
            for item in todo:
                if cancelled and cancelled():
                    prog.failures.append("cancelled")
                    break
                rec = item.record
                parsed = naming.parse_name(rec.name)
                prog.current_name = rec.name
                prog.current_total = item.size or 0
                prog.current_done = 0
                tid = self.db.start_transfer(
                    session_id, self.cfg.camera_folder, rec.media_path, rec.name,
                    parsed.kind, item.size or 0,
                    {0: "sd", 1: "internal", None: None}.get(rec.storage),
                    str(item.dest),
                    dest_name=item.dest.name,
                )

                def on_bytes(done: int, total: int) -> None:
                    prog.current_done = done
                    if progress:
                        progress(prog)

                try:
                    result = self.http.download(
                        item.storage_idx or 0, item.url_path, item.dest,
                        expected_size=item.size or None,
                        progress=on_bytes, cancelled=cancelled,
                        on_network_lost=self.on_network_lost,
                    )
                    verified = item.size is None or result.size == item.size
                    self.db.finish_transfer(tid, "done", result.hash_hex, verified)
                    prog.done_files += 1
                    prog.done_bytes += result.size
                    prog.results.append(
                        {
                            "original": rec.name,
                            "saved_as": item.dest.name,
                            "dest_path": str(item.dest),
                            "size": result.size,
                            "hash": result.hash_hex,
                            "storage": {0: "sd", 1: "internal", None: None}.get(rec.storage),
                            "kind": parsed.kind,
                            "verified": verified,
                            "speed_mbs": (result.size / result.seconds / 1e6)
                            if result.seconds else None,
                        }
                    )
                    rate = result.size / result.seconds / 1e6 if result.seconds else 0
                    renamed = f" -> {item.dest.name}" if item.dest.name != rec.name else ""
                    log.info("done %s%s (%d B, %.1f MB/s%s)", rec.name, renamed, result.size,
                             rate, f", resumed@{result.resumed_from}" if result.resumed_from else "")
                except DownloadError as e:
                    self.db.finish_transfer(tid, "failed", None, False)
                    prog.failures.append(f"{rec.name}: {e}")
                    log.error("FAILED %s: %s", rec.name, e)
                    if str(e) == "cancelled":
                        break
                if progress:
                    progress(prog)
        finally:
            if own_session:
                self.db.finish_session(session_id, prog.done_files, prog.done_bytes,
                                       note="; ".join(prog.failures[:5]))
        return prog
