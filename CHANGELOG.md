# Changelog

All notable changes to Osmo Offload. Versions follow [SemVer](https://semver.org);
v1.0.0 is declared by the project owner.

## [Unreleased]

## [0.7.0] - 2026-08-20
### Verified on hardware — delete-from-camera
- "Free up camera" deletes only files with a completed, size-verified transfer
  on record: one at a time, playback re-asserted before each, status checked
  (0x0000), then an authoritative RE-LIST confirms what is actually gone; the
  history rows gain deleted-from-camera timestamps. Live run: 2/2 clips
  (internal + SD store), 1.75 GB freed, camera lists 0 files after.
  Never re-sends on a silent reply (handles get reused); duplicate handles
  are refused for all holders.
- Pocket 4 Pro SD handle geometry confirmed: base 0x00100000, step 0x40.
### Changed
- "Slate & Ember" design iteration: fresh graphite neutrals; amber demoted to
  small accents; primary action is now a quiet light-neutral button at natural
  width; warm data colors (battery gradient, copper/sand storage) unchanged.
- Confirm dialog + toasts for the delete flow; "Free up camera…" lives in the
  Media tab with a danger outline.

## [0.6.0] - 2026-08-20
### Verified on hardware
- Media tab thumbnails fetched live from the camera (`.scr` JPEGs over /v2,
  cached per camera) on the Osmo Pocket 4 Pro.
### Added
- "Ember" design language (built with ui-ux-pro-max 2.13 data): warm dark
  neutrals, amber brand/interactive color, battery bar with red->amber->green
  gradient (>=50% greenish to full green), storage bars in two shades of one
  copper/sand hue with red override >=92% full, subtle eased value animations
  (interruptible, ~220 ms, skipped when hidden) — snappy by construction.
- Media tab: thumbnail grid, click-to-toggle selection, Select new / Clear,
  "Transfer selected (n)" cherry-pick transfers.
- Progress-signal throttling (<=10 Hz to the UI regardless of chunk rate).

## [0.5.0] - 2026-08-20
### Verified on hardware — GUI-driven transfer, dual-store
- Full transfer through the GUI: 1.5 GB clip from the **SD card** at 63 MB/s,
  with both mounts probed live (SD -> /v2 storage=0, internal -> 1 on the
  Pocket 4 Pro). Dedup left the internal clip untouched.
### Added
- GUI wired to the live core: worker-thread controller (asyncio BLE loop),
  connection state pill, live battery/storage card, queue with per-file
  progress, tray toasts, cancel, keep-PC-awake during transfers.
- History records the original DJI filename AND the renamed destination
  (`dest_name` column + migration); History tab shows Original name /
  Saved as (full path as tooltip); transfer log lines show `orig -> renamed`.
- UI/UX pass per ui-ux-pro-max v2.13 data: OLED-leaning palette, semantic
  state colors, focus/hover/disabled states, empty states, cursor affordances.

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
