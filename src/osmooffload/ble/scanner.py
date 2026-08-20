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
        model_id, model_name = _parse_model(adv.manufacturer_data)
        if model_id is None and name:
            for mid, mname in MODEL_NAMES.items():
                if name.replace(" ", "").lower().startswith(mname.replace(" ", "").lower()):
                    model_id, model_name = mid, mname
                    break
            else:
                if name.startswith("OsmoPocket4P"):
                    model_id, model_name = 0x0022, "Osmo Pocket 4 Pro"
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
