# Changelog

All notable changes to Osmo Offload. Versions follow [SemVer](https://semver.org);
v1.0.0 is declared by the project owner.

## [Unreleased]
### Added
- Offload pipeline: naming/templates + dated `video|photo` folder layout,
  SQLite transfer history (sessions, hashes, verified flag, delete gating),
  resumable hashing HTTP downloader for `/v2` (Range, retry, .part files),
  offload planner/orchestrator with per-store mount probing and history dedup.
- Shared async bring-up helper (`core/connect.py`) and end-to-end CLI
  (`scripts/offload.py`) with `--dry-run`, filters, template override.
- UDP datalink transport + camera session (registration, playback hold,
  media-list query, status decode) ported from osmosis — hardware bring-up
  in progress.

## [0.2.0] - 2026-08-20
### Verified on hardware
- Osmo Pocket 4 Pro pairing end-to-end on Windows: BLE scan (new advert
  format, product type 218), approve-on-camera, already-paired fast path,
  WiFi SSID/password retrieval over BLE. Negotiated MTU 510 — camera fine.
- Camera AP wake (0x53/0x10 + 07/47 nudge), netsh WPA2 join at 5 GHz
  (1201 Mbps link), DHCP 192.168.2.x, camera HTTP server reachable.
- Battery telemetry (0x0D/0x02) already streaming over BLE.

## [0.1.0] - 2026-08-20
### Added
- Working BLE probe against real hardware (`scripts/probe.py`).

## [0.0.1] - 2026-08-20
### Added
- Project scaffold: Python 3.13, bleak 3, pytest; git repo initialized.
- DUML frame codec (SOF 0x55): CRC8/CRC16, encoder, decoder, resyncing
  stream parser. 19 unit tests pinned byte-exact against verbatim capture
  frames from the osmosis protocol reference.
- BLE command builders: session open/keepalive, wake, SetPairingPIN,
  pairing-approval ACK, GetWifiSsid/Password/Mac, ConnectToWiFi.
- BLE layer: DJI camera scanner (classic + new advert formats), GATT link
  with the documented bring-up order (dual CCCD subscribe, fff4 arm,
  paced write-without-response), request/retry + keepalive machinery.
- Probe script (`scripts/probe.py`): scan, pair (on-camera approval),
  retrieve WiFi credentials; full hex logging to `logs/`.
- `scripts/enable-radios.ps1`: switches WiFi/Bluetooth software radios on.
