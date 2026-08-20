"""Transfer history — the app's persistent ledger (SQLite, stdlib).

Answers "has this camera file been offloaded already?" and "when did which
file land where?", and stores the receive-hash for verified transfers.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    camera_id TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    transport TEXT NOT NULL DEFAULT 'wifi',
    action TEXT NOT NULL DEFAULT 'transfer',   -- transfer|refresh|delete|usb
    files_done INTEGER NOT NULL DEFAULT 0,
    bytes_done INTEGER NOT NULL DEFAULT 0,
    seconds REAL,
    rate_mbs REAL,
    log_path TEXT,
    report_path TEXT,
    outcome TEXT,                              -- ok|partial|failed|empty
    note TEXT
);
CREATE TABLE IF NOT EXISTS transfers (
    id INTEGER PRIMARY KEY,
    session_id INTEGER REFERENCES sessions(id),
    camera_id TEXT NOT NULL,
    media_path TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL,
    storage TEXT,
    hash TEXT,
    dest_name TEXT,
    dest_path TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|done|failed
    verified INTEGER NOT NULL DEFAULT 0,
    deleted_from_camera_at REAL
);
CREATE INDEX IF NOT EXISTS idx_transfers_lookup
    ON transfers(camera_id, media_path, size, status);
CREATE INDEX IF NOT EXISTS idx_transfers_time ON transfers(started_at);
"""


@dataclass
class TransferRow:
    id: int
    camera_id: str
    media_path: str
    name: str  # original camera-side filename
    kind: str
    size: int
    dest_name: str | None  # what it was renamed to on disk
    dest_path: str
    status: str
    verified: bool
    finished_at: float | None
    log_path: str | None = None  # the session log this transfer belongs to


@dataclass
class SessionRow:
    id: int
    camera_id: str
    action: str
    started_at: float
    finished_at: float | None
    files_done: int
    bytes_done: int
    seconds: float | None
    rate_mbs: float | None
    outcome: str | None
    report_path: str | None
    log_path: str | None
    note: str | None


class HistoryDB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # Accessed from worker threads (asyncio.to_thread / GUI workers).
        # Callers are sequential today; _lock guards init and any future
        # concurrent paths (GUI must serialize through one worker or _lock).
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
            # migrations for DBs created before these columns existed
            for stmt in (
                "ALTER TABLE transfers ADD COLUMN dest_name TEXT",
                "ALTER TABLE sessions ADD COLUMN log_path TEXT",
                "ALTER TABLE sessions ADD COLUMN action TEXT NOT NULL DEFAULT 'transfer'",
                "ALTER TABLE sessions ADD COLUMN seconds REAL",
                "ALTER TABLE sessions ADD COLUMN rate_mbs REAL",
                "ALTER TABLE sessions ADD COLUMN report_path TEXT",
                "ALTER TABLE sessions ADD COLUMN outcome TEXT",
            ):
                try:
                    self._db.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # column already there
            self._db.commit()

    def close(self) -> None:
        self._db.close()

    # -- sessions ------------------------------------------------------------

    def start_session(self, camera_id: str, transport: str = "wifi",
                      action: str = "transfer", log_path: str | None = None) -> int:
        cur = self._db.execute(
            "INSERT INTO sessions(camera_id, started_at, transport, action, log_path) "
            "VALUES(?,?,?,?,?)",
            (camera_id, time.time(), transport, action, log_path),
        )
        self._db.commit()
        return cur.lastrowid

    def finish_session(self, session_id: int, files_done: int, bytes_done: int,
                       note: str = "", seconds: float | None = None,
                       rate_mbs: float | None = None, outcome: str | None = None,
                       report_path: str | None = None) -> None:
        self._db.execute(
            "UPDATE sessions SET finished_at=?, files_done=?, bytes_done=?, note=?, "
            "seconds=?, rate_mbs=?, outcome=?, report_path=? WHERE id=?",
            (time.time(), files_done, bytes_done, note, seconds, rate_mbs,
             outcome, report_path, session_id),
        )
        self._db.commit()

    def set_session_report(self, session_id: int, report_path: str) -> None:
        self._db.execute(
            "UPDATE sessions SET report_path=? WHERE id=?", (report_path, session_id)
        )
        self._db.commit()

    def recent_sessions(self, limit: int = 200, camera_id: str | None = None) -> list[SessionRow]:
        q = (
            "SELECT id, camera_id, action, started_at, finished_at, files_done, bytes_done, "
            "seconds, rate_mbs, outcome, report_path, log_path, note FROM sessions "
        )
        args: tuple = ()
        if camera_id:
            q += "WHERE camera_id=? "
            args = (camera_id,)
        q += "ORDER BY started_at DESC LIMIT ?"
        rows = self._db.execute(q, args + (limit,)).fetchall()
        return [SessionRow(*r) for r in rows]

    # -- transfers -----------------------------------------------------------

    def is_offloaded(self, camera_id: str, media_path: str, size: int) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM transfers WHERE camera_id=? AND media_path=? AND size=? "
            "AND status='done' LIMIT 1",
            (camera_id, media_path, size),
        ).fetchone()
        return row is not None

    def start_transfer(
        self, session_id: int, camera_id: str, media_path: str, name: str,
        kind: str, size: int, storage: str | None, dest_path: str,
        dest_name: str | None = None,
    ) -> int:
        cur = self._db.execute(
            "INSERT INTO transfers(session_id, camera_id, media_path, name, kind, size, "
            "storage, dest_name, dest_path, started_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (session_id, camera_id, media_path, name, kind, size, storage,
             dest_name, dest_path, time.time()),
        )
        self._db.commit()
        return cur.lastrowid

    def finish_transfer(self, transfer_id: int, status: str, hash_hex: str | None, verified: bool) -> None:
        self._db.execute(
            "UPDATE transfers SET finished_at=?, status=?, hash=?, verified=? WHERE id=?",
            (time.time(), status, hash_hex, int(verified), transfer_id),
        )
        self._db.commit()

    def mark_deleted_from_camera(self, camera_id: str, media_path: str) -> None:
        self._db.execute(
            "UPDATE transfers SET deleted_from_camera_at=? WHERE camera_id=? AND media_path=? "
            "AND status='done'",
            (time.time(), camera_id, media_path),
        )
        self._db.commit()

    def deletable(self, camera_id: str, media_path: str, size: int) -> bool:
        """True only for files with a completed, verified transfer on record."""
        row = self._db.execute(
            "SELECT 1 FROM transfers WHERE camera_id=? AND media_path=? AND size=? "
            "AND status='done' AND verified=1 LIMIT 1",
            (camera_id, media_path, size),
        ).fetchone()
        return row is not None

    def recent(self, limit: int = 200, camera_id: str | None = None) -> list[TransferRow]:
        q = (
            "SELECT t.id, t.camera_id, t.media_path, t.name, t.kind, t.size, t.dest_name, "
            "t.dest_path, t.status, t.verified, t.finished_at, s.log_path "
            "FROM transfers t LEFT JOIN sessions s ON t.session_id = s.id "
        )
        args: tuple = ()
        if camera_id:
            q += "WHERE t.camera_id=? "
            args = (camera_id,)
        q += "ORDER BY t.started_at DESC LIMIT ?"
        rows = self._db.execute(q, args + (limit,)).fetchall()
        return [
            TransferRow(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], bool(r[9]),
                        r[10], r[11])
            for r in rows
        ]
