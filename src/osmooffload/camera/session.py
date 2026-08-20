"""Camera datalink session: registration, playback hold, media list, status.

Blocking (plain sockets) — run it in a worker thread; the BLE keepalive lives
on the asyncio loop. Ported from osmosis CameraSession/DumlSession, minus what
we don't need (GPS, trim, drones).
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from dataclasses import dataclass, field

from ..duml import commands as blecmd
from ..net.datalink import DatalinkTransport
from . import manifest as mf

log = logging.getLogger("osmo.session")

LIST_BASE = bytes.fromhex(
    "4a002a10010000000000010000002d000d0100ffffffffffffffff000100000000000000000000000000"
)
LIST_TRIGGER = bytes.fromhex("4a040e1001000000000001000000")
APP_PRESENCE = bytes.fromhex("170046237c415050000000000002")
SD_QUERY_CTR = 1
INTERNAL_QUERY_CTR = 2
NEWEST_SENTINEL = 0x40000001

PARAM_SUBS = [
    "camcap_mode_profile", "camcap_video_format", "camcap_fov", "camcap_iso",
    "camcap_photo_storage_format", "camcap_color_mode", "cam_storage", "cam_status",
]


def list_cmd(ctr: int, cursor: int) -> bytes:
    p = bytearray(LIST_BASE)
    p[4] = ctr
    struct.pack_into("<I", p, 10, cursor & 0xFFFFFFFF)
    return bytes(p)


def device_info_payload() -> bytes:
    b = bytearray(62)
    b[1:4] = b"APP"
    b[41] = 0x02
    b[50] = 0x02
    b[51] = 0x08
    return bytes(b)


def subscription_payload(name: str, sub_id: int) -> bytes:
    nb = name.encode("ascii")
    inner = len(nb) + 6
    return (
        bytes([0x02, 0x02, 0x00, 0x00])
        + struct.pack("<I", sub_id)
        + b"\x00\x00\x00"
        + struct.pack("<H", inner)
        + struct.pack("<H", len(nb))
        + nb
        + b"\x00\x00\x00\x00"
    )


@dataclass
class CameraStatus:
    battery_pct: int | None = None
    voltage_mv: int | None = None
    current_ma: int | None = None
    playback: bool | None = None
    recording: bool | None = None
    store_count: int | None = None
    sd_total_mib: int | None = None
    sd_free_mib: int | None = None
    internal_total_mib: int | None = None
    internal_free_mib: int | None = None
    active_total_mib: int | None = None
    active_free_mib: int | None = None
    last_update: float = field(default_factory=time.monotonic)


class CameraDatalink:
    def __init__(
        self,
        ip: str,
        port: int = 9004,
        tcp_poke: bool = True,
        identifier: str = "osmooffload",
        bind_local: bool = True,
    ):
        self.ip = ip
        self.port = port
        self.tcp_poke = tcp_poke
        self.identifier = identifier
        # Symmetric port (9004->9004) like DJI Fly does with drones. On Windows
        # this also keeps the firewall's UDP state happy: replies come back to
        # the same flow instead of an unsolicited ephemeral port.
        self.tx = DatalinkTransport(ip, port, bind_local=bind_local)
        self.status = CameraStatus()
        self.playback_reported: bool | None = None
        self.channel_mismatch = False
        self._registered_at = 0.0
        # serializes socket use between caller thread and the keepalive beat
        self._io_lock = threading.RLock()
        self._ka_stop: threading.Event | None = None
        self._ka_thread: threading.Thread | None = None
        self._open = False
        self.on_status = None  # optional callback(CameraStatus), from the beat

    # -- bring-up ------------------------------------------------------------

    def open(self) -> bool:
        self.tx.open()
        if self.tcp_poke:
            try:
                with socket.create_connection((self.ip, 7001), timeout=1.2) as s:
                    frame = blecmd.set_pairing_pin(0x8092, self.identifier)
                    s.sendall(frame.encode())
                time.sleep(0.4)
                log.info("tcp/7001 poke sent")
            except OSError as e:
                log.warning("tcp/7001 poke failed: %s (continuing)", e)
        reply = self.tx.handshake()
        if reply is None:
            log.error("handshake FAILED on udp/%d", self.port)
            self.tx.close()
            return False
        log.info("handshake OK on udp/%d", self.port)
        for _ in range(5):
            got = self.tx.recv_all(0.4)
            self._parse_status(got)
            self.tx.send_ack()
        self.tx.sync_seq_to_channel()
        log.info(
            "session=0x%04x base=0x%04x channel=0x%04x",
            self.tx.session_id, self.tx.base_seq, self.tx.cam_channel,
        )
        self.channel_mismatch = self.tx.cam_channel != self.tx.base_seq
        if self.channel_mismatch:
            log.warning(
                "peer answered on its own sequence channel — may be holding a "
                "session from a previous connection"
            )
        self._open = True
        return True

    @property
    def alive(self) -> bool:
        return self._open

    def register(self) -> None:
        self.tx.send_duml(0x00, 0x81, device_info_payload(), receiver_type=0x08, receiver_id=2, cmd_type=4)
        self._drain(0.2)
        self.tx.send_duml(0x00, 0x88, APP_PRESENCE, receiver_type=0x08, receiver_id=1)
        self._drain(0.2)
        self.tx.send_duml(0x03, 0xDA, bytes.fromhex("05ffffffff"), receiver_type=0x03, receiver_id=0)
        self._drain(0.2)
        for i, name in enumerate(PARAM_SUBS):
            self.tx.send_duml(0x00, 0x99, subscription_payload(name, i + 1), receiver_type=0x08, receiver_id=1)
            self._drain(0.08)
        self._registered_at = time.monotonic()
        log.info("registered (devinfo + presence + gimbal init + %d subs)", len(PARAM_SUBS))

    def close(self) -> None:
        self.stop_keepalive()
        self._open = False
        try:
            # leave playback politely; harmless if we never entered
            self.tx.send_duml(0x02, 0x0C, bytes.fromhex("01010000"), receiver_type=0x01, receiver_id=0)
            time.sleep(0.1)
        except Exception:
            pass
        self.tx.close()

    # -- warm-hold keepalive --------------------------------------------------

    def start_keepalive(self) -> None:
        """Hold the datalink (and the camera AP) between actions: ~1 Hz
        presence beat + transport ACK, status kept fresh. Idempotent."""
        if self._ka_thread and self._ka_thread.is_alive():
            return
        self._ka_stop = threading.Event()

        def beat() -> None:
            ticks = 0
            while self._ka_stop is not None and not self._ka_stop.wait(1.0):
                try:
                    with self._io_lock:
                        self.presence_beat()
                        got = self.tx.recv_all(0.15)
                        self._parse_status(got)
                        self.tx.send_ack()
                    ticks += 1
                    if self.on_status and ticks % 3 == 0:
                        self.on_status(self.status)
                except Exception as e:
                    log.debug("keepalive beat died: %s", e)
                    self._open = False
                    return

        self._ka_thread = threading.Thread(target=beat, name="osmo-dl-keepalive", daemon=True)
        self._ka_thread.start()
        log.info("datalink keepalive started")

    def stop_keepalive(self) -> None:
        if self._ka_stop is not None:
            self._ka_stop.set()
        if self._ka_thread and self._ka_thread.is_alive():
            self._ka_thread.join(timeout=2.0)
        self._ka_thread = None
        self._ka_stop = None

    # -- status --------------------------------------------------------------

    def _drain(self, duration: float) -> list[bytes]:
        got = self.tx.recv_all(duration)
        self._parse_status(got)
        return got

    def _parse_status(self, datagrams: list[bytes]) -> None:
        st = self.status
        for d in datagrams:
            for cmd_set, cmd_id, pl in mf.iter_frames(d):
                if cmd_set == 0x02 and cmd_id == 0x80 and len(pl) >= 13:
                    flags = struct.unpack_from("<I", pl, 0)[0]
                    st.playback = bool(flags & 0x40000000)
                    self.playback_reported = st.playback
                    st.recording = bool(pl[0] & 0x80)
                    st.active_total_mib = struct.unpack_from("<I", pl, 5)[0]
                    st.active_free_mib = struct.unpack_from("<I", pl, 9)[0]
                    st.last_update = time.monotonic()
                elif cmd_set == 0x02 and cmd_id == 0xDC and len(pl) >= 22:
                    st.store_count = pl[2]
                    st.sd_total_mib = struct.unpack_from("<I", pl, 6)[0]
                    st.sd_free_mib = struct.unpack_from("<I", pl, 10)[0]
                    if len(pl) >= 32:
                        st.internal_total_mib = struct.unpack_from("<I", pl, 24)[0]
                        st.internal_free_mib = struct.unpack_from("<I", pl, 28)[0]
                    st.last_update = time.monotonic()
                elif cmd_set == 0x0D and cmd_id == 0x02 and len(pl) >= 21:
                    st.voltage_mv = struct.unpack_from("<H", pl, 1)[0]
                    st.current_ma = struct.unpack_from("<i", pl, 5)[0]
                    st.battery_pct = pl[20]
                    st.last_update = time.monotonic()

    # -- playback ------------------------------------------------------------

    def enter_playback(self, attempts: int = 3) -> bool:
        if self.playback_reported:
            return True
        for i in range(attempts):
            self.tx.send_duml(0x02, 0x0C, bytes.fromhex("01010001"), receiver_type=0x01, receiver_id=0)
            deadline = time.monotonic() + 0.9
            while time.monotonic() < deadline:
                self._drain(0.15)
                if self.playback_reported:
                    log.info("playback held (attempt %d)", i + 1)
                    return True
        return self._enter_playback_via_control()

    def _enter_playback_via_control(self) -> bool:
        """The Pocket 3 route: 0x01/0x01 control bursts, cmd_type 0, no replies."""
        log.info("0x02/0x0C did not move the camera — trying 0x01/0x01 route")
        prelude = bytes.fromhex("0300000000040000000701")
        enter = bytes.fromhex("0000000000040000000401")
        deadline = time.monotonic() + 2.5
        sent = silent = 0
        while time.monotonic() < deadline:
            self.tx.send_duml(0x01, 0x01, prelude if sent < 6 else enter,
                              receiver_type=0x01, receiver_id=0, cmd_type=0)
            sent += 1
            got = self._drain(0.05)
            if self.playback_reported:
                log.info("playback held via 0x01/0x01 (after %d frames)", sent)
                return True
            silent = silent + 1 if not got else 0
            if silent >= 8:
                log.warning("0x01/0x01: camera went quiet after %d frames", sent)
                return False
        log.warning("0x01/0x01 sent %d frames, still in capture", sent)
        return False

    def presence_beat(self) -> None:
        self.tx.send_duml(0x00, 0x88, APP_PRESENCE, receiver_type=0x08, receiver_id=1)

    # -- inline commands -----------------------------------------------------

    def run_command(
        self, cmd_set: int, cmd_id: int, payload: bytes,
        receiver_type: int = 0x01, receiver_id: int = 0,
        timeout: float = 2.5, cmd_type: int = 2,
    ) -> bytes | None:
        """Send a command and wait for its non-empty reply frame (the camera
        sends an empty transport ACK first — skip it). Status keeps parsing."""
        with self._io_lock:
            self.tx.send_duml(cmd_set, cmd_id, payload, receiver_type=receiver_type,
                              receiver_id=receiver_id, cmd_type=cmd_type)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                got = self.tx.recv_all(0.3)
                self._parse_status(got)
                for d in got:
                    for cs, ci, pl in mf.iter_frames(d):
                        if cs == cmd_set and ci == cmd_id and pl:
                            return pl
                self.tx.send_ack()
            return None

    # -- delete (irreversible) ----------------------------------------------

    @staticmethod
    def delete_payload(handles: list[int]) -> bytes:
        """0x00/0x28 payload: [count:u8][handle:u32-LE x n][n:u32] 00 [n:u32] 01 01 00 00"""
        n = len(handles)
        out = bytearray([n])
        for h in handles:
            out += struct.pack("<I", h)
        out += struct.pack("<I", n) + b"\x00" + struct.pack("<I", n) + bytes([1, 1, 0, 0])
        return bytes(out)

    def delete_files(self, handles: list[int]) -> int | None:
        """Delete by manifest handles. Returns the status word (0x0000 = OK,
        0x00d6 = no such handle) or None when no reply arrived — in which case
        the delete MAY still have landed: verify by re-listing, NEVER re-send
        (cameras reuse file numbers, so a stale handle can hit a newer file).
        """
        if not handles:
            return None
        # Hardware finding (osmosis): deletes are only answered while the
        # camera is actually in playback — re-assert right before, every time.
        self.run_command(0x02, 0x0C, bytes.fromhex("01010001"), timeout=1.5)
        log.info("DELETE 0x00/0x28 handles=%s", [f"0x{h:08x}" for h in handles])
        pl = self.run_command(0x00, 0x28, self.delete_payload(handles), timeout=10.0)
        if pl is None:
            log.warning("DELETE: no reply — may still have landed; caller must verify by re-list")
            return None
        status = pl[0] | (pl[1] << 8) if len(pl) >= 2 else pl[0]
        log.info("DELETE status=0x%04x", status)
        return status

    # -- media list ----------------------------------------------------------

    def query_newest_page(self) -> tuple[list[mf.MediaRecord], bytes]:
        """The proven 3-command newest-page sequence. Returns (records, raw_blob)."""
        with self._io_lock:
            blob = bytearray()
            self.tx.send_duml(0x00, 0x26, list_cmd(SD_QUERY_CTR, 1), receiver_type=0x01, receiver_id=0)
            last_count = -1
            stable = 0
            for batch in range(15):
                got = self.tx.recv_all(0.8)
                for r in got:
                    blob += r
                self._parse_status(got)
                self.tx.send_ack()
                self.presence_beat()
                if batch == 1:
                    self.tx.send_duml(0x00, 0x26, LIST_TRIGGER, receiver_type=0x01, receiver_id=0)
                if batch == 2:
                    self.tx.send_duml(0x00, 0x26, list_cmd(INTERNAL_QUERY_CTR, NEWEST_SENTINEL),
                                      receiver_type=0x01, receiver_id=0)
                if batch == 4 and not mf.list_answered(bytes(blob)):
                    # not a single 0x00/0x27 frame: dead/stale session —
                    # fail fast so the caller can re-handshake (saves ~8 s)
                    log.warning("list unanswered after %d batches — bailing early", batch + 1)
                    break
                count = len(mf.decode(mf.reassemble(bytes(blob))))
                if count != last_count:
                    log.info("list: %d files (batch %d, blob %d B)", count, batch, len(blob))
                if batch >= 4 and count > 0 and count == last_count:
                    stable += 1
                    if stable >= 2:
                        break
                else:
                    stable = 0
                last_count = count
            raw = bytes(blob)
            return self._collect_stores(raw), raw

    def _collect_stores(self, raw: bytes) -> list[mf.MediaRecord]:
        def slice_of(ctr: int) -> list[mf.MediaRecord]:
            b = mf.reassemble(raw, request_ctr=ctr)
            return mf.decode(b) if b else []

        sd = slice_of(SD_QUERY_CTR)
        internal = slice_of(INTERNAL_QUERY_CTR)
        ambiguous = bool(sd) and bool(internal) and (
            {r.media_path for r in sd} == {r.media_path for r in internal}
        )
        if (not sd and not internal) or ambiguous:
            merged = mf.decode(mf.reassemble(raw))
            log.info("store split unavailable (%s) — %d files",
                     "identical lists" if ambiguous else "no counter echo", len(merged))
            return merged
        for r in internal:
            r.storage = 1
        for r in sd:
            r.storage = 0
        log.info("per-store lists — SD %d, internal %d", len(sd), len(internal))
        seen: set[str] = set()
        out: list[mf.MediaRecord] = []
        for r in internal + sd:  # internal first on the rare path overlap
            if r.media_path not in seen:
                seen.add(r.media_path)
                out.append(r)
        return out
