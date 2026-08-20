# Changelog

All notable changes to Osmo Offload. Versions follow [SemVer](https://semver.org);
v1.0.0 is declared by the project owner.

## [Unreleased]

## [0.4.0] - 2026-08-20
### Verified on hardware — first full wireless offload
- End-to-end: BLE wake -> AP join -> datalink -> playback -> list -> plan ->
  download. 252.5 MB clip in 4 s (**61.4 MB/s avg**), valid MP4, landed in
  `<base>\<camera>\<date>\video\`. Rerun skips it ("already offloaded").
- Battery + dual-store storage decoded live (SD absent reads 0/0 as documented).
### Added
- Offload pipeline: naming/templates + dated `video|photo` folder layout,
  SQLite transfer history (sessions, hashes, verified flag, delete gating),
  resumable hashing HTTP downloader for `/v2` (Range, retry, .part files),
  offload planner/orchestrator with per-store mount probing and history dedup.
- Shared async bring-up helper (`core/connect.py`) and end-to-end CLI
  (`scripts/offload.py`) with `--dry-run`, filters, template override.
- GUI skeleton (PySide6): dark theme, tray mode + start-minimized, camera
  card with battery/storage bars in the locked display format, tabs.
### Fixed
- History DB usable from worker threads (`check_same_thread=False`).

## [0.3.0] - 2026-08-20
### Verified on hardware — datalink + media list
- UDP 9004 handshake (fix: 40-byte SYN had lost 4 bytes in transcription —
  now derived from the reference frame with an import-time guard), TCP-7001
  poke, registration, playback hold (with 0x01/0x01 fallback), chunked
  0x00/0x27 manifest reassembly, per-store split (SD/internal), record decode
  (paths, sizes, handles, thumbs). Stale-session quirk recovers via retry.

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
