"""Builders for the BLE-side DUML commands (osmosis MEDIA_PROTOCOL.md §21-26).

Address byte is ((id << 5) | type). We always send as App (0x02). Two special
session endpoints are NOT the camera: 0xF0 (session wake/keepalive) and 0x1C
(the 0x53/0x10 wake target). Addressing those to the camera gets `e0` rejects.
"""

from __future__ import annotations

from .frame import FLAGS_REQUEST, FLAGS_RESPONSE, DumlFrame

ADDR_APP = 0x02
ADDR_CAMERA = 0x01
ADDR_GIMBAL = 0x03
ADDR_WIFI = 0x07
ADDR_DM368_1 = 0x28  # (1<<5)|8
ADDR_DM368_2 = 0x48  # (2<<5)|8
ADDR_SYSTEM_RTC = 0x28
ADDR_SESSION = 0xF0  # type 0x10, id 7
ADDR_WAKE = 0x1C  # type 0x1C, id 0


def pack_string(s: str) -> bytes:
    raw = s.encode("utf-8")
    if len(raw) > 0xFF:
        raise ValueError("string too long for PackString")
    return bytes([len(raw)]) + raw


def _req(receiver: int, msg_id: int, cmd_set: int, cmd_id: int, payload: bytes = b"") -> DumlFrame:
    return DumlFrame(
        sender=ADDR_APP,
        receiver=receiver,
        msg_id=msg_id,
        flags=FLAGS_REQUEST,
        cmd_set=cmd_set,
        cmd_id=cmd_id,
        payload=payload,
    )


def session_open(msg_id: int) -> DumlFrame:
    """0x00/0x2b `04 00` -> 0xF0. First write of every session, pre-pairing."""
    return _req(ADDR_SESSION, msg_id, 0x00, 0x2B, bytes([0x04, 0x00]))


def session_keepalive(msg_id: int) -> DumlFrame:
    """0x00/0x2b `01 01` -> 0xF0, repeated ~1 Hz for the whole BLE session."""
    return _req(ADDR_SESSION, msg_id, 0x00, 0x2B, bytes([0x01, 0x01]))


def wake(msg_id: int) -> DumlFrame:
    """0x53/0x10 `00 00 00 00` -> 0x1C. Camera answers 01 00 00 00 and wakes.

    A Pocket 3 answers `e0` here yet still wakes via the 0x2b session —
    treat a reject as non-fatal.
    """
    return _req(ADDR_WAKE, msg_id, 0x53, 0x10, bytes(4))


def set_pairing_pin(msg_id: int, identifier: str, token: str = "osmo") -> DumlFrame:
    """0x07/0x45 -> WiFi. Reply payload: 00 01 already-paired / 00 02 approval.

    `identifier` is what the camera remembers the approval under — mint one
    per install, persist it, and NEVER vary it between retries.
    """
    return _req(ADDR_WIFI, msg_id, 0x07, 0x45, pack_string(identifier) + pack_string(token))


def pairing_approval_ack(msg_id: int) -> DumlFrame:
    """ACK for the 0x07/0x46 approval REQUEST the camera sends after the user
    taps approve. That request is the pairing-complete signal."""
    return DumlFrame(
        sender=ADDR_APP,
        receiver=ADDR_WIFI,
        msg_id=msg_id,
        flags=FLAGS_RESPONSE,
        cmd_set=0x07,
        cmd_id=0x46,
        payload=bytes([0x00]),
    )


def get_wifi_ssid(msg_id: int) -> DumlFrame:
    return _req(ADDR_WIFI, msg_id, 0x07, 0x07)


def get_wifi_password(msg_id: int) -> DumlFrame:
    return _req(ADDR_WIFI, msg_id, 0x07, 0x0E)


def get_wifi_mac(msg_id: int) -> DumlFrame:
    return _req(ADDR_WIFI, msg_id, 0x07, 0x0C)


def connect_to_wifi(msg_id: int, ssid: str, password: str) -> DumlFrame:
    """0x07/0x47 -> WiFi: send the camera its OWN creds; AP comes up ~15 s later.
    Fallback only — Mimo doesn't send this; the AP normally rises via the
    session + wake alone."""
    return _req(ADDR_WIFI, msg_id, 0x07, 0x47, pack_string(ssid) + pack_string(password))


def parse_packed_string_reply(payload: bytes) -> tuple[int, str]:
    """Replies shaped `[status:1][PackString value]` -> (status, value)."""
    if len(payload) < 2:
        raise ValueError(f"short packed-string reply: {payload.hex()}")
    status = payload[0]
    n = payload[1]
    raw = payload[2 : 2 + n]
    if len(raw) != n:
        raise ValueError(f"truncated packed string: {payload.hex()}")
    return status, raw.decode("utf-8", errors="replace")
