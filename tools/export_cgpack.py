#!/usr/bin/env python3
"""Export a full-resolution CG pack for an external FAT32 USB drive.

CGs are baked into the on-calc build at half resolution (image_resolve.py's
CG_SIZE, 160x90) to fit the archive budget. Every "own"-palette scene also
got its full-res (CG_FULL_SIZE, 320x180) composite saved to
build/cgpack_src/ before that downscale -- see _bake_flat()'s own comment.
This script turns those full-res composites into one raw .BIN file per CG,
quantized against the *same* 256-color palette convert_images.py already
chose for that CG's on-calc version (so only detail changes, not color), for
the user to copy onto a FAT32-formatted USB drive as /DDLC/ and plug into
the calculator via a USB-OTG adapter. See src/cgpack.c for the runtime side
that reads this back.

File layout, one CG{scene_id:03d}.BIN per "own"-palette scene: 320x180 =
57,600 raw palette-index pixel bytes, padded to a block boundary (57,856
bytes / 113 blocks; the last 256 are unused padding, never read past row
180's first pixels). No embedded palette -- src/cgpack.c's caller
(assets_scene_palette()) already has the identical palette resident from
DCGPAL, since this script quantizes onto that *same* palette (see
export_scene() below), so shipping it a second time in every file would
just be redundant USB traffic for bytes that never differ.

Plus one BUILD.ID marker file (512 bytes: 4-byte magic + 4-byte build
fingerprint) so the runtime can refuse a pack built against a different
--files selection/order (CG scene ids are positional, not stable across a
different build -- see image_resolve.py's scene_id()).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

from PIL import Image as PILImage

from image_resolve import CG_FULL_SIZE

_BLOCK = 512
PIXEL_BYTES = CG_FULL_SIZE[0] * CG_FULL_SIZE[1]              # 57,600
PIXEL_PADDED = -(-PIXEL_BYTES // _BLOCK) * _BLOCK             # 57,856 (113 blocks)
CG_FILE_BYTES = PIXEL_PADDED

MARKER_MAGIC = b"DCGP"
MARKER_BYTES = 512  # 1 block


def build_fingerprint(manifest: dict) -> bytes:
    """4-byte fingerprint over every scene's (id, source file) -- matched
    against import_game.py's DCGVER AppVar at runtime by src/cgpack.c so a
    pack built against a different --files selection/order (a real risk:
    scene ids are positional, see image_resolve.py's scene_id()) is
    rejected instead of silently showing the wrong image for some id.
    """
    h = hashlib.sha256()
    for i, scene in enumerate(manifest["scenes"]):
        h.update(struct.pack("<H", i))
        h.update(scene["file"].encode("utf-8"))
    return h.digest()[:4]


def _decode_1555_palette(raw: bytes) -> list[int]:
    """Reverse gfx_RGBTo1555's packing (graphx.h) back to 8-bit RGB triples,
    flattened for Pillow's Image.putpalette(): bit 15 unused, R/G/B 5 bits
    each at 14-10/9-5/4-0. This is the exact format convimg already wrote
    into pal_cg_NNN.bin, which gets memcpy'd straight into gfx_palette at
    runtime (render_apply_palette()) -- decoding it any other way would
    quantize the full-res pack onto the wrong colors.
    """
    entries = len(raw) // 2
    out: list[int] = []
    for i in range(entries):
        v = raw[2 * i] | (raw[2 * i + 1] << 8)
        r5, g5, b5 = (v >> 10) & 0x1F, (v >> 5) & 0x1F, v & 0x1F
        out += [r5 * 255 // 31, g5 * 255 // 31, b5 * 255 // 31]
    out += [0, 0, 0] * (256 - entries)
    return out


def export_scene(scene: dict, cgpack_src_dir: Path, gfx_dir: Path) -> bytes:
    src_path = cgpack_src_dir / scene["file"]
    img = PILImage.open(src_path).convert("RGB")
    if img.size != CG_FULL_SIZE:
        # Shouldn't happen (image_resolve.py always saves at CG_FULL_SIZE),
        # but a mismatched cache/build shouldn't produce a corrupt pack.
        img = img.resize(CG_FULL_SIZE, PILImage.LANCZOS)

    # Quantize onto the exact same palette convert_images.py already baked
    # for this CG's on-calc version (not a fresh quantization) -- the pack
    # doesn't ship its own palette bytes, relying on that identity (see this
    # module's own docstring and assets.c's assets_scene_palette()).
    pal_path = gfx_dir / f"pal_cg_{scene['cg_palette_index']:03d}.bin"
    ref = PILImage.new("P", (1, 1))
    ref.putpalette(_decode_1555_palette(pal_path.read_bytes()))
    # Default (Floyd-Steinberg) dithering: this pack exists purely for
    # quality, and a full-res image approximates gradients much better
    # dithered against a fixed 256-color palette than flat-mapped.
    indexed = img.quantize(palette=ref)
    pixels = indexed.tobytes()
    if len(pixels) != PIXEL_BYTES:
        raise ValueError(f"{src_path}: expected {PIXEL_BYTES} indexed bytes, got {len(pixels)}")

    out = bytearray(pixels)
    out += b"\x00" * (PIXEL_PADDED - PIXEL_BYTES)
    assert len(out) == CG_FILE_BYTES
    return bytes(out)


def export_cgpack(build_dir: Path) -> tuple[int, int]:
    manifest = json.loads((build_dir / "manifest.json").read_text())
    gfx_dir = build_dir / "gfx"
    cgpack_src_dir = build_dir / "cgpack_src"
    out_dir = build_dir / "cgpack"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Stale files from an earlier build (a different --files selection can
    # both add and remove CG ids) would otherwise linger and get copied
    # onto a drive alongside the new, correctly-fingerprinted pack.
    for stale in out_dir.glob("*"):
        stale.unlink()

    count = 0
    for scene_id, scene in enumerate(manifest["scenes"]):
        if scene["palette"] != "own":
            continue
        data = export_scene(scene, cgpack_src_dir, gfx_dir)
        (out_dir / f"CG{scene_id:03d}.BIN").write_bytes(data)
        count += 1

    fingerprint = build_fingerprint(manifest)
    marker = (MARKER_MAGIC + fingerprint).ljust(MARKER_BYTES, b"\x00")
    (out_dir / "BUILD.ID").write_bytes(marker)

    total = sum(f.stat().st_size for f in out_dir.glob("CG*.BIN"))
    return count, total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-dir", type=Path, default=Path("build"))
    args = ap.parse_args()

    manifest_path = args.build_dir / "manifest.json"
    if not manifest_path.is_file():
        sys.exit(f"{manifest_path} not found -- run image_resolve.py "
                 f"(via import_game.py) first")
    if not (args.build_dir / "gfx").is_dir():
        sys.exit(f"{args.build_dir / 'gfx'} not found -- run convert_images.py first")

    count, total = export_cgpack(args.build_dir)
    out_dir = args.build_dir / "cgpack"
    print(f"wrote {count} full-res CGs to {out_dir} ({total} bytes) -- "
          f"copy this directory's contents onto a FAT32 drive as /DDLC/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
