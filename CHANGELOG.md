# Changelog

All notable changes to Osmo Offload. Versions follow [SemVer](https://semver.org);
v1.0.0 is declared by the project owner.

## [Unreleased]

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
