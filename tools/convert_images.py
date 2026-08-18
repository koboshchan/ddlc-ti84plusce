#!/usr/bin/env python3
"""Turn build/manifest.json (from image_resolve.py) into a convimg.yaml and run it.

Two palette groups, per docs/FORMAT.md:
  - pal_game: one shared palette across every sprite and every "shared"
    (background/solid-color) scene, since any character can appear on any
    background. Indices 0-7 are pinned via fixed-entries for the engine's
    reserved UI colors; indices 10-49 for the real textbox/namebox art's
    own colors (see UI_BOX_FIXED_ENTRIES).
  - one small palette per "own" (CG) scene, since CGs render full-screen and
    alone and don't need to share a palette with anything else -- except the
    dialogue box, which still draws over a CG too, so the same indices
    0-7/10-49 are pinned here as well.

Output is `type: bin` (raw quantized+compressed bytes plus a listing) into
build/gfx/ -- packaging those bytes into TI AppVars is import_game.py's job,
via convbin, which is where the 64KB-per-variable splitting decision belongs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

# Matches docs/FORMAT.md's reserved-index table. Exact-pixel reconciliation
# with src/render.c's placeholder gfx_palette constants (raw RGB565, a
# different color space than convimg's 24-bit hex) is Milestone 3 work; this
# reserves the same index *layout* now so the two line up structurally.
FIXED_ENTRIES = [
    (0, "#FF00FF"),  # transparent sentinel -- never drawn opaque
    (1, "#000000"),  # black
    (2, "#FFFFFF"),  # white
    (3, "#5A1030"),  # dialogue box fill
    (4, "#F7C9DC"),  # dialogue box edge
    (5, "#FFB8D9"),  # speaker name
    (6, "#FFE060"),  # menu highlight
    (7, "#201020"),  # shadow
]

# Pinned only in the title palette, on top of FIXED_ENTRIES. These are the two
# flat colors DDLC's nav panel overlay turns out to be made of -- render.c
# draws that panel as two rectangles rather than shipping ~18KB of art, which
# needs their indices to be known ahead of time (and exact, so the panel
# doesn't band against the background). Valid only while the title palette is
# loaded; see render.h.
TITLE_FIXED_ENTRIES = [
    (8, "#FFE6F4"),  # nav panel fill  -- main_menu.png x 0..279
    (9, "#FFBDE1"),  # nav panel edge  -- main_menu.png x 280..309
]

# Pinned in pal_game *and* every CG's own palette (not pal_title -- the
# dialogue box never overlaps the title screen), on top of FIXED_ENTRIES.
# The real textbox.png/namebox.png art (image_resolve.py's ui_box_art())
# uses ~2000/~27 distinct colors respectively -- far more than the 8 flat
# semantic colors above cover -- to render its gradient/dot-pattern shading.
# render_box() draws that art over *every* scene including a CG's own, but
# a CG's own palette is quantized purely from that one image's own pixels,
# with zero awareness of what colors the textbox/namebox art actually
# needs -- so without reserving them here too, only the 8 flat colors above
# survived the swap and the other ~200+ got reassigned to whatever best
# fit the CG instead, rendering the dialogue box in essentially arbitrary
# colors over any CG (confirmed live: a namebox that should be pink came
# out solid red over one). Every CG gets its own independently-quantized
# palette (128 of them) rather than one shared CG palette, and the
# textbox/namebox art itself is baked to raw palette-index bytes exactly
# once (against pal_game) and blitted unchanged under whatever palette is
# active (src/render.c's render_box(), via blit_raw()) -- so these 40
# colors have to land on the same 40 indices with the same RGB under every
# one of those 128 palettes too, which rules out letting each CG quantize
# them freely for a per-CG-optimal fit.
#
# This list is convimg's own quantizer's output (max-entries: 40,
# quality: 10), not a hand-rolled approximation: stitch ui_textbox.png and
# ui_namebox.png into one composite image, quantize *that* on its own down
# to 40 colors, and read the resulting palette back out. An earlier version
# of this list came from PIL's Image.quantize(colors=40, MEDIANCUT)
# instead, which is a cruder/non-dithering-aware quantizer than convimg's
# own and visibly mis-colored some of the box's dot-pattern shading as a
# result -- convimg's quantizer is what actually decides the real palettes
# below, so asking it for this reference palette too, rather than a
# different algorithm, is what makes it representative.
UI_BOX_FIXED_ENTRIES = [
    (10, "#000000"), (11, "#5A1031"), (12, "#8C516B"), (13, "#CE6D9C"),
    (14, "#CE759C"), (15, "#CE79A5"), (16, "#CE7DA5"), (17, "#CE82A5"),
    (18, "#CEB6BD"), (19, "#D675A5"), (20, "#D682A5"), (21, "#D68AAD"),
    (22, "#D68EAD"), (23, "#D692B5"), (24, "#D696B5"), (25, "#D69AB5"),
    (26, "#DE7DAD"), (27, "#DE86AD"), (28, "#DE8AB5"), (29, "#DEA2BD"),
    (30, "#DECAD6"), (31, "#E682AD"), (32, "#E68EB5"), (33, "#E692B5"),
    (34, "#E692BD"), (35, "#E696BD"), (36, "#EF8AB5"), (37, "#EF96BD"),
    (38, "#EF9AC5"), (39, "#EFAAC5"), (40, "#EFD7E6"), (41, "#F792BD"),
    (42, "#F79AC5"), (43, "#F79EC5"), (44, "#F7A2C5"), (45, "#F7A2CE"),
    (46, "#FFA2CE"), (47, "#FFA6CE"), (48, "#FFB2D6"), (49, "#FFE3EF"),
]

# A private palette used *only* to quantize the textbox/namebox art itself
# (see the "textbox" convert below for why). max-entries equals exactly the
# number of fixed-entries here (0-7, 8-9 as unreachable placeholders, 10-49),
# so there isn't a single free slot left for convimg's quantizer to assign
# -- every pixel is forced to land on one of the real 48 reserved colors.
# Indices 8-9 aren't reserved by pal_game/any CG palette (only pal_title
# reserves them), so they're pinned here to a duplicate of index 0's
# implausible sentinel magenta -- a color that will never be the nearest
# match for any real box pixel -- rather than left free, which would let
# a stray pixel land on an index whose color is unrelated (whatever
# pal_game/that CG happened to quantize there) everywhere else.
UI_BOX_PALETTE_ENTRIES = (
    FIXED_ENTRIES
    + [(8, "#FF00FF"), (9, "#FF00FF")]
    + UI_BOX_FIXED_ENTRIES
)


def build_yaml(manifest: dict, img_dir: Path, gfx_dir: Path, quality: int = 8) -> dict:
    # Every input image path is bare (no directory component at all) and
    # run_convimg() runs convimg with cwd=img_dir to match -- two real,
    # confirmed-on-Windows bugs ruled this out any other way:
    #
    # 1. An absolute path (what this used to pass, via .resolve()): convimg's
    #    own output-path handling on Windows doesn't recognize a
    #    drive-letter-prefixed absolute path (`E:\...`) as already absolute,
    #    and naively concatenates it onto `directory:` instead of taking
    #    just the basename -- produces a broken nested path
    #    (`gfx/E:\...\sprite.png.bin`) and a hard failure. POSIX absolute
    #    paths never hit this (a leading `/` reads as already-absolute
    #    everywhere), which is why this went unnoticed until building on an
    #    actual Windows host.
    #
    # 2. A relative path *with a directory component* (the first fix tried
    #    here, build-dir-relative so images read like `img/sprite.png`):
    #    convimg preserves that directory component in the OUTPUT filename
    #    too (`gfx/img/sprite.bin`), which doesn't exist as a directory --
    #    also a hard failure, also only surfaced on the real Windows build.
    #
    # A bare filename with convimg's cwd pointed straight at img_dir sidesteps
    # both: there's no directory component for either bug to mishandle.
    def rel(p: Path) -> str:
        return p.name

    sprite_files = [rel(img_dir / s["file"]) for s in manifest["sprites"]]
    shared_files = [rel(img_dir / s["file"])
                    for s in manifest["scenes"] if s["palette"] == "shared"]
    own_scenes = [s for s in manifest["scenes"] if s["palette"] == "own"]
    title_files = [rel(img_dir / t["file"]) for t in manifest.get("title_art", [])]
    title_bg = manifest.get("title_bg") or {}
    title_bg_file = rel(img_dir / title_bg["file"]) if title_bg else None
    poem_bg = manifest.get("poem_bg") or {}
    poem_bg_file = rel(img_dir / poem_bg["file"]) if poem_bg else None
    textbox = manifest.get("textbox") or {}
    textbox_file = rel(img_dir / textbox["file"]) if textbox else None
    namebox = manifest.get("namebox") or {}
    namebox_file = rel(img_dir / namebox["file"]) if namebox else None

    palettes = [{
        "name": "pal_game",
        "max-entries": 256,
        # quality 10 across ~200 sprites' full palette took ~10 CPU-minutes
        # in testing; 8 (libimagequant's own default) is markedly faster and
        # still good for DDLC's fairly flat anime shading. Override with
        # --quality for a final release build.
        "quality": quality,
        "fixed-entries": [{"color": {"index": i, "hex": h}}
                          for i, h in FIXED_ENTRIES + UI_BOX_FIXED_ENTRIES],
        # textbox_file/namebox_file deliberately aren't fed in here: their
        # colors are already guaranteed present via the fixed-entries above,
        # and their actual raw index bytes are quantized separately against
        # pal_uibox below (not pal_game), so pal_game's free (non-fixed)
        # slots are better spent entirely on sprites/backgrounds.
        "images": sprite_files + shared_files
                 + ([poem_bg_file] if poem_bg_file else []),
    }]
    if textbox_file or namebox_file:
        palettes.append({
            "name": "pal_uibox",
            "max-entries": len(UI_BOX_PALETTE_ENTRIES),
            "quality": 10,
            "fixed-entries": [{"color": {"index": i, "hex": h}}
                              for i, h in UI_BOX_PALETTE_ENTRIES],
            "images": [f for f in (textbox_file, namebox_file) if f],
        })
    converts = []
    outputs_converts = []
    outputs_palettes = ["pal_game"]
    if textbox_file or namebox_file:
        outputs_palettes.append("pal_uibox")

    if sprite_files:
        converts.append({
            # No zx0 here (unlike backgrounds below): a compressed sprite's
            # decompressed size isn't known ahead of a decompress call, and
            # zx0_Decompress has no bounds-checked API to guard against a
            # too-small destination -- rather than track per-sprite
            # uncompressed sizes just to size a scratch buffer safely, ship
            # sprites as plain rlet. src/assets.c then points a
            # gfx_rletsprite_t directly at the AppVar's flash bytes (it's
            # archived) and draws from there with zero copying and zero
            # decompression -- simpler and faster, at some archive size
            # cost that's affordable at the current bundle size.
            "name": "sprites", "palette": "pal_game", "style": "rlet",
            "transparent-index": 0, "dither": 0.4,
            "images": sprite_files,
        })
        outputs_converts.append("sprites")

    if shared_files:
        converts.append({
            # width-and-height off: convimg's dimension header is one byte
            # per axis (max 255), but backgrounds/CGs are a fixed 320x180 --
            # over the limit, and redundant anyway since BG_SIZE/CG_SIZE in
            # image_resolve.py already fix the size at conversion time.
            #
            # zx0: a background's decompressed size is always exactly
            # SCENE_BYTES (320x180, fixed above), so src/assets.c can
            # bounds-check a destination safely. assets_scene() decompresses
            # on every call (every typewriter tick, every idle-bob redraw --
            # not just an actual scene change), which is real per-frame cost;
            # a decompressed-results cache was tried to avoid that but didn't
            # fit real on-device RAM (graphx's own draw buffer plus a
            # resident script chunk already use most of it) -- see
            # assets_scene()'s comment. Compression stays on anyway: at real
            # game scale, uncompressed 24 backgrounds alone need ~1.3MB
            # against a real ~2.9MB archive, which doesn't fit at all.
            "name": "backgrounds", "palette": "pal_game", "style": "palette",
            "dither": 0.4, "width-and-height": False, "compress": "zx0",
            "images": shared_files,
        })
        outputs_converts.append("backgrounds")

    if poem_bg_file:
        converts.append({
            # Full-screen (POEM_BG_SIZE, 320x240), not the scene area's
            # BG_SIZE -- see image_resolve.py's poem_background(). Flat raw
            # indices like title_bg below: src/poem.c redraws this every
            # frame the minigame loop runs (once per input poll, not just on
            # a state change), so it can't afford a decode step any more
            # than title/splash's per-frame art can.
            "name": "poem_bg", "palette": "pal_game", "style": "palette",
            "dither": 0.4, "width-and-height": False,
            "images": [poem_bg_file],
        })
        outputs_converts.append("poem_bg")

    if textbox_file:
        converts.append({
            # Same reasoning as poem_bg above: flat raw indices, no
            # compression -- src/render.c's render_box() draws this every
            # typewriter tick (see assets_textbox()'s own comment).
            #
            # Quantized against pal_uibox, not pal_game: pal_uibox has zero
            # free slots (max-entries equals its own fixed-entries count),
            # so every pixel is forced onto one of the 48 real reserved
            # colors at the same indices pal_game/every CG palette pins them
            # to. Quantizing against pal_game instead (as this used to)
            # lets a pixel land on one of pal_game's *free* indices when
            # that happens to be a nearer match than any of the 48 reserved
            # ones -- fine under pal_game itself, but wrong under a CG's
            # palette, where that same index means a completely different,
            # CG-specific color. (An earlier attempt used convimg's
            # `omit-indices` to try to rule those indices out instead --
            # don't reintroduce that: omit-indices *deletes* every pixel
            # byte matching an omitted index from the output stream
            # entirely (see convimg's image_remove_omits()), which corrupts
            # the raster rather than remapping it.)
            "name": "textbox", "palette": "pal_uibox", "style": "palette",
            "dither": 0.4, "width-and-height": False,
            "images": [textbox_file],
        })
        outputs_converts.append("textbox")

    if namebox_file:
        converts.append({
            # See the "textbox" convert above for why this uses pal_uibox.
            "name": "namebox", "palette": "pal_uibox", "style": "palette",
            "dither": 0.4, "width-and-height": False,
            "images": [namebox_file],
        })
        outputs_converts.append("namebox")

    # The title screen gets its own palette rather than sharing pal_game.
    # Its art is a separate DDLC asset set (pastel pinks, the logo's flat
    # brand colors) with little overlap with in-game classroom scenes, so
    # folding it into the shared palette would cost color fidelity on both
    # sides. It can afford a private palette where a CG can't easily: the
    # title is a distinct screen mode, so src/assets.c swaps the whole
    # palette on entry/exit rather than mid-scene.
    #
    # FIXED_ENTRIES is pinned here too, so index 0 stays the rlet transparent
    # sentinel and render.c's COL_* UI colors (menu text, highlight) keep
    # meaning the same thing while the title palette is loaded.
    if title_files or title_bg_file:
        palettes.append({
            "name": "pal_title", "max-entries": 256, "quality": quality,
            "fixed-entries": [{"color": {"index": i, "hex": h}}
                              for i, h in FIXED_ENTRIES + TITLE_FIXED_ENTRIES],
            "images": title_files + ([title_bg_file] if title_bg_file else []),
        })
        outputs_palettes.append("pal_title")

    if title_files:
        converts.append({
            # rlet like sprites: the character art, logo, and nav panel are
            # all alpha-cut shapes drawn over the background.
            "name": "title", "palette": "pal_title", "style": "rlet",
            "transparent-index": 0, "dither": 0.4,
            "images": title_files,
        })
        outputs_converts.append("title")

    if title_bg_file:
        converts.append({
            # Flat raw indices, like backgrounds -- assets_title_bg() copies
            # rows straight out of it every frame, so no decode step.
            "name": "title_bg", "palette": "pal_title", "style": "palette",
            "dither": 0.4, "width-and-height": False,
            "images": [title_bg_file],
        })
        outputs_converts.append("title_bg")

    # One palette + convert block per CG: each needs its own palette, so a
    # shared block (like sprites/backgrounds above) can't express this.
    for i, scene in enumerate(own_scenes):
        name = f"cg_{i:03d}"
        path = rel(img_dir / scene["file"])
        palettes.append({
            # FIXED_ENTRIES and UI_BOX_FIXED_ENTRIES pinned here too (like
            # pal_title): the dialogue box stays on screen over a CG, so its
            # COL_* indices *and* its real textbox/namebox art both need to
            # mean the same thing under this palette as they do under
            # pal_game -- see UI_BOX_FIXED_ENTRIES's own comment.
            "name": f"pal_{name}", "max-entries": 256, "quality": 10,
            "fixed-entries": [{"color": {"index": i, "hex": h}}
                              for i, h in FIXED_ENTRIES + UI_BOX_FIXED_ENTRIES],
            "images": [path],
        })
        converts.append({
            "name": name, "palette": f"pal_{name}", "style": "palette",
            "width-and-height": False, "compress": "zx0", "images": [path],
        })
        outputs_palettes.append(f"pal_{name}")
        outputs_converts.append(name)

    return {
        "palettes": palettes,
        "converts": converts,
        "outputs": [{
            "type": "bin",
            # Relative from img_dir (see rel()'s own docstring -- that's
            # where convimg actually runs from), not img_dir itself.
            "directory": os.path.relpath(gfx_dir, img_dir),
            "palettes": outputs_palettes,
            "converts": outputs_converts,
        }],
    }


def run_convimg(yaml_path: Path, img_dir: Path, threads: int | None = None) -> None:
    if shutil.which("convimg") is None:
        sys.exit("convimg not found on PATH. Export CEdev/bin, e.g.:\n"
                  '  export PATH="$HOME/CEdev/bin:$PATH"')
    # convimg's own default is a fixed 4 threads regardless of the host --
    # a huge waste on a many-core machine for the palette quantization pass,
    # which is CPU-bound and embarrassingly parallel across images. Use
    # every logical core by default rather than convimg's conservative
    # built-in default.
    threads = threads or os.cpu_count() or 4
    # cwd=img_dir, not yaml_path.parent: build_yaml()'s own image paths are
    # bare filenames assuming that cwd (see its docstring for the two real
    # Windows bugs that forced this).
    yaml_rel = os.path.relpath(yaml_path, img_dir)
    subprocess.run(["convimg", "-i", yaml_rel, "--threads", str(threads)],
                   check=True, cwd=img_dir)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-dir", type=Path, default=Path("build"))
    ap.add_argument("--quality", type=int, default=8, help="palette quantization quality, 1-10 (default 8)")
    ap.add_argument("--dry-run", action="store_true", help="write the YAML but don't run convimg")
    args = ap.parse_args()

    manifest_path = args.build_dir / "manifest.json"
    if not manifest_path.is_file():
        sys.exit(f"{manifest_path} not found -- run image_resolve.py first")
    manifest = json.loads(manifest_path.read_text())

    gfx_dir = args.build_dir / "gfx"
    gfx_dir.mkdir(parents=True, exist_ok=True)
    img_dir = args.build_dir / "img"

    doc = build_yaml(manifest, img_dir, gfx_dir, quality=args.quality)
    yaml_path = args.build_dir / "convimg.yaml"
    yaml_path.write_text(yaml.safe_dump(doc, sort_keys=False))
    print(f"wrote {yaml_path} "
          f"({len(doc['palettes'])} palettes, {len(doc['converts'])} convert groups)")

    if args.dry_run:
        return 0

    run_convimg(yaml_path, img_dir)
    print(f"done: converted images in {gfx_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
