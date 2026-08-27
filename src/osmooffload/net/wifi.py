"""Windows WiFi control via netsh: join the camera's WPA2 AP, restore after.

The camera AP is internet-less; on an Ethernet-primary PC, internet routing is
untouched. All calls are async (subprocess) so the BLE keepalive keeps beating
while we associate.

Every subprocess is spawned with CREATE_NO_WINDOW so no console flashes on
screen — netsh/ipconfig otherwise pop a black window per call.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("osmo.wifi")

CREATE_NO_WINDOW = 0x08000000  # keeps netsh/ipconfig off-screen

PROFILE_XML = """<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{ssid}</name>
    <SSIDConfig><SSID><name>{ssid}</name></SSID><nonBroadcast>false</nonBroadcast></SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>manual</connectionMode>
    <MSM><security>
        <authEncryption>
            <authentication>WPA2PSK</authentication>
            <encryption>AES</encryption>
            <useOneX>false</useOneX>
        </authEncryption>
        <sharedKey>
            <keyType>passPhrase</keyType>
            <protected>false</protected>
            <keyMaterial>{password}</keyMaterial>
        </sharedKey>
    </security></MSM>
</WLANProfile>
"""


async def _run(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("mbcs", errors="replace")


_location_warned = False


def _location_gated(text: str) -> bool:
    """netsh refuses to report WLAN info when Windows Location is off.

    `show networks` and `show interfaces` both fail this way (error 5), but
    `add profile` / `connect` and ipconfig are unaffected — so a join still
    works, we just cannot read it back. Detect it and fall back to proving
    association by the DHCP address instead of blocking the whole transfer.
    """
    t = text.lower()
    return "location" in t and ("permission" in t or "services" in t)


def _warn_location_once() -> None:
    global _location_warned
    if not _location_warned:
        _location_warned = True
        log.warning(
            "Windows Location Services is off, so netsh cannot report WiFi "
            "state (Settings > Privacy & security > Location). Falling back "
            "to confirming the camera join by its DHCP address."
        )


async def _netsh(*args: str) -> tuple[int, str]:
    rc, text = await _run("netsh", *args)
    log.debug("netsh %s -> rc=%s %s", " ".join(args), rc, text.strip()[:400])
    return rc, text


async def interface_status(iface: str | None = None) -> dict[str, str]:
    _, text = await _netsh("wlan", "show", "interfaces")
    if _location_gated(text):
        _warn_location_once()
        return {}
    blocks = re.split(r"\r?\n\r?\n", text)
    for block in blocks:
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                fields[k.strip().lower()] = v.strip()
        if "name" in fields and (iface is None or fields["name"] == iface):
            return fields
    return {}


async def current_profile(iface: str | None = None) -> str | None:
    st = await interface_status(iface)
    if st.get("state", "").lower().startswith("connected"):
        return st.get("profile") or None
    return None


async def is_associated(ssid: str, iface: str | None = None) -> bool:
    st = await interface_status(iface)
    return (
        st.get("state", "").lower().startswith("connected")
        and st.get("ssid", "").lower() == ssid.lower()
    )


async def network_visible(ssid: str) -> bool:
    _, text = await _netsh("wlan", "show", "networks")
    if _location_gated(text):
        # Cannot enumerate at all — report visible so the caller proceeds to
        # the join, which does not need Location, rather than timing out with
        # a misleading "the camera's WiFi never appeared".
        _warn_location_once()
        return True
    return any(
        line.strip().lower().endswith(ssid.lower()) and line.strip().lower().startswith("ssid")
        for line in text.splitlines()
    )


async def ensure_profile(ssid: str, password: str) -> None:
    xml = PROFILE_XML.format(ssid=ssid, password=password)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".xml", delete=False, encoding="utf-8"
    ) as f:
        f.write(xml)
        path = Path(f.name)
    try:
        rc, out = await _netsh("wlan", "add", "profile", f"filename={path}", "user=current")
        if rc != 0:
            raise RuntimeError(f"netsh add profile failed: {out.strip()}")
    finally:
        path.unlink(missing_ok=True)


async def connect(ssid: str, timeout: float = 25.0, iface: str | None = None) -> dict[str, str]:
    args = ["wlan", "connect", f"name={ssid}"]
    if iface:
        args.append(f"interface={iface}")
    rc, out = await _netsh(*args)
    gated = _location_gated(out)
    if gated:
        # The connect request itself is honoured; only the status readback is
        # blocked. Let the caller's wait_for_ip() be the proof of association.
        _warn_location_once()
        return {}
    if rc != 0:
        raise RuntimeError(f"netsh connect failed: {out.strip()}")
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        st = await interface_status(iface)
        if not st:  # Location turned off mid-flight; fall back to the IP check
            return {}
        if (
            st.get("state", "").lower().startswith("connected")
            and st.get("ssid", "").lower() == ssid.lower()
        ):
            log.info(
                "associated: ssid=%s band=%s signal=%s rx/tx=%s/%s",
                st.get("ssid"), st.get("band") or st.get("radio type"),
                st.get("signal"), st.get("receive rate (mbps)"), st.get("transmit rate (mbps)"),
            )
            return st
        await asyncio.sleep(0.6)
    raise TimeoutError(f"association with {ssid} did not complete in {timeout:.0f}s")


async def disconnect(iface: str | None = None) -> None:
    args = ["wlan", "disconnect"]
    if iface:
        args.append(f"interface={iface}")
    await _netsh(*args)


def rejoin_sync(ssid: str, timeout: float = 30.0) -> bool:
    """Blocking WiFi rejoin for worker threads (mid-transfer AP blip recovery).
    The profile already exists from the initial join."""
    import time as _time

    try:
        subprocess.run(
            ["netsh", "wlan", "connect", f"name={ssid}"],
            capture_output=True, timeout=10, check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as e:
        log.debug("rejoin connect failed: %s", e)
        return False
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        try:
            out = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True, timeout=10, check=False,
                creationflags=CREATE_NO_WINDOW,
            ).stdout.decode("mbcs", "replace")
            if _location_gated(out):
                # Same fallback as connect(): the camera subnet address is
                # proof enough that we are back on the camera's AP.
                ip = subprocess.run(
                    ["ipconfig"], capture_output=True, timeout=10, check=False,
                    creationflags=CREATE_NO_WINDOW,
                ).stdout.decode("mbcs", "replace")
                if re.search(r"IPv4[^:]*:\s*192\.168\.2\.\d+", ip):
                    log.info("rejoined %s after AP blip (by DHCP address)", ssid)
                    return True
            elif "connected" in out.lower() and ssid.lower() in out.lower():
                log.info("rejoined %s after AP blip", ssid)
                return True
        except Exception:
            pass
        _time.sleep(1.5)
    return False


async def wait_for_ip(prefix: str = "192.168.2.", timeout: float = 20.0) -> str:
    """Wait for a DHCP address in the camera's subnet on any interface."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        _, text = await _run("ipconfig")
        m = re.search(rf"IPv4[^:]*:\s*({re.escape(prefix)}\d+)", text)
        if m:
            return m.group(1)
        await asyncio.sleep(0.8)
    raise TimeoutError(f"no {prefix}x address within {timeout:.0f}s")
