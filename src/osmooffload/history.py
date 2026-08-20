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
    files_done INTEGER NOT NULL DEFAULT 0,
    bytes_done INTEGER NOT NULL DEFAULT 0,
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
    name: str
    kind: str
    size: int
    dest_path: str
    status: str
    verified: bool
    finished_at: float | None


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
            self._db.commit()

    def close(self) -> None:
        self._db.close()

    # -- sessions ------------------------------------------------------------

    def start_session(self, camera_id: str, transport: str = "wifi") -> int:
        cur = self._db.execute(
            "INSERT INTO sessions(camera_id, started_at, transport) VALUES(?,?,?)",
            (camera_id, time.time(), transport),
        )
        self._db.commit()
        return cur.lastrowid

    def finish_session(self, session_id: int, files_done: int, bytes_done: int, note: str = "") -> None:
        self._db.execute(
            "UPDATE sessions SET finished_at=?, files_done=?, bytes_done=?, note=? WHERE id=?",
            (time.time(), files_done, bytes_done, note, session_id),
        )
        self._db.commit()

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
    ) -> int:
        cur = self._db.execute(
            "INSERT INTO transfers(session_id, camera_id, media_path, name, kind, size, "
            "storage, dest_path, started_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (session_id, camera_id, media_path, name, kind, size, storage, dest_path, time.time()),
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
            "SELECT id, camera_id, media_path, name, kind, size, dest_path, status, "
            "verified, finished_at FROM transfers "
        )
        args: tuple = ()
        if camera_id:
            q += "WHERE camera_id=? "
            args = (camera_id,)
        q += "ORDER BY started_at DESC LIMIT ?"
        rows = self._db.execute(q, args + (limit,)).fetchall()
        return [TransferRow(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], bool(r[8]), r[9]) for r in rows]
