"""GUI launcher. `--mock` fills the window with fake data (no camera I/O)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from osmooffload.ui.app import run_app

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    args = ap.parse_args()
    raise SystemExit(run_app(mock=args.mock))
