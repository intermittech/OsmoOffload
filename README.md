# Osmo Offload

Wireless media offload from DJI Osmo cameras to Windows — no phone, no cable,
no DJI account. Talks the reverse-engineered DUML protocol directly
(BLE control + camera WiFi AP + HTTP downloads).

Built on the protocol documentation of
[KonradIT/osmosis](https://github.com/KonradIT/osmosis) (MIT). Primary target:
DJI Osmo Pocket 4 Pro; the protocol core follows the model-agnostic paths.

**Status: early development — not yet usable.** See [CHANGELOG.md](CHANGELOG.md).

## Development

```
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -e .[dev]
.venv\Scripts\python -m pytest
.venv\Scripts\python scripts\probe.py --scan-only
```

Independent third-party project — not affiliated with, authorized, or endorsed
by DJI. Use at your own risk.
