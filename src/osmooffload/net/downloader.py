"""HTTP media downloader for the camera's /v2 endpoint.

lighttpd/1.4.55 on 192.168.2.1: `GET /v2?storage={0|1}&path=<path>`, HEAD gives
Content-Length, Range gives 206 partials — so every download is resumable.

Downloads write to `<dest>.part` and rename into place only after the size
checks out; the receive-hash (blake2b-128) is computed on the stream (prefix
re-hashed from disk on resume) so verification costs no second read.
Transient mid-transfer 404/500s are documented camera behavior — retry with
resume, don't treat them as missing files.
"""

from __future__ import annotations

import hashlib
import http.client
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote

log = logging.getLogger("osmo.dl")

ProgressCb = Callable[[int, int], None]  # (bytes_done, bytes_total)


class DownloadError(Exception):
    pass


@dataclass
class DownloadResult:
    path: Path
    size: int
    hash_hex: str
    seconds: float
    resumed_from: int


class CameraHttp:
    def __init__(self, host: str = "192.168.2.1", timeout: float = 15.0):
        self.host = host
        self.timeout = timeout
        self._conn: http.client.HTTPConnection | None = None

    def _connect(self) -> http.client.HTTPConnection:
        if self._conn is None:
            self._conn = http.client.HTTPConnection(self.host, 80, timeout=self.timeout)
        return self._conn

    def _reset(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def close(self) -> None:
        self._reset()

    @staticmethod
    def media_url(storage: int, path: str) -> str:
        return f"/v2?storage={storage}&path={quote(path, safe='/')}"

    def head_size(self, storage: int, path: str) -> int | None:
        """Content-Length via HEAD, or None if the camera refuses (reset/404)."""
        url = self.media_url(storage, path)
        try:
            conn = self._connect()
            conn.request("HEAD", url)
            resp = conn.getresponse()
            resp.read()
            if resp.status == 200:
                cl = resp.getheader("Content-Length")
                return int(cl) if cl is not None else None
            return None
        except (OSError, http.client.HTTPException):
            self._reset()
            return None

    def probe_storage(self, path: str, candidates: tuple[int, ...] = (1, 0)) -> tuple[int, int] | None:
        """HEAD the file at each storage id; return (storage, size) of the winner."""
        for storage in candidates:
            size = self.head_size(storage, path)
            if size is not None:
                return storage, size
        return None

    def fetch_small(self, storage: int, path: str, limit: int = 4 << 20) -> bytes | None:
        """Whole-body GET for small files (thumbnails). None on any failure."""
        url = self.media_url(storage, path)
        try:
            conn = self._connect()
            conn.request("GET", url)
            resp = conn.getresponse()
            if resp.status != 200:
                resp.read()
                return None
            data = resp.read(limit + 1)
            if len(data) > limit:
                return None
            return data
        except (OSError, http.client.HTTPException):
            self._reset()
            return None

    def download(
        self,
        storage: int,
        path: str,
        dest: Path,
        expected_size: int | None = None,
        progress: ProgressCb | None = None,
        max_retries: int = 6,
        cancelled: Callable[[], bool] | None = None,
        on_network_lost: Callable[[], None] | None = None,
    ) -> DownloadResult:
        """Resumable download to `dest` (via `dest.part`), returns hash + timing."""
        t0 = time.monotonic()
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        url = self.media_url(storage, path)

        if expected_size is None:
            expected_size = self.head_size(storage, path)

        # resume state: hash the existing prefix from disk
        h = hashlib.blake2b(digest_size=16)
        pos = 0
        if part.exists():
            with part.open("rb") as f:
                while chunk := f.read(1 << 20):
                    h.update(chunk)
                    pos += len(chunk)
            if expected_size is not None and pos > expected_size:
                log.warning("stale .part larger than source (%d > %d) — restarting", pos, expected_size)
                part.unlink()
                h = hashlib.blake2b(digest_size=16)
                pos = 0
        resumed_from = pos

        retries = 0
        while True:
            if cancelled and cancelled():
                raise DownloadError("cancelled")
            if expected_size is not None and pos >= expected_size:
                break
            try:
                conn = self._connect()
                headers = {"Range": f"bytes={pos}-"} if pos else {}
                conn.request("GET", url, headers=headers)
                resp = conn.getresponse()
                if resp.status == 416:
                    # Range past end-of-file: the .part is already complete (or
                    # a stale oversize leftover). Re-HEAD; finalize if it matches,
                    # else discard and restart from zero.
                    resp.read()
                    self._reset()
                    real = self.head_size(storage, path)
                    if real is not None and pos == real:
                        expected_size = real
                        break  # complete — finalize below
                    log.warning("416 on %s (pos=%d real=%s) — restarting from 0",
                                dest.name, pos, real)
                    part.unlink(missing_ok=True)
                    h = hashlib.blake2b(digest_size=16)
                    pos = 0
                    if real is not None:
                        expected_size = real
                    continue
                if resp.status not in (200, 206):
                    resp.read()
                    raise DownloadError(f"HTTP {resp.status}")
                if resp.status == 200 and pos:
                    # server ignored Range — start over
                    log.warning("Range ignored; restarting %s", dest.name)
                    h = hashlib.blake2b(digest_size=16)
                    pos = 0
                    part.unlink(missing_ok=True)
                if expected_size is None:
                    cl = resp.getheader("Content-Length")
                    if resp.status == 200 and cl:
                        expected_size = int(cl)
                with part.open("ab" if pos else "wb") as f:
                    while True:
                        if cancelled and cancelled():
                            resp.close()
                            raise DownloadError("cancelled")
                        chunk = resp.read(1 << 18)
                        if not chunk:
                            break
                        f.write(chunk)
                        h.update(chunk)
                        pos += len(chunk)
                        if progress:
                            progress(pos, expected_size or 0)
                if expected_size is None or pos >= expected_size:
                    break
                # short read — fall through to retry with resume
                raise DownloadError(f"short read at {pos}/{expected_size}")
            except DownloadError as e:
                if str(e) == "cancelled":
                    raise
                retries += 1
                self._reset()
                if retries > max_retries:
                    raise
                log.info("retrying %s (%s), attempt %d, resume at %d", dest.name, e, retries, pos)
                time.sleep(min(2.0 * retries, 8.0))
            except (OSError, http.client.HTTPException) as e:
                retries += 1
                self._reset()
                if retries > max_retries:
                    raise DownloadError(f"{type(e).__name__}: {e}") from e
                log.info("retrying %s (%s: %s), attempt %d, resume at %d",
                         dest.name, type(e).__name__, e, retries, pos)
                # network-level failure: give the caller a chance to rejoin
                # the camera AP before the next resume attempt
                if on_network_lost and retries >= 2 and isinstance(e, OSError):
                    try:
                        on_network_lost()
                    except Exception as re:
                        log.debug("network-recovery hook failed: %s", re)
                time.sleep(min(2.0 * retries, 8.0))

        if expected_size is not None and pos != expected_size:
            raise DownloadError(f"size mismatch: got {pos}, expected {expected_size}")
        part.replace(dest)
        return DownloadResult(
            path=dest, size=pos, hash_hex=h.hexdigest(),
            seconds=time.monotonic() - t0, resumed_from=resumed_from,
        )
