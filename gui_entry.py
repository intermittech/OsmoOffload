"""Osmo Offload — packaged entry point (PyInstaller target)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from osmooffload.ui.app import run_app  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="demo data, no camera I/O")
    ap.add_argument("--screenshot", help="save a PNG of the window and exit (diagnostics)")
    args = ap.parse_args()
    raise SystemExit(run_app(mock=args.mock, screenshot=args.screenshot))
