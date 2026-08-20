"""BLE GATT link to an Osmo camera — the control channel.

Implements the exact bring-up the camera demands (MEDIA_PROTOCOL.md preamble):
subscribe CCCDs of BOTH fff4 and fff5, then write `01 00` to the fff4 VALUE
(with response) and settle, before any fff5 traffic. fff5 is
write-without-response only, so consecutive writes are paced. Getting any of
this wrong yields a camera that ATT-acks everything and answers nothing.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections.abc import Callable

from bleak import BleakClient, normalize_uuid_16

from ..duml import DumlFrame, FrameParser
from ..duml.frame import FLAGS_REQUEST

log = logging.getLogger("osmo.ble")

CHAR_FFF3 = normalize_uuid_16(0xFFF3)
CHAR_FFF4 = normalize_uuid_16(0xFFF4)
CHAR_FFF5 = normalize_uuid_16(0xFFF5)

WRITE_SPACING_S = 0.10  # conservative floor between fff5 writes (docs: >= conn interval)


class BleLink:
    def __init__(self, device) -> None:
        self._client = BleakClient(device, timeout=20.0)
        self._parser_fff4 = FrameParser()
        self._parser_fff5 = FrameParser()
        self._msg_ids = itertools.cycle(range(0x2000, 0xFFF0))
        self._waiters: list[tuple[Callable[[DumlFrame], bool], asyncio.Future]] = []
        self._last_write = 0.0
        self._write_lock = asyncio.Lock()
        self._keepalive_task: asyncio.Task | None = None
        self.frames_log: list[tuple[str, DumlFrame]] = []
        self.on_frame: Callable[[DumlFrame], None] | None = None

    # -- lifecycle -----------------------------------------------------------

    async def connect(self) -> None:
        await self._client.connect()
        log.info("connected; negotiated MTU=%s", self._client.mtu_size)
        await self._client.start_notify(CHAR_FFF4, self._on_fff4)
        await self._client.start_notify(CHAR_FFF5, self._on_fff5)
        # arm: 01 00 to the fff4 VALUE, with response, then settle
        await self._client.write_gatt_char(CHAR_FFF4, b"\x01\x00", response=True)
        await asyncio.sleep(0.25)
        log.info("armed (01 00 -> fff4)")

    async def disconnect(self) -> None:
        self.stop_keepalive()
        try:
            if self._client.is_connected:
                await self._client.disconnect()
        except Exception as e:  # teardown must never raise
            log.debug("disconnect: %s", e)

    @property
    def is_connected(self) -> bool:
        return self._client.is_connected

    @property
    def mtu(self) -> int:
        return self._client.mtu_size

    # -- rx ------------------------------------------------------------------

    def _on_fff4(self, _char, data: bytearray) -> None:
        log.debug("fff4 <- %s", bytes(data).hex())
        for f in self._parser_fff4.feed(bytes(data)):
            self._dispatch(f)

    def _on_fff5(self, _char, data: bytearray) -> None:
        log.debug("fff5 <- %s", bytes(data).hex())
        for f in self._parser_fff5.feed(bytes(data)):
            self._dispatch(f)

    def _dispatch(self, frame: DumlFrame) -> None:
        log.info("RX %r", frame)
        self.frames_log.append(("rx", frame))
        if self.on_frame:
            try:
                self.on_frame(frame)
            except Exception:
                log.exception("on_frame handler")
        for matcher, fut in list(self._waiters):
            if not fut.done() and matcher(frame):
                fut.get_loop().call_soon_threadsafe(fut.set_result, frame)
                self._waiters.remove((matcher, fut))

    # -- tx ------------------------------------------------------------------

    def next_msg_id(self) -> int:
        return next(self._msg_ids)

    async def send(self, frame: DumlFrame) -> None:
        async with self._write_lock:
            gap = WRITE_SPACING_S - (time.monotonic() - self._last_write)
            if gap > 0:
                await asyncio.sleep(gap)
            raw = frame.encode()
            log.info("TX %r", frame)
            log.debug("fff5 -> %s", raw.hex())
            self.frames_log.append(("tx", frame))
            await self._client.write_gatt_char(CHAR_FFF5, raw, response=False)
            self._last_write = time.monotonic()

    def wait_for(
        self, matcher: Callable[[DumlFrame], bool], timeout: float
    ) -> "asyncio.Future[DumlFrame]":
        fut: asyncio.Future[DumlFrame] = asyncio.get_running_loop().create_future()
        self._waiters.append((matcher, fut))
        return asyncio.ensure_future(asyncio.wait_for(fut, timeout))

    async def request(self, frame: DumlFrame, timeout: float = 3.0) -> DumlFrame:
        """Send a request and await the response with matching cmdset/cmdid."""
        assert frame.flags == FLAGS_REQUEST
        waiter = self.wait_for(
            lambda f: f.key == frame.key and f.flags != FLAGS_REQUEST, timeout
        )
        await self.send(frame)
        return await waiter

    async def request_retry(
        self, build: Callable[[int], DumlFrame], timeout: float = 3.0, attempts: int = 3
    ) -> DumlFrame:
        """fff5 is write-without-response: a first write can silently drop.
        Retry with a freshly built frame (same content, new msg id)."""
        last: Exception | None = None
        for i in range(attempts):
            try:
                return await self.request(build(self.next_msg_id()), timeout)
            except asyncio.TimeoutError as e:
                last = e
                log.warning("no reply (attempt %d/%d) for %r", i + 1, attempts, build(0).key)
        raise last or asyncio.TimeoutError()

    # -- keepalive -----------------------------------------------------------

    def start_keepalive(self, build: Callable[[int], DumlFrame], interval: float = 1.0) -> None:
        async def beat() -> None:
            while self.is_connected:
                try:
                    await self.send(build(self.next_msg_id()))
                except Exception as e:
                    log.debug("keepalive send failed: %s", e)
                    return
                await asyncio.sleep(interval)

        self.stop_keepalive()
        self._keepalive_task = asyncio.get_running_loop().create_task(beat())

    def stop_keepalive(self) -> None:
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
        self._keepalive_task = None
