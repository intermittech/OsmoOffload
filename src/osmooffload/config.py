"""App state persistence.

For now everything lives in ./config/appstate.json next to the repo (gitignored).
Will move to %APPDATA%/OsmoOffload when the app is packaged.

The pairing `identifier` is minted once per install and NEVER rotated — the
camera stores its approval under it (rotating forces a new approval and burns
a remembered slot; retries with a different identifier read as a second app).
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):  # packaged exe: per-user app data
        return Path(os.environ.get("APPDATA", Path.home())) / "OsmoOffload"
    return Path(__file__).resolve().parents[2]


CONFIG_DIR = _base_dir() / "config"
STATE_FILE = CONFIG_DIR / "appstate.json"
LOG_DIR = _base_dir() / "logs"


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def get_identifier(state: dict[str, Any]) -> str:
    ident = state.get("identifier")
    if not ident:
        ident = secrets.token_hex(16)  # 32 hex chars, the shape DJI apps use
        state["identifier"] = ident
        save_state(state)
    return ident


def remember_camera(state: dict[str, Any], address: str, **fields: Any) -> None:
    cams = state.setdefault("cameras", {})
    cam = cams.setdefault(address, {})
    cam.update({k: v for k, v in fields.items() if v is not None})
    save_state(state)
