"""BLE discovery of DJI Osmo cameras.

Two advert formats exist (MEDIA_PROTOCOL.md per-model reference):
- classic: model id u16-LE in the DJI manufacturer data
- new (Pocket 4 Pro): flag bit at mfr payload byte 5 marks a u16 product type
  at bytes 10-11 (218 = Pocket 4 Pro); the classic field reads 0x0000 there.

We match generously (name prefix, fff0 service, DJI company ids) and let the
caller pick; a renamed body still matches on manufacturer data.
"""

from __future__ import annotations

from dataclasses import dataclass

from bleak import BleakScanner

DJI_COMPANY_IDS = {0x08AA, 0xAA08}  # docs cite both byte orders; accept either
XTRA_COMPANY_IDS = {0xAAF7, 0xF7AA}

NAME_PREFIXES = (
    "OsmoPocket4P",
    "OsmoPocket4",
    "OsmoPocket3",
    "OsmoAction",
    "OsmoNano",
    "Osmo360",
    "XtraEdgePro",
    "Osmo",
)

MODEL_NAMES = {
    0x0015: "Osmo Action 5 Pro",
    0x0018: "Osmo Action 6",
    0x0019: "Osmo Nano",
    0x0020: "Osmo Pocket 3",
    0x0021: "Osmo Pocket 4",
    0x0022: "Osmo Pocket 4 Pro",
}

NEW_ADVERT_PRODUCT_TYPES = {218: ("Osmo Pocket 4 Pro", 0x0022)}

# BLE local-name prefix -> (model id, display name). Used when a packet carries
# no manufacturer data — adverts and scan responses arrive separately, so the
# first packet seen for a device often has the name but not the model bytes.
# Longest prefix first: "OsmoPocket4P" must beat "OsmoPocket4".
NAME_MODELS: tuple[tuple[str, int, str], ...] = (
    ("OsmoPocket4P", 0x0022, "Osmo Pocket 4 Pro"),
    ("OsmoPocket4", 0x0021, "Osmo Pocket 4"),
    ("OsmoPocket3", 0x0020, "Osmo Pocket 3"),
    ("OsmoNano", 0x0019, "Osmo Nano"),
    ("Osmo360", 0x0017, "Osmo 360"),
    # The Xtra Edge Pro is a rebadged Action 5 Pro on DJI firmware and shares
    # its model byte, but its name matches no MODEL_NAMES entry.
    ("XtraEdgePro", 0x0015, "Xtra Edge Pro"),
    ("OsmoAction6", 0x0018, "Osmo Action 6"),
)


def resolve_model(mfr_data: dict[int, bytes], name: str) -> tuple[int | None, str]:
    """Model from manufacturer data, falling back to the BLE local name."""
    model_id, model_name = _parse_model(mfr_data)
    if model_id is not None:
        return model_id, model_name
    if name:
        # NAME_MODELS first: it is ordered longest-prefix-first, while
        # MODEL_NAMES is a plain dict, so matching that first resolves
        # "OsmoPocket4P-…" as a Pocket 4 rather than a Pocket 4 Pro.
        for prefix, mid, disp in NAME_MODELS:
            if name.startswith(prefix):
                return mid, disp
        for mid, mname in MODEL_NAMES.items():
            if name.replace(" ", "").lower().startswith(mname.replace(" ", "").lower()):
                return mid, mname
    return None, "unknown"


@dataclass
class FoundCamera:
    address: str
    name: str
    rssi: int
    model_id: int | None
    model_name: str
    mfr_hex: str
    device: object  # BLEDevice, kept for connecting


def _parse_model(mfr_data: dict[int, bytes]) -> tuple[int | None, str]:
    for cid, payload in mfr_data.items():
        if cid not in DJI_COMPANY_IDS | XTRA_COMPANY_IDS:
            continue
        # new advert format: product type u16-LE at payload bytes 10-11
        if len(payload) >= 12:
            ptype = payload[10] | (payload[11] << 8)
            if ptype in NEW_ADVERT_PRODUCT_TYPES:
                name, model_id = NEW_ADVERT_PRODUCT_TYPES[ptype]
                return model_id, name
        # classic format: model byte at payload[0]
        if payload:
            model_id = payload[0]
            if model_id in MODEL_NAMES:
                return model_id, MODEL_NAMES[model_id]
    return None, "unknown"


async def scan(timeout: float = 8.0) -> list[FoundCamera]:
    found: list[FoundCamera] = []
    results = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for address, (device, adv) in results.items():
        name = adv.local_name or device.name or ""
        is_dji_mfr = bool(set(adv.manufacturer_data) & (DJI_COMPANY_IDS | XTRA_COMPANY_IDS))
        is_name = name.startswith(NAME_PREFIXES)
        has_fff0 = any("fff0" in (u or "") for u in adv.service_uuids)
        if not (is_dji_mfr or is_name or has_fff0):
            continue
        model_id, model_name = resolve_model(adv.manufacturer_data, name)
        mfr_hex = ";".join(f"{cid:04x}:{p.hex()}" for cid, p in adv.manufacturer_data.items())
        found.append(
            FoundCamera(
                address=address,
                name=name,
                rssi=adv.rssi,
                model_id=model_id,
                model_name=model_name,
                mfr_hex=mfr_hex,
                device=device,
            )
        )
    found.sort(key=lambda c: -c.rssi)
    return found


async def find_fast(address: str, timeout: float = 12.0) -> FoundCamera | None:
    """Return the saved camera as soon as its advert is seen, instead of
    waiting out the full scan window — the common reconnect case resolves in
    a second or two rather than the fixed timeout."""
    import asyncio

    want = address.lower()
    found_evt = asyncio.Event()
    hit: dict[str, FoundCamera] = {}

    def on_detect(device, adv) -> None:
        if (device.address or "").lower() != want:
            return
        name = adv.local_name or device.name or ""
        model_id, model_name = resolve_model(adv.manufacturer_data, name)
        # Advert and scan response arrive as separate packets; the first one
        # often carries no model bytes. Keep an unresolved hit as a fallback
        # but hold out for a packet that identifies the body, so a reconnect
        # never downgrades a known camera to "unknown" in the saved state.
        if model_id is None and hit.get("c") is not None:
            return
        hit["c"] = FoundCamera(
            address=device.address, name=name, rssi=adv.rssi,
            model_id=model_id, model_name=model_name,
            mfr_hex=";".join(f"{c:04x}:{p.hex()}" for c, p in adv.manufacturer_data.items()),
            device=device,
        )
        if model_id is not None:
            found_evt.set()

    scanner = BleakScanner(detection_callback=on_detect)
    await scanner.start()
    try:
        await asyncio.wait_for(found_evt.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        await scanner.stop()
    return hit.get("c")
