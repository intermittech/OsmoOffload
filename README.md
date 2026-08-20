# Osmo Offload

Wireless media offload from DJI Osmo cameras to Windows — **no phone, no
cable, no DJI account**. Talks the reverse-engineered DUML protocol directly:
BLE wakes and pairs the camera, the app joins the camera's own WiFi, and files
come down over HTTP at wire speed (60+ MB/s on a Pocket 4 Pro at 5 GHz).

Built on the protocol documentation of
[KonradIT/osmosis](https://github.com/KonradIT/osmosis) (MIT).

## Features

- **One-click offload** of everything new — resumable, hash-verified, deduped
  against a transfer history; dated folder structure with `video`/`photo`
  split and optional naming templates
- **Live camera card**: battery (red→amber→green gradient), internal + SD
  storage bars, connection state
- **Media browser** with real camera thumbnails and cherry-pick transfers
- **Auto-transfer**: camera comes in range → files flow (cooldown + backoff)
- **Free up camera**: deletes only files with a verified on-disk copy,
  confirmed by re-listing — never blind
- **Session reports** (HTML + CSV) with per-clip duration/resolution and a
  shoot summary; per-session logs, openable from the History tab
- **After-transfer hooks** (`OSMO_DEST`, `OSMO_REPORT`, `OSMO_FILES`, …)
- **USB ingest** through the same pipeline when you do plug in a cable
  (strictly detects real DJI volumes)
- Tray mode with start-minimized, first-run wizard, update check against
  GitHub releases

Hardware-verified on the **DJI Osmo Pocket 4 Pro**; the protocol core follows
the model-agnostic paths documented for the wider Osmo line.

## Install

Grab `OsmoOffload-Setup-<version>.exe` from Releases. Per-user install, no
admin needed; "Start with Windows" is a checkbox in the installer. Uninstall
from Windows Settings keeps your transfer history and config
(`%APPDATA%\OsmoOffload`).

## Requirements

- Windows 10/11 with Bluetooth LE and WiFi (5 GHz capable)
- A supported DJI Osmo camera

## Build from source

```
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -e .[dev] pyinstaller
.venv\Scripts\python -m pytest
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

## Development scripts

`scripts/probe.py` (BLE pair + credentials), `scripts/wake_and_join.py`
(AP bring-up), `scripts/list_media.py` (datalink + manifest),
`scripts/offload.py` (headless CLI offload), `scripts/gui.py --mock`.

## Credits & license

Protocol reverse engineering: [KonradIT/osmosis](https://github.com/KonradIT/osmosis)
and the projects it credits. This app: MIT.

Independent third-party project — not affiliated with, authorized, or endorsed
by DJI. "DJI" and "Osmo" are trademarks of their respective owners. Use at
your own risk; deleting media is your responsibility (the app only deletes
files it has verified on disk, and only when you ask).
