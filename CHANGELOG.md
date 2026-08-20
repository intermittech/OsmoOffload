# Changelog

All notable changes to Osmo Offload. Versions follow [SemVer](https://semver.org);
v1.0.0 is declared by the project owner.

## [Unreleased]

## [0.13.1] - 2026-08-20
### Changed
- "Last 24 h" replaced by **"Only synced"** — the exclusive mirror of "Only
  unsynced": selection becomes exactly the files already at the destination.
  Two clicks now cover "delete everything I've already synced from the
  camera" without touching unsynced footage.

## [0.13.0] - 2026-08-20
### Fixed
- **Changing the base folder now resyncs correctly.** "Already on disk" is
  decided by the filesystem at the CURRENT destination — never by the history
  database alone. Point the app at a new/empty folder and everything on the
  camera becomes transferable again (wireless and USB paths; regression test).
  The delete double-warning wording now says "no copy at the current
  destination folder" accordingly.
### Added
- **"Only unsynced"** selection button (between Last 24 h and None): selection
  becomes exactly the files not present at the destination folder yet.

## [0.12.1] - 2026-08-20
### Fixed
- Media tile: a checked tile now shows its kind-colored border, not just the
  ticked checkbox. Two bugs from an earlier interleaved edit: the border was
  styled only on toggle (never on a tile that started checked), and the
  filename label plus the initial style call were stranded as dead code inside
  the timestamp parser — so tiles also lost their filename captions. Checkbox
  is now built last, connected before its initial state is set, with an
  explicit initial restyle; labels restored.

## [0.12.0] - 2026-08-20
### Fixed — delete now removes ALL selected files (was: only session-new ones)
- **Root cause found & fixed**: the manifest decoder only searched for the
  *video* marker `03 ff 19 06`, so stills (and some records written with the
  short `[fe|ff] 19 06` marker) decoded with handle 0 and were undeletable.
  Now anchors on the `19 06` pair common to both shapes — handle @ −10, size
  @ −14. Ground-truthed on a Pocket 4 Pro (seq 5/6/7 → 0x40100140/180/1c0).
  Verified live: all 3 remaining files (2 JPG + 1 MP4) deleted, camera empty.
- Deletes now refresh the datalink session before the ~40 s write window ages
  out (this was why an earlier delete only removed the first few files), and
  retry a stale/unanswered list by reopening.
- Safe handle derivation for any still genuinely lacking a marker: fits
  base+seq×step from ≥3 agreeing known handles, never collides with a known
  handle, and every derived delete is verified by re-list with abort-if-the-
  wrong-file-vanishes.
### Changed — selection & delete UX (user requests)
- Delete now removes **exactly the selected files** (not an auto-filtered set).
  On-disk files are selectable; deleting never-transferred files triggers a
  **second, louder confirmation** listing them.
- Selection buttons: **All / All video / All photo / Last 24 h / None** —
  colored to match media kinds (All = amber↔teal blend, video = amber,
  photo = teal); kind/time buttons are additive, None clears.
- **Queue shows only the currently selected files** (nothing selected → empty).
- Disk badge redrawn as a database/HDD cylinder (was a floppy).
- **Auto-connect on open** (unless boot-started minimized to tray).
- Connect-time estimate recomputed from real logs (now ~21 s).

## [0.11.0] - 2026-08-20
### Changed — Media grid redesign (user feedback)
- Tiles are now real checkbox tiles: checkbox top-left is THE selection
  mechanism, click anywhere on a tile toggles it, no more phantom rubber-band
  selection or clipped native check indicator.
- Selection buttons: **All / All video / All photo / None** — the kind buttons
  are additive (video then photo builds the set). Default: new files checked,
  files already on disk unchecked.
- Files already on disk: **grayscale thumbnail + disk badge** (top-right) and
  a disabled checkbox — they can't be re-selected.
- **Video and photo tiles have different border colors** (amber / teal).
- Thumbnails survive grid rebuilds (pixmap cache) — they no longer vanish
  after a transfer completes; fit is exact.
- One user-facing label — "already on disk" — replaces the confusing
  "already offloaded" / "exists on disk" pair (the internal distinction —
  history record vs matching file found at destination — now lives in the
  debug log only).
- **Legend** in the camera rail: border colors, disk badge, in-range dot.
- Queue/History **columns are user-resizable and reorderable** (drag).
- Session reports no longer print "Resolutions — unknown".

## [0.10.0] - 2026-08-20
### Changed — UX polish pass (all user-requested)
- **No more console-window flashes**: every netsh/ipconfig subprocess now
  spawns with CREATE_NO_WINDOW — connecting/transferring/deleting is silent.
- **History is one row per session** (transfer / refresh / delete / USB), each
  with When / Action / Files / Data / Speed / Result and an **Open report**
  button. The report is a formatted HTML page — summary, per-file table with
  original → saved-as names, destination folders, sizes, durations, speeds,
  hashes — with the full DEBUG log folded into a collapsed <details> at the
  bottom (hidden by default). Refresh and delete get reports too.
- **Stay connected after an action**: the camera connection is held warm for
  2 minutes (BLE + WiFi + a live datalink keepalive), so a follow-up refresh /
  transfer / delete skips the whole reconnect. Measured: **cold 56–85 s →
  warm 5 s** (~11–16×). Battery/storage keep updating live during the hold.
- **Faster cold connect**: early-exit BLE scan (returns the instant the advert
  is heard), known-camera path skips the 10004 port probe, and the AP-wake
  nudge fires sooner. A rolling average of real connect times drives a
  **countdown** ("usually ~30s — about 12s left") shown while connecting.
- **"Almost connected"** state once battery data arrives mid-connect.
- Transfer **speed shown in the pill** ("transferring · 62 MB/s").
- **Auto-refresh after transfer/delete** so the grid never shows stale state.
- Thumbnails letterboxed to fit their tiles; "Free up camera" → **"Delete
  files on camera…"**; wizard "Finish & pair" / "Bluetooth & WiFi" (&& typo).
### Fixed
- HTTP 416 on resume: a stale/complete `.part` past EOF is now finalized (or
  discarded and restarted) instead of failing the file.

## [0.9.1] - 2026-08-20
### Fixed
- All user-facing paths now display Windows-style backslashes: defaults,
  the base-folder fields (Qt's folder picker returns forward slashes — now
  normalized on pick and on save), stored settings, and mock data.

## [0.9.0] - 2026-08-20
### Verified on hardware
- Empty-camera handling: a camera that answers the list with zero records now
  reads "Camera is empty" instead of an error (live-tested); an unanswered
  list is still retried and reported honestly.
### Added
- Per-session DEBUG log files, recorded in history; the History tab gained an
  **Open log** button on every transfer row (plus original name / saved-as).
- **USB ingest** ("From USB"): strict DJI-volume detection (DCIM + DJI markers,
  never a random stick), both banks, same dedup/naming/history/report pipeline.
- **First-run wizard**: base folder, radio check/enable, guided pairing.
- **Update check** against GitHub releases (Settings → owner/repo; quiet check
  at startup, "Check now" button opens the release page when newer).
- **Packaging**: PyInstaller onefile exe (frozen config lives in
  %APPDATA%\OsmoOffload), Inno Setup installer with selectable
  "Start with Windows" + desktop shortcut, per-user install, clean uninstall
  that keeps user data; `scripts\build.ps1` builds both.
### Verified
- 31 unit tests; the frozen exe boots and renders identically (screenshot
  smoke test built into the binary via `--mock --screenshot`).

## [0.8.0] - 2026-08-20
### Added
- Session ingest reports: HTML + CSV written to `<base>\<camera>\_reports\`
  after each transfer — original/renamed names, sizes, hashes, store, per-clip
  duration + resolution, verified flags, and a shoot-summary header.
- Shoot summary via a local stdlib MP4 box parser (moov/mvhd + tkhd) —
  validated against real 4 Pro clips (4K durations parsed exactly).
- Settings: transfer filters (videos/photos), "write session reports",
  "open destination folder when done", and an after-transfer command hook
  (receives OSMO_CAMERA/COUNT/BYTES/DEST/REPORT/FILES in its environment).
### Fixed / hardened
- One automatic retry when connect fails transiently (AP no-show).
- Mid-transfer network-loss recovery: after repeated socket errors the
  downloader asks for a WiFi rejoin (netsh) and resumes from byte offset.

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
