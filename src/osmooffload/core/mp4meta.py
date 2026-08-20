"""Minimal MP4 box parser: duration + video resolution from local files.

Reads only the box tree (moov/mvhd for duration, trak/tkhd for dimensions) —
no frame data, so it's O(header) regardless of file size.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}


@dataclass
class Mp4Meta:
    duration_s: float | None
    width: int | None
    height: int | None

    @property
    def resolution(self) -> str | None:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return None


def _iter_boxes(f, start: int, end: int, depth: int = 0):
    pos = start
    while pos + 8 <= end and depth < 8:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return
        size = struct.unpack(">I", head[:4])[0]
        btype = head[4:8]
        header = 8
        if size == 1:
            big = f.read(8)
            if len(big) < 8:
                return
            size = struct.unpack(">Q", big)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header:
            return
        yield btype, pos + header, pos + size
        pos += size


def _parse_mvhd(f, start: int) -> float | None:
    f.seek(start)
    data = f.read(28)
    if len(data) < 20:
        return None
    version = data[0]
    if version == 1:
        if len(data) < 28:
            return None
        timescale = struct.unpack(">I", data[20:24])[0]
        duration = struct.unpack(">Q", data[24:32] if len(data) >= 32 else f.read(8))[0]
    else:
        timescale = struct.unpack(">I", data[12:16])[0]
        duration = struct.unpack(">I", data[16:20])[0]
    if not timescale or duration in (0, 0xFFFFFFFF):
        return None
    return duration / timescale


def _parse_tkhd(f, start: int, end: int) -> tuple[int, int]:
    # width/height are 16.16 fixed in the LAST 8 bytes of the box
    f.seek(end - 8)
    data = f.read(8)
    if len(data) < 8:
        return 0, 0
    w = struct.unpack(">I", data[0:4])[0] >> 16
    h = struct.unpack(">I", data[4:8])[0] >> 16
    return w, h


def probe(path: Path) -> Mp4Meta:
    duration = None
    width = height = 0
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            def walk(start: int, end: int, depth: int) -> None:
                nonlocal duration, width, height
                for btype, s, e in list(_iter_boxes(f, start, end, depth)):
                    if btype == b"mvhd":
                        duration = _parse_mvhd(f, s) or duration
                    elif btype == b"tkhd":
                        w, h = _parse_tkhd(f, s, e)
                        # keep the largest track (video beats audio's 0x0)
                        if w * h > width * height:
                            width, height = w, h
                    elif btype in _CONTAINERS:
                        walk(s, e, depth + 1)

            walk(0, size, 0)
    except OSError:
        pass
    return Mp4Meta(duration_s=duration, width=width or None, height=height or None)
