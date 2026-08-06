#!/usr/bin/env python3
"""Extract DDLC's .rpa archives into assets/raw/ via unrpa.

Usage: python3 tools/extract.py <path-to-DDLC-game-dir> [--dest assets/raw]

Only scripts.rpa and images.rpa are extracted. audio.rpa is skipped (the
TI-84 Plus CE has no audio hardware) and fonts.rpa is skipped (the engine
uses graphx's built-in font; any font it ships must be open-licensed, not
Team Salvato's, per LICENSE).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ARCHIVES = ("scripts.rpa", "images.rpa")


def extract(game_dir: Path, dest: Path) -> None:
    if shutil.which("unrpa") is None:
        sys.exit("unrpa not found on PATH. Install it with: pip3 install -r tools/requirements.txt")

    dest.mkdir(parents=True, exist_ok=True)
    for name in ARCHIVES:
        archive = game_dir / name
        if not archive.is_file():
            sys.exit(f"missing {archive} -- is {game_dir} really a DDLC 'game' directory?")
        print(f"extracting {name}...")
        subprocess.run(
            ["unrpa", "-m", "-p", str(dest), "--continue-on-error", str(archive)],
            check=True,
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game_dir", type=Path, help="path to DDLC's 'game' directory")
    ap.add_argument("--dest", type=Path, default=Path("assets/raw"))
    args = ap.parse_args()

    extract(args.game_dir.expanduser().resolve(), args.dest)
    print(f"done: extracted into {args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
