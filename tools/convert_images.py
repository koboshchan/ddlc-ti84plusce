#!/usr/bin/env python3
"""Turn build/manifest.json (from image_resolve.py) into a convimg.yaml and run it.

Two palette groups, per docs/FORMAT.md:
  - pal_game: one shared palette across every sprite and every "shared"
    (background/solid-color) scene, since any character can appear on any
    background. Indices 0-7 are pinned via fixed-entries for the engine's
    reserved UI colors.
  - one small palette per "own" (CG) scene, since CGs render full-screen and
    alone and don't need to share a palette with anything else.

Output is `type: bin` (raw quantized+compressed bytes plus a listing) into
build/gfx/ -- packaging those bytes into TI AppVars is import_game.py's job,
via convbin, which is where the 64KB-per-variable splitting decision belongs.
"""

from __future__ import annotations

import argparse
import json
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


def build_yaml(manifest: dict, img_dir: Path, gfx_dir: Path, quality: int = 8) -> dict:
    sprite_files = [str((img_dir / s["file"]).resolve()) for s in manifest["sprites"]]
    shared_files = [str((img_dir / s["file"]).resolve())
                    for s in manifest["scenes"] if s["palette"] == "shared"]
    own_scenes = [s for s in manifest["scenes"] if s["palette"] == "own"]
    title_files = [str((img_dir / t["file"]).resolve()) for t in manifest.get("title_art", [])]
    title_bg = manifest.get("title_bg") or {}
    title_bg_file = str((img_dir / title_bg["file"]).resolve()) if title_bg else None

    palettes = [{
        "name": "pal_game",
        "max-entries": 256,
        # quality 10 across ~200 sprites' full palette took ~10 CPU-minutes
        # in testing; 8 (libimagequant's own default) is markedly faster and
        # still good for DDLC's fairly flat anime shading. Override with
        # --quality for a final release build.
        "quality": quality,
        "fixed-entries": [{"color": {"index": i, "hex": h}} for i, h in FIXED_ENTRIES],
        "images": sprite_files + shared_files,
    }]
    converts = []
    outputs_converts = []
    outputs_palettes = ["pal_game"]

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
            # bounds-check a destination safely. An earlier version shipped
            # these uncompressed because assets_scene() ran
            # zx0_Decompress every single frame draw_background() was
            # called -- every typewriter tick, every idle-bob redraw -- and
            # that decode was the real bottleneck behind "laggy" text. Now
            # decompressed once into a small cache on an actual scene
            # change and memcpy'd from there every frame instead, so the
            # expensive part no longer runs per-frame -- see assets.c's
            # assets_scene(). Worth it at real game scale: uncompressed,
            # 24 backgrounds alone need ~1.3MB against a real ~2.9MB
            # archive.
            "name": "backgrounds", "palette": "pal_game", "style": "palette",
            "dither": 0.4, "width-and-height": False, "compress": "zx0",
            "images": shared_files,
        })
        outputs_converts.append("backgrounds")

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
        path = str((img_dir / scene["file"]).resolve())
        palettes.append({
            # FIXED_ENTRIES pinned here too (like pal_title): the dialogue box
            # stays on screen over a CG, so its COL_* indices need to mean the
            # same thing under this palette as they do under pal_game.
            "name": f"pal_{name}", "max-entries": 256, "quality": 10,
            "fixed-entries": [{"color": {"index": i, "hex": h}} for i, h in FIXED_ENTRIES],
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
            "directory": str(gfx_dir.resolve()),
            "palettes": outputs_palettes,
            "converts": outputs_converts,
        }],
    }


def run_convimg(yaml_path: Path) -> None:
    if shutil.which("convimg") is None:
        sys.exit("convimg not found on PATH. Export CEdev/bin, e.g.:\n"
                  '  export PATH="$HOME/CEdev/bin:$PATH"')
    subprocess.run(["convimg", "-i", yaml_path.name], check=True, cwd=yaml_path.parent)


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

    doc = build_yaml(manifest, args.build_dir / "img", gfx_dir, quality=args.quality)
    yaml_path = args.build_dir / "convimg.yaml"
    yaml_path.write_text(yaml.safe_dump(doc, sort_keys=False))
    print(f"wrote {yaml_path} "
          f"({len(doc['palettes'])} palettes, {len(doc['converts'])} convert groups)")

    if args.dry_run:
        return 0

    run_convimg(yaml_path)
    print(f"done: converted images in {gfx_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
