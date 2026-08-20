#!/usr/bin/env python3
"""Rasterize a TTF/OTF into convfont's own text-format font (`.convfont`),
covering printable ASCII (32-126) -- this engine's whole charset (see
render.c/text.c: no Unicode substitution table beyond the documented
OP_GLITCHTEXT ASCII stand-in).

convfont (part of the CE C toolchain, same suite as convimg) turns a
`.convfont` file into the packed binary fontlib_font_t data
tools/import_game.py bundles as an AppVar and src/render.c loads via
fontlibc at runtime -- this script is only the TTF -> `.convfont` bridge;
it doesn't invoke convfont itself (see import_game.py's do_fonts()).

Usage: python3 tools/ttf_to_convfont.py <font.ttf> <height_px> <out.convfont> [--name NAME]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FIRST_CP = 32   # ' '
LAST_CP = 126   # '~'

# Pixel coverage (0-255 grayscale/alpha) at or above this counts as "on".
# PIL antialiases small glyphs, so a mid-range cutoff -- not >=128 -- keeps
# thin stems (the ascender of 'l', serifs) from vanishing at these sizes.
THRESHOLD = 96


def rasterize_glyph(font: ImageFont.FreeTypeFont, ch: str, cell_w: int,
                     cell_h: int, ascent: int) -> tuple[list[list[bool]], int]:
    """(bitmap, advance_width) for @p ch, top-left-anchored at (0, 0) and
    baseline-aligned via @p ascent so every glyph in the font shares one
    consistent baseline row regardless of its own bounding box -- without
    this, descenders/ascenders would each reset their own vertical origin
    and glyphs would sit at visibly different heights side by side."""
    img = Image.new("L", (cell_w, cell_h), 0)
    draw = ImageDraw.Draw(img)
    draw.text((0, ascent), ch, font=font, fill=255, anchor="ls")
    px = img.load()
    bitmap = [[px[x, y] >= THRESHOLD for x in range(cell_w)] for y in range(cell_h)]

    natural_advance = round(draw.textlength(ch, font=font))
    # A flat "+1 no matter what" here used to read as too tight for one font
    # (0 gap at these pixel sizes -- draw.textlength()'s side bearing
    # rounds away to nothing) and visibly too loose for another (Aller_Rg's
    # own side bearing is already real at this size, so adding to it
    # doubled the gap -- confirmed live: "That girl is Sayori..." spaced
    # noticeably wider than real DDLC's own rendering). Basing the minimum
    # on the glyph's own actual rendered ink instead of the font's reported
    # metric fixes both: only pad up to a 1px gap after the *real* rightmost
    # lit pixel, so a font whose natural advance already clears that does
    # nothing extra, and one that doesn't gets exactly enough to stop
    # touching its neighbor.
    ink_right = -1
    for y in range(cell_h):
        for x in range(cell_w):
            if bitmap[y][x]:
                ink_right = max(ink_right, x)
    advance = max(natural_advance, ink_right + 2)
    if advance <= 0:
        advance = max(1, cell_w // 2)  # space and other zero-ink glyphs still need real width
    return bitmap, advance


def build_convfont(ttf_path: Path, height_px: int, name: str) -> str:
    # PIL's "size" is an em-square target, not a literal glyph pixel height --
    # oversize the request and let getmetrics() report the real ascent/descent
    # actually used, then rely on the per-glyph threshold/crop below rather
    # than assuming size == height_px directly (fonts vary in how tall their
    # em actually renders relative to the requested size).
    font = ImageFont.truetype(str(ttf_path), height_px)
    ascent, descent = font.getmetrics()
    cell_h = ascent + descent
    cell_w = height_px * 2  # generous headroom; glyphs are cropped to their own advance below

    lines = [
        "convfont",
        ": Font Metadata",
        ": Font Properties",
        f"Height: {cell_h}",
        "Double width: False",
        "Code page: ASCII",
        ": Font Metrics",
        "Weight: Normal",
        "Style: Sans-serif",
        "Style: Upright",
        "Style: Proportional",
        "Italic adjust: 0",
        "Space above: 1",
        "Space below: 1",
        f"Cap height: {ascent}",
        f"x-height: {round(ascent * 0.6)}",
        f"Baseline: {ascent}",
        "Font Data:",
    ]

    for cp in range(FIRST_CP, LAST_CP + 1):
        ch = chr(cp)
        bitmap, advance = rasterize_glyph(font, ch, cell_w, cell_h, ascent)
        width = max(1, min(advance, cell_w))
        lines.append(f"Code point: {cp}")
        lines.append(f"Name: {name} U+{cp:08X}")
        lines.append(f"Unicode: U+{cp:08X}")
        lines.append(f"Width: {width}")
        lines.append("Data:")
        for row in bitmap:
            lines.append("".join("#" if on else " " for on in row[:width]))

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ttf", type=Path)
    ap.add_argument("height", type=int, help="target pixel height (roughly cap height)")
    ap.add_argument("out", type=Path)
    ap.add_argument("--name", default=None, help="font name recorded in glyph comments")
    args = ap.parse_args()

    name = args.name or args.ttf.stem
    text = build_convfont(args.ttf, args.height, name)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
