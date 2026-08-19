#!/usr/bin/env python3
"""Extract DDLC's .rpa archives into assets/raw/ via unrpa.

Usage: python3 tools/extract.py <path-to-DDLC-game-dir> [--dest assets/raw]

scripts.rpa, images.rpa, and fonts.rpa are extracted. audio.rpa is skipped
(the TI-84 Plus CE has no audio hardware).

fonts.rpa itself ships several fonts under several different licenses (see
docs/FORMAT.md's "Text rendering" section) -- per LICENSE, this project only
*uses* the ones with a license that permits it (Halogen.ttf: public domain;
RifficFree-Bold.ttf: free for personal and commercial use). The rest of the
pack (including Aller_Rg.ttf, DDLC's own actual dialogue font, which is
Dalton Maag's commercial font under a 25-user/verbatim-redistribution-only
free tier) is extracted here like every other asset but never read by
tools/convert_fonts.py or baked into a build.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ARCHIVES = ("scripts.rpa", "images.rpa", "fonts.rpa")


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
