"""CompositePack manifest reassembly + decode (osmosis CameraSession port).

The 0x00/0x26 file-list request streams back as chunked 0x00/0x27 frames. Each
data-chunk payload is [10-byte sub-header][chunk]; strip the sub-header, concat
in arrival order. The reassembled manifest opens with a u32-LE file count, then
one length-delimited record per file, anchored on the DCIM media-path field.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass

from ..duml.crc import crc8, crc16

log = logging.getLogger("osmo.manifest")

VIDEO_MARKER = b"\x03\xff\x19\x06"
VIDEO_HANDLE_BASE = 0x40000000


@dataclass
class MediaRecord:
    folder: str
    name: str  # base.EXT
    media_path: str  # DCIM/<folder>/<base>, no extension
    thumb_path: str | None
    handle: int
    size: int
    storage: int | None = None  # 0 = SD, 1 = internal (when known)
    star: bool = False

    @property
    def ext(self) -> str:
        return self.name.rsplit(".", 1)[-1].upper() if "." in self.name else ""

    @property
    def is_video(self) -> bool:
        return self.ext in ("MP4", "MOV")


def iter_frames(raw: bytes):
    """Yield (cmd_set, cmd_id, payload) for every CRC-valid DUML frame in raw,
    scanning one byte at a time (frames can be tunnelled / length is 10-bit)."""
    i = 0
    n = len(raw)
    while i + 13 <= n:
        if raw[i] != 0x55:
            i += 1
            continue
        length = (raw[i + 1] | (raw[i + 2] << 8)) & 0x3FF
        ver = raw[i + 2] >> 2
        if ver != 1 or length < 13 or i + length > n:
            i += 1
            continue
        if crc8(raw[i : i + 3]) != raw[i + 3]:
            i += 1
            continue
        body = raw[i : i + length - 2]
        want = raw[i + length - 2] | (raw[i + length - 1] << 8)
        if crc16(body) != want:
            i += 1
            continue
        yield raw[i + 9], raw[i + 10], raw[i + 11 : i + length - 2]
        i += 1


def reassemble(raw: bytes, request_ctr: int | None = None) -> bytes:
    """Concatenate the 0x00/0x27 data chunks (sub-header stripped) in arrival order.

    Selects chunks by DUML command (0x00/0x27) AND the 4A 01 data-chunk prefix.
    If request_ctr is given, keep only chunks whose sub-header byte 4 echoes it
    (per-store split).
    """
    out = bytearray()
    for cmd_set, cmd_id, pl in iter_frames(raw):
        if cmd_set != 0x00 or cmd_id != 0x27:
            continue
        if len(pl) <= 10 or pl[0] != 0x4A or pl[1] != 0x01:
            continue
        if request_ctr is not None and pl[4] != request_ctr:
            continue
        out += pl[10:]
    return bytes(out)


def _read_path(buf: bytes, i: int, sub: int, prefix: bytes) -> tuple[bytes, int] | None:
    """Path TLV: 1a [total] 00 00 00 <sub> <ascii>, ascii = total-6 bytes."""
    if i + 6 > len(buf) or buf[i] != 0x1A:
        return None
    if buf[i + 2 : i + 5] != b"\x00\x00\x00" or buf[i + 5] != sub:
        return None
    slen = buf[i + 1] - 6
    if slen < len(prefix):
        return None
    value = buf[i + 6 : i + 6 + slen]
    if len(value) != slen or not value.startswith(prefix):
        return None
    return value, i + 6 + slen


def decode(buf: bytes) -> list[MediaRecord]:
    """Decode the reassembled manifest into media records, anchored on each
    DCIM media-path field, scoping every other field to that record's window."""
    medias: list[tuple[int, int, str]] = []
    i = 0
    while i < len(buf):
        f = _read_path(buf, i, sub=1, prefix=b"DCIM/")
        if f:
            medias.append((i, f[1], f[0].decode("ascii", "replace")))
            i = f[1]
        else:
            i += 1

    files: list[MediaRecord] = []
    for k, (pos, end, path) in enumerate(medias):
        lo = medias[k - 1][1] if k else 0
        hi = medias[k + 1][0] if k + 1 < len(medias) else len(buf)
        parts = path.split("/")
        folder = parts[1] if len(parts) > 1 else ""
        base = path.rsplit("/", 1)[-1]

        # extension from the 0d filename field within this window
        ext = ""
        j = lo
        needle = (base + ".").encode()
        while j < hi - 2:
            if buf[j] == 0x0D and buf[j + 2 : j + 2 + len(needle)] == needle:
                fld_end = j + 2 + buf[j + 1]
                ext = buf[j + 2 + len(needle) : fld_end].decode("ascii", "replace").upper()
                break
            j += 1

        # thumb path (sub=2) — search from the end of THIS record's media
        # path, not from `lo`. Both documented field orders put the thumb TLV
        # directly after the media path (`head enum filename media thumb` on
        # Action 6 / Nano, `head media thumb enum filename` on the CAM_ family),
        # so a scan starting at `lo` returns the previous record's thumb.
        thumb = None
        t = end
        while t < hi:
            tf = _read_path(buf, t, sub=2, prefix=b"MISC/")
            if tf:
                thumb = tf[0].decode("ascii", "replace")
                break
            t += 1

        # Anchor on the constant `19 06` pair common to BOTH record shapes —
        # videos write `03 ff 19 06`, stills a shorter `[ff|fe] 19 06`. The
        # handle is u32-LE @ (19 06 − 10) and the byte size u32-LE @ (19 06 − 14)
        # for both (ground-truthed on a Pocket 4 Pro: seq 5/6/7 -> 0x40100140/
        # 180/1c0, step 0x40, with matching file sizes). Require the fe/ff
        # prefix so a stray `19 06` in path/data can't be mistaken for a marker.
        handle = size = 0
        star = False
        p = lo
        while p < pos - 1:
            if buf[p] == 0x19 and buf[p + 1] == 0x06 and p >= 1 and buf[p - 1] in (0xFE, 0xFF):
                if p - 10 >= 0:
                    handle = struct.unpack_from("<I", buf, p - 10)[0]
                if p - 14 >= 0:
                    size = struct.unpack_from("<I", buf, p - 14)[0]
                # starTag: u8 at `[fe|ff] 19 06` + 9 — the `fe|ff` sits at
                # p - 1, so the flag is p + 8. Only bodies that put the enum
                # block after the marker carry it there: in the CAM_ order the
                # media path TLV starts at p + 7, making p + 8 its length byte,
                # and reading that as a flag invents favourites.
                if p + 7 < len(buf) and buf[p + 7] != 0x1A:
                    star_off = p + 8
                    if star_off < len(buf) and buf[star_off] in (0, 1):
                        star = buf[star_off] == 1
                break
            p += 1
        else:
            # Bodies whose stills carry no `[fe|ff]`-prefixed marker (Pocket 3)
            # still write the `19 06` pair at a fixed spot: 13 B before the
            # media path's ascii, i.e. 7 B before its TLV. Same -10/-14 rule.
            q = pos - 7
            if q >= 14 and buf[q] == 0x19 and buf[q + 1] == 0x06:
                handle = struct.unpack_from("<I", buf, q - 10)[0]
                size = struct.unpack_from("<I", buf, q - 14)[0]

        name = f"{base}.{ext}" if ext else base
        files.append(
            MediaRecord(
                folder=folder,
                name=name,
                media_path=path,
                thumb_path=thumb,
                handle=handle,
                size=size,
                star=star,
            )
        )
    return files


def header_count(buf: bytes) -> int:
    return struct.unpack_from("<I", buf, 0)[0] if len(buf) >= 4 else 0


def list_answered(raw: bytes) -> bool:
    """True if the camera sent ANY 0x00/0x27 list frame (start/end markers
    included) — distinguishes 'camera is empty' from 'query never answered'."""
    return any(cs == 0x00 and ci == 0x27 for cs, ci, _pl in iter_frames(raw))
