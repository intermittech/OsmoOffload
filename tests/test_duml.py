"""Codec tests pinned against verbatim capture frames from the osmosis docs
(MEDIA_PROTOCOL.md — every vector below is a real frame from a Mimo capture,
so passing CRC + byte-exact rebuild means our codec matches DJI's)."""

import pytest

from osmooffload.duml import DumlFrame, FrameParser
from osmooffload.duml import commands as cmd

# (hex, sender, receiver, flags, cmd_set, cmd_id, payload_hex)
VECTORS = [
    # GetWifiSsid request
    ("550d0433020700a04007077472", 0x02, 0x07, 0x40, 0x07, 0x07, ""),
    # GetWifiPassword request
    ("550d0433020700a040070eb5ef", 0x02, 0x07, 0x40, 0x07, 0x0E, ""),
    # GetWifiMac request
    ("550d0433020700a040070ca7cc", 0x02, 0x07, 0x40, 0x07, 0x0C, ""),
    # Session open 04 00 -> 0xF0
    ("550f04a202f01bcb40002b04009ab9", 0x02, 0xF0, 0x40, 0x00, 0x2B, "0400"),
    # Session keepalive 01 01 -> 0xF0
    ("550f04a202f01bcb40002b0101abd6", 0x02, 0xF0, 0x40, 0x00, 0x2B, "0101"),
    # Camera status poll (cmd_type PUSH)
    ("550d0433020100a00002a0f5c3", 0x02, 0x01, 0x00, 0x02, 0xA0, ""),
    # Get version (cmd_type 4 -> flags 0x80)
    ("550d0433024800a0800000017e", 0x02, 0x48, 0x80, 0x00, 0x00, ""),
]

PAIRING_VECTOR = (
    "553304c2020700a0400745203238346165356238643736623333373561303461"
    "363431376164373162656133046f736d6f8c02"
)


@pytest.mark.parametrize("hex_,sender,receiver,flags,cset,cid,pl", VECTORS)
def test_decode_capture_vectors(hex_, sender, receiver, flags, cset, cid, pl):
    f = DumlFrame.decode(bytes.fromhex(hex_))
    assert f.sender == sender
    assert f.receiver == receiver
    assert f.flags == flags
    assert f.cmd_set == cset
    assert f.cmd_id == cid
    assert f.payload.hex() == pl


@pytest.mark.parametrize("hex_", [v[0] for v in VECTORS])
def test_reencode_is_byte_exact(hex_):
    raw = bytes.fromhex(hex_)
    f = DumlFrame.decode(raw)
    assert f.encode() == raw


def test_builder_matches_capture_getssid():
    # capture has msg-id bytes 00 a0 -> LE 0xA000
    f = cmd.get_wifi_ssid(0xA000)
    assert f.encode().hex() == "550d0433020700a04007077472"


def test_builder_matches_capture_pairing():
    f = cmd.set_pairing_pin(0xA000, "284ae5b8d76b3375a04a6417ad71bea3", "osmo")
    assert f.encode().hex() == PAIRING_VECTOR


def test_builder_matches_capture_session():
    assert cmd.session_open(0xCB1B).encode().hex() == "550f04a202f01bcb40002b04009ab9"
    assert cmd.session_keepalive(0xCB1B).encode().hex() == "550f04a202f01bcb40002b0101abd6"


def test_parser_reassembles_split_and_garbage():
    frames = [bytes.fromhex(v[0]) for v in VECTORS]
    stream = b"\x00\x13garbage" + frames[0] + frames[1][:5]
    p = FrameParser()
    got = p.feed(stream)
    assert len(got) == 1
    got += p.feed(frames[1][5:] + b"\x55\x01junk" + frames[2])
    assert len(got) == 3
    assert [g.encode() for g in got] == frames[:3]


def test_packed_string_reply():
    payload = bytes([0x00, 0x12]) + b"XtraEdgePro-2DCA16"
    status, val = cmd.parse_packed_string_reply(payload)
    assert status == 0
    assert val == "XtraEdgePro-2DCA16"
