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


def build_yaml(manifest: dict, img_dir: Path, gfx_dir: Path, quality: int = 8) -> dict:
    sprite_files = [str((img_dir / s["file"]).resolve()) for s in manifest["sprites"]]
    shared_files = [str((img_dir / s["file"]).resolve())
                    for s in manifest["scenes"] if s["palette"] == "shared"]
    own_scenes = [s for s in manifest["scenes"] if s["palette"] == "own"]

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
            "name": "sprites", "palette": "pal_game", "style": "rlet",
            "transparent-index": 0, "compress": "zx0", "dither": 0.4,
            "images": sprite_files,
        })
        outputs_converts.append("sprites")

    if shared_files:
        converts.append({
            # width-and-height off: convimg's dimension header is one byte
            # per axis (max 255), but backgrounds/CGs are a fixed 320x180 --
            # over the limit, and redundant anyway since BG_SIZE/CG_SIZE in
            # image_resolve.py already fix the size at conversion time.
            "name": "backgrounds", "palette": "pal_game", "style": "palette",
            "compress": "zx0", "dither": 0.4, "width-and-height": False,
            "images": shared_files,
        })
        outputs_converts.append("backgrounds")

    # One palette + convert block per CG: each needs its own palette, so a
    # shared block (like sprites/backgrounds above) can't express this.
    for i, scene in enumerate(own_scenes):
        name = f"cg_{i:03d}"
        path = str((img_dir / scene["file"]).resolve())
        palettes.append({
            "name": f"pal_{name}", "max-entries": 256, "quality": 10, "images": [path],
        })
        converts.append({
            "name": name, "palette": f"pal_{name}", "style": "palette",
            "compress": "zx0", "width-and-height": False, "images": [path],
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
