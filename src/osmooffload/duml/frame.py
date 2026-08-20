"""DUML frame encode/decode (SOF 0x55), per osmosis MEDIA_PROTOCOL.md §3.

Layout:
  off sz  field
  0   1   SOF = 0x55
  1   1   len_lo                 total_len = 13 + len(payload), incl. both CRCs
  2   1   (ver<<2)|len_hi[9:8]   ver=1 -> 0x04 when len < 256
  3   1   CRC8 over bytes[0:3]
  4   1   sender   ((id<<5)|type)
  5   1   receiver
  6   2   msg id / seq           encoded LE; the camera echoes the raw bytes
  8   1   cmd flags              0x40 request, 0xC0 response, 0x00 push/notify
  9   1   CmdSet
  10  1   CmdId
  11  N   payload
  +N  2   CRC16 over bytes[0:11+N], appended LE
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .crc import crc8, crc16

SOF = 0x55
HEADER_LEN = 11
OVERHEAD = 13  # header + crc16

FLAGS_REQUEST = 0x40
FLAGS_RESPONSE = 0xC0
FLAGS_PUSH = 0x00


@dataclass(frozen=True)
class DumlFrame:
    sender: int
    receiver: int
    msg_id: int  # u16, encoded little-endian
    flags: int
    cmd_set: int
    cmd_id: int
    payload: bytes = b""

    def encode(self) -> bytes:
        total = OVERHEAD + len(self.payload)
        if total > 0x3FF:
            raise ValueError(f"frame too long: {total}")
        head = bytes([SOF, total & 0xFF, 0x04 | ((total >> 8) & 0x03)])
        head += bytes([crc8(head)])
        body = bytes(
            [
                self.sender,
                self.receiver,
                self.msg_id & 0xFF,
                (self.msg_id >> 8) & 0xFF,
                self.flags,
                self.cmd_set,
                self.cmd_id,
            ]
        ) + self.payload
        frame = head + body
        c16 = crc16(frame)
        return frame + bytes([c16 & 0xFF, (c16 >> 8) & 0xFF])

    @property
    def is_request(self) -> bool:
        return self.flags & 0xE0 == FLAGS_REQUEST

    @property
    def is_response(self) -> bool:
        return self.flags & 0xE0 == FLAGS_RESPONSE

    @property
    def key(self) -> tuple[int, int]:
        return (self.cmd_set, self.cmd_id)

    def __repr__(self) -> str:  # compact hex-forward repr for logs
        return (
            f"DUML({self.sender:02x}->{self.receiver:02x} id={self.msg_id:04x} "
            f"fl={self.flags:02x} {self.cmd_set:02x}/{self.cmd_id:02x} "
            f"pl={self.payload.hex() or '-'})"
        )

    @staticmethod
    def decode(data: bytes) -> "DumlFrame":
        """Decode exactly one frame from `data` (must be the exact frame bytes)."""
        frame, used = DumlFrame.decode_prefix(data)
        if used != len(data):
            raise ValueError(f"trailing bytes after frame: {len(data) - used}")
        return frame

    @staticmethod
    def decode_prefix(data: bytes) -> tuple["DumlFrame", int]:
        """Decode one frame from the start of `data`; returns (frame, bytes_used)."""
        if len(data) < OVERHEAD:
            raise ValueError("short frame")
        if data[0] != SOF:
            raise ValueError("bad SOF")
        if crc8(data[0:3]) != data[3]:
            raise ValueError("bad header CRC8")
        total = data[1] | ((data[2] & 0x03) << 8)
        if total < OVERHEAD or len(data) < total:
            raise ValueError("truncated frame")
        c16 = crc16(data[0 : total - 2])
        if c16 != (data[total - 2] | (data[total - 1] << 8)):
            raise ValueError("bad CRC16")
        return (
            DumlFrame(
                sender=data[4],
                receiver=data[5],
                msg_id=data[6] | (data[7] << 8),
                flags=data[8],
                cmd_set=data[9],
                cmd_id=data[10],
                payload=bytes(data[11 : total - 2]),
            ),
            total,
        )


@dataclass
class FrameParser:
    """Byte-stream reassembler: feed arbitrary chunks, yields complete frames.

    Resyncs on garbage by skipping to the next plausible SOF, so a corrupted
    or partial chunk can't wedge the stream.
    """

    _buf: bytearray = field(default_factory=bytearray)

    def feed(self, chunk: bytes) -> list[DumlFrame]:
        self._buf.extend(chunk)
        out: list[DumlFrame] = []
        while True:
            # hunt for SOF
            while self._buf and self._buf[0] != SOF:
                del self._buf[0]
            if len(self._buf) < OVERHEAD:
                return out
            # header sanity before length trust
            if crc8(bytes(self._buf[0:3])) != self._buf[3]:
                del self._buf[0]  # false SOF, resync
                continue
            total = self._buf[1] | ((self._buf[2] & 0x03) << 8)
            if total < OVERHEAD:
                del self._buf[0]
                continue
            if len(self._buf) < total:
                return out  # wait for more bytes
            try:
                frame, used = DumlFrame.decode_prefix(bytes(self._buf[:total]))
            except ValueError:
                del self._buf[0]  # bad CRC16 -> resync
                continue
            out.append(frame)
            del self._buf[:used]
