"""DUML-over-UDP datalink for Osmo cameras (osmosis DumlTransport port).

Wire layers per packet: [8B udp hdr][12B routing hdr][DUML frame].

Sliding-window sequenced transport. The load-bearing subtlety (two osmosis
regressions): the routing header's ackSeq and ownSeq BOTH live in the app's
own command-seq space — ackSeq = ownSeq - 8, never the camera's telemetry seq.
Get it wrong and the receive window silently drops writes while reads still
work. Our own send seq advances +8 per packet, starting at camera_channel + 8.
"""

from __future__ import annotations

import logging
import random
import socket
import time

from ..duml import DumlFrame
from ..duml.frame import crc8 as _c8  # noqa: F401  (not used; keep import list tidy)

log = logging.getLogger("osmo.datalink")

PKT_HANDSHAKE = 0x00
PKT_TELEMETRY = 0x01
PKT_ACK = 0x04
PKT_COMMAND = 0x05

# The handshake (SYN) frame the official app offers: a proposed base sequence in
# bytes[0:2] (LE) followed by verbatim window/MTU params (window 100, MTU 0x05c0).
# Byte-for-byte the osmosis DumlSession.handshakeFrame() 40-byte frame. We derive
# the tail from that frame so the two can never drift: a hand-copied tail had
# dropped 4 bytes, producing a 38-byte SYN the camera silently ignored.
_REF_HANDSHAKE_FRAME = bytes.fromhex(
    "000064006400c005140000640000019001c005140000640014006400c00514000064000101040102"
)
HANDSHAKE_TAIL = _REF_HANDSHAKE_FRAME[2:]  # payload = baseSeq_LE(2B) + HANDSHAKE_TAIL


def _assemble_handshake(base_seq: int) -> bytes:
    return bytes([base_seq & 0xFF, (base_seq >> 8) & 0xFF]) + HANDSHAKE_TAIL


# Import-time guard: assembling with the reference base must reproduce the frame.
assert (
    _assemble_handshake(_REF_HANDSHAKE_FRAME[0] | (_REF_HANDSHAKE_FRAME[1] << 8))
    == _REF_HANDSHAKE_FRAME
), "handshake assembly drifted from the reference frame"
assert len(_assemble_handshake(0x1234)) == 40, "handshake SYN payload must be 40 bytes"


def udp_header(pkt_type: int, payload_len: int, session_id: int, seq: int) -> bytes:
    total = 8 + payload_len
    w0 = (1 << 15) | (total & 0x3FFF)
    b = bytes(
        [
            w0 & 0xFF, (w0 >> 8) & 0xFF,
            session_id & 0xFF, (session_id >> 8) & 0xFF,
            seq & 0xFF, (seq >> 8) & 0xFF,
            pkt_type & 0xFF,
        ]
    )
    xor = 0
    for x in b:
        xor ^= x
    return b + bytes([xor])


def routing_header(seq: int, cmd_counter: int, drone: bool = False) -> bytes:
    ack = (seq - 8) & 0xFFFF
    return bytes(
        [
            ack & 0xFF, (ack >> 8) & 0xFF,
            seq & 0xFF, (seq >> 8) & 0xFF,
            0, 0, 0, 0, cmd_counter & 0xFF, 0x01,
            0x60 if drone else 0x00, 0x00,
        ]
    )


