#!/usr/bin/env python3
"""Turn two of DDLC's own bundled fonts into one convfont fontpack.

Only fonts with a license that actually permits this are used -- see
extract.py's own docstring and docs/FORMAT.md's "Text rendering" section.
Index 0 in the pack is Halogen (public domain), the default/body-text font;
index 1 is RifficFree-Bold (free for personal and commercial use), DDLC's
own real font for the speaker namebox (gui.rpy's name_font) and several
menu labels. src/assets.c reads them back out by that same fixed index via
fontlibc's fontlib_GetFontByIndex() -- the pack's own internal names aren't
used as a lookup key on-calc, so nothing here needs to match render.c
symbol-for-symbol, just index-for-index.

Output is a single packed binary (fontlib_font_pack_t) -- packaging that
into a TI AppVar is import_game.py's job, same split of responsibility as
convert_images.py/convimg's `type: bin` output.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import ttf_to_convfont

# (source path under raw_dir, pixel height passed to ttf_to_convfont, name
# recorded in each glyph's own comment field). Order fixes the pack index
# fontlib_GetFontByIndex()/src/assets.c read back by -- see module
# docstring. 12px keeps proportions close to the engine's existing ~10px
# dialogue line pitch (src/render.c) while leaving enough width for
# RifficFree-Bold's boldest glyphs not to collide with each other -- see
# the FORMAT.md note on its lowercase "i" losing its dot below this.
FONTS = [
    ("gui/font/Halogen.ttf", 12, "Halogen"),
    ("gui/font/RifficFree-Bold.ttf", 12, "RifficFreeBold"),
]


def build_fontpack(raw_dir: Path, build_dir: Path) -> Path:
    if shutil.which("convfont") is None:
        sys.exit('convfont not found on PATH. Export CEdev/bin, e.g.:\n'
                  '  export PATH="$HOME/CEdev/bin:$PATH"')

    font_dir = build_dir / "fonts"
    font_dir.mkdir(parents=True, exist_ok=True)

    convfont_args = ["convfont", "-o", "fontpack",
                      "-N", "DDLCFonts", "-A", "ddlc-ti84plusce",
                      "-C", "See LICENSE -- not Team Salvato's",
                      "-D", "Halogen (index 0) + RifficFree-Bold (index 1)",
                      "-V", "1", "-P", "ASCII"]

    for rel_path, height, name in FONTS:
        ttf_path = raw_dir / rel_path
        if not ttf_path.is_file():
            sys.exit(f"missing {ttf_path} -- run extract.py first "
                      "(fonts.rpa must be extracted, see extract.py's own docstring)")
        out_path = font_dir / f"{name}.convfont"
        text = ttf_to_convfont.build_convfont(ttf_path, height, name)
        out_path.write_text(text)
        convfont_args += ["-t", str(out_path)]

    out_bin = font_dir / "fontpack.bin"
    convfont_args.append(str(out_bin))

    subprocess.run(convfont_args, check=True)
    return out_bin


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-dir", type=Path, default=Path("assets/raw"))
    ap.add_argument("--build-dir", type=Path, default=Path("build"))
    args = ap.parse_args()

    out = build_fontpack(args.raw_dir, args.build_dir)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
