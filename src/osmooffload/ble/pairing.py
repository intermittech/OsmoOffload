"""Pairing + WiFi-credential flow over BLE (MEDIA_PROTOCOL.md §21-26 order).

Sequence (mirrors what Mimo does):
  1. session open  0x00/0x2b `04 00` -> 0xF0   (pre-pairing)
  2. SetPairingPIN 0x07/0x45                    -> `00 01` paired / `00 02` approval
  3. (approval) user taps on camera -> camera sends 0x07/0x46 REQUEST -> we ACK
  4. keepalive     0x00/0x2b `01 01` @ 1 Hz     (holds the link; idle drop ~5 s)
  5. wake          0x53/0x10 -> 0x1C            (`e0` reject is non-fatal on Pockets)
  6. GetWifiSsid / GetWifiPassword / GetWifiMac (paced ~500 ms apart)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from ..duml import commands as cmd
from ..duml.frame import FLAGS_REQUEST
from .link import BleLink

log = logging.getLogger("osmo.pair")


@dataclass
class WifiCredentials:
    ssid: str
    password: str
    mac: str | None
    was_already_paired: bool


class ApprovalDeclined(Exception):
    pass


async def pair_and_get_credentials(
    link: BleLink,
    identifier: str,
    approval_timeout: float = 120.0,
    on_approval_needed=None,
) -> WifiCredentials:
    # 1. open the session (Mimo's first write, before pairing)
    await link.send(cmd.session_open(link.next_msg_id()))
    await asyncio.sleep(0.2)

    # Register the approval-request waiter up front: the 0x07/0x46 REQUEST is
    # the "go" signal and can arrive the moment the user taps approve.
    approval_waiter = link.wait_for(
        lambda f: f.key == (0x07, 0x46) and f.flags & 0xE0 == FLAGS_REQUEST,
        approval_timeout,
    )

    # 2. pairing pin — same identifier on every retry (write-no-rsp can drop)
    reply = await link.request_retry(
        lambda mid: cmd.set_pairing_pin(mid, identifier), timeout=3.0
    )
    status = reply.payload[1] if len(reply.payload) >= 2 else None
    already = status == 0x01
    log.info("pairing status: %s (%s)", f"{status:#04x}" if status is not None else "?",
             "already paired" if already else "approval required")

    if already:
        approval_waiter.cancel()
    else:
        if on_approval_needed:
            on_approval_needed()
        try:
            req = await approval_waiter
        except asyncio.TimeoutError:
            raise TimeoutError(
                "No approval arrived from the camera — the prompt may have "
                "expired or been declined. Re-run when you're at the camera."
            ) from None
        # ACK the request with its own msg id — this completes pairing
        await link.send(cmd.pairing_approval_ack(req.msg_id))
        log.info("pairing approved on camera (07/46 payload=%s)", req.payload.hex())

    # 4. keepalive from here on (camera drops an idle link after ~5-6 s)
    link.start_keepalive(cmd.session_keepalive, interval=1.0)

    # 5. wake — a Pocket 3 answers e0 yet wakes anyway; treat all outcomes as ok
    try:
        wreply = await link.request(cmd.wake(link.next_msg_id()), timeout=2.5)
        log.info("wake reply: %s", wreply.payload.hex())
    except asyncio.TimeoutError:
        log.info("wake: no reply (non-fatal)")

    # 6. credentials, paced
    await asyncio.sleep(0.5)
    ssid_reply = await link.request_retry(cmd.get_wifi_ssid, timeout=3.0)
    _, ssid = cmd.parse_packed_string_reply(ssid_reply.payload)
    log.info("SSID: %s", ssid)

    await asyncio.sleep(0.5)
    pass_reply = await link.request_retry(cmd.get_wifi_password, timeout=3.0)
    _, password = cmd.parse_packed_string_reply(pass_reply.payload)
    log.info("password: (len %d)", len(password))

    mac = None
    try:
        await asyncio.sleep(0.5)
        mac_reply = await link.request_retry(cmd.get_wifi_mac, timeout=3.0, attempts=2)
        if len(mac_reply.payload) >= 7:
            mac = ":".join(f"{b:02x}" for b in mac_reply.payload[1:7])
            log.info("camera WiFi MAC: %s", mac)
    except asyncio.TimeoutError:
        log.info("no MAC reply (non-fatal)")

    return WifiCredentials(ssid=ssid, password=password, mac=mac, was_already_paired=already)