class DatalinkTransport:
    def __init__(self, ip: str, port: int, *, bind_local: bool = False, drone: bool = False):
        self.ip = ip
        self.port = port
        self._drone = drone
        self._sock: socket.socket | None = None
        self._bind_local = bind_local
        self.session_id = 0
        self.base_seq = 0xB887
        self.cam_channel = 0xB887
        self._udp_seq = 0
        self._duml_seq = 0xA000
        self._cmd_counter = 0
        self._peer_cursor = 0
        self.last_sent: bytes | None = None

    def open(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if self._bind_local:
            try:
                self._sock.bind(("", self.port))
            except OSError as e:
                log.warning("local udp/%d unavailable (%s) — ephemeral", self.port, e)
        self._sock.settimeout(0.2)
        self.session_id = random.randint(0x1000, 0xFFFE)
        self.base_seq = random.randint(0x1000, 0xF000) & 0xFFF8
        self.cam_channel = self.base_seq
        self._peer_cursor = 0

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            finally:
                self._sock = None

    # -- raw send/recv -------------------------------------------------------

    def _send(self, pkt: bytes) -> bool:
        self.last_sent = pkt
        try:
            self._sock.sendto(pkt, (self.ip, self.port))
            return True
        except OSError as e:  # ENETUNREACH on a dying AP must never crash the loop
            log.debug("sendto failed: %s", e)
            return False

    def _advance(self) -> None:
        self._udp_seq = (self._udp_seq + 8) & 0xFFFF

    def send_raw(self, pkt_type: int, payload: bytes) -> None:
        pkt = udp_header(pkt_type, len(payload), self.session_id, self._udp_seq) + payload
        if self._send(pkt):
            self._advance()

    def recv_all(self, duration: float) -> list[bytes]:
        out: list[bytes] = []
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            try:
                data = self._sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            out.append(data)
            if len(data) >= 10:
                ch = data[8] | (data[9] << 8)
                if ch != 0:
                    self.cam_channel = ch
            if len(data) == 34 and data[6] == PKT_TELEMETRY:
                self._peer_cursor = data[10] | (data[11] << 8)
        return out

    # -- handshake -----------------------------------------------------------

    def handshake(self, attempts: int = 20) -> bytes | None:
        payload = _assemble_handshake(self.base_seq)
        try:
            local = self._sock.getsockname()
        except OSError:
            local = ("?", -1)
        log.debug(
            "handshake SYN -> %s:%d  local=%s:%s (bind_local=%s) sid=0x%04x base=0x%04x "
            "len=%d hex=%s",
            self.ip, self.port, local[0], local[1], self._bind_local,
            self.session_id, self.base_seq, len(payload), payload.hex(),
        )
        got_any = 0
        for i in range(attempts):
            self.send_raw(PKT_HANDSHAKE, payload)
            got = self.recv_all(0.35)
            got_any += len(got)
            for r in got:
                if len(r) >= 8 and r[6] == PKT_HANDSHAKE:
                    log.debug("handshake attempt %d: type-0 REPLY (%d B) %s",
                              i, len(r), r[:32].hex())
                    return r
            if got:
                log.debug("handshake attempt %d: %d datagram(s), none type-0: %s",
                          i, len(got), [g[:20].hex() for g in got[:4]])
        log.debug("handshake: gave up after %d attempts, %d datagram(s) seen total",
                  attempts, got_any)
        return None

    def sync_seq_to_channel(self) -> None:
        self._udp_seq = (self.cam_channel + 8) & 0xFFFF

    # -- acks & commands -----------------------------------------------------

    def send_ack(self) -> None:
        def grp(v: int) -> bytes:
            return bytes([v & 0xFF, (v >> 8) & 0xFF, v & 0xFF, (v >> 8) & 0xFF, 0, 0, 0, 0])

        payload = grp(self._peer_cursor) + grp(self.base_seq) + grp(self.base_seq) + b"\x00\x00"
        hdr = udp_header(PKT_ACK, len(payload), self.session_id, 0)
        self._send(hdr + payload)

    def send_duml(
        self,
        cmd_set: int,
        cmd_id: int,
        payload: bytes,
        *,
        receiver_type: int,
        receiver_id: int,
        cmd_type: int = 2,
    ) -> None:
        self._cmd_counter += 1
        rt = routing_header(self._udp_seq, self._cmd_counter, self._drone)
        frame = DumlFrame(
            sender=0x02,
            receiver=((receiver_id << 5) | receiver_type) & 0xFF,
            msg_id=self._duml_seq,
            flags=(cmd_type << 5) & 0xFF,
            cmd_set=cmd_set,
            cmd_id=cmd_id,
            payload=payload,
        ).encode()
        self._duml_seq = (self._duml_seq + 1) & 0xFFFF
        pkt = udp_header(PKT_COMMAND, len(rt) + len(frame), self.session_id, self._udp_seq) + rt + frame
        if self._send(pkt):
            self._advance()
