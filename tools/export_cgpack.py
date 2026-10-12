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
import zlib
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


def export_timed_frame(output: Path, scene: int, index: int, timestamp_ms: int,
                       pixels: bytes, palette: bytes) -> Path:
    """Serialize already reference-quantized bytes, never re-quantize here.

    This is the reader format only; reference rendering/scheduling is separate.
    """
    if not (0 <= scene <= 255 and 0 <= index < 4096 and
            0 <= timestamp_ms <= 0xffffffff):
        raise ValueError("timed frame identity/timestamp outside format bounds")
    if len(pixels) != 57600 or len(palette) != 512:
        raise ValueError("timed frame requires 320x180 indices and 256 RGB1555 colors")
    header = bytearray(512)
    struct.pack_into("<4sBBHIIHHII", header, 0, b"DTV1", scene, 0, index,
                     timestamp_ms, 57600, 320, 180, 512, 1024)
    struct.pack_into("<II", header, 28, zlib.crc32(palette), zlib.crc32(pixels))
    dest = output / f"T{scene:03d}" / f"F{index:04d}.BIN"
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = bytes(header) + palette + pixels + bytes(256)
    assert len(payload) == 58880
    dest.write_bytes(payload)
    return dest


def export_timed_schedule(output: Path, scene: int, period_ms: int,
                          frames: list[tuple[bytes, bytes]]) -> Path:
    """Fixed cadence, exact supplied reference bytes, no implicit conversion."""
    if not 0 <= scene <= 255 or not 1 <= period_ms <= 60000:
        raise ValueError("invalid scene or frame period")
    if not 1 <= len(frames) <= 4096:
        raise ValueError("invalid frame count")
    # Validate every frame BEFORE writing schedule or frame files.
    if any(len(pixels) != 57600 or len(palette) != 512
           for pixels, palette in frames):
        raise ValueError("invalid frame lengths")
    directory = output / f"T{scene:03d}"
    directory.mkdir(parents=True, exist_ok=True)
    header = bytearray(512)
    struct.pack_into("<4sBBHII", header, 0, b"DTM1", scene, 0,
                     len(frames), period_ms, len(frames) * period_ms)
    struct.pack_into("<I", header, 16, zlib.crc32(header[:16]))
    hashes = []
    for index, (pixels, palette) in enumerate(frames):
        dest = export_timed_frame(output, scene, index, index * period_ms,
                                  pixels, palette)
        hashes.append(dict(index=index, timestamp_ms=index * period_ms,
                           pixels_sha256=hashlib.sha256(pixels).hexdigest(),
                           palette_sha256=hashlib.sha256(palette).hexdigest(),
                           file_sha256=hashlib.sha256(dest.read_bytes()).hexdigest()))
    # Write schedule LAST so interrupted export cannot publish partial sequence.
    (directory / "REFERENCE.json").write_text(json.dumps(
        dict(scene=scene, period_ms=period_ms, frames=hashes), indent=2))
    path = directory / "SCHED.BIN"
    path.write_bytes(header)
    return path


def export_timed_manifest(source: Path, output: Path, scene_count: int) -> int:
    """Consume explicitly hashed, prequantized frames from reference pipeline.

    Manifest is not inferred from scene artwork. Paths are relative to the
    manifest and cannot escape its directory. Missing/mismatched reference
    bytes fail closed before any schedule is published.
    """
    root = source.resolve().parent
    manifest = json.loads(source.read_text())
    if manifest.get("version") != 1:
        raise ValueError("unsupported timed manifest version")
    sequences = []
    used = set()
    def load(item, key, expected_length):
        path = (root / item[key]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("reference path escapes manifest directory")
        data = path.read_bytes()
        if len(data) != expected_length or hashlib.sha256(data).hexdigest() != item[key + "_sha256"]:
            raise ValueError("reference bytes do not match declared length/hash")
        return data
    for seq in manifest["sequences"]:
        scene, period = seq["scene"], seq["period_ms"]
        if type(scene) is not int or not 0 <= scene < min(scene_count, 256) or scene in used:
            raise ValueError("invalid or duplicate timed scene")
        used.add(scene)
        if type(period) is not int or not 1 <= period <= 60000 or not 1 <= len(seq["frames"]) <= 4096:
            raise ValueError("invalid timed schedule")
        frames = []
        for index, item in enumerate(seq["frames"]):
            if item["timestamp_ms"] != index * period:
                raise ValueError("reference timestamps do not match uniform schedule")
            frames.append((load(item, "pixels", 57600), load(item, "palette", 512)))
        sequences.append((scene, period, frames))
    for scene, period, frames in sequences:
        export_timed_schedule(output, scene, period, frames)
    return len(sequences)


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


def export_cgpack(build_dir: Path, timed_manifest: Path | None = None) -> tuple[int, int]:
    manifest = json.loads((build_dir / "manifest.json").read_text())
    gfx_dir = build_dir / "gfx"
    cgpack_src_dir = build_dir / "cgpack_src"
    out_dir = build_dir / "cgpack"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Stale files from an earlier build (a different --files selection can
    # both add and remove CG ids) would otherwise linger and get copied
    # onto a drive alongside the new, correctly-fingerprinted pack.
    for stale in out_dir.glob("*"):
        if stale.is_file() and (stale.name == "BUILD.ID" or
                                (stale.name.startswith("CG") and stale.suffix == ".BIN")):
            stale.unlink()
    # Own generated timed files only; never remove arbitrary pack directories.
    for directory in out_dir.glob("T[0-9][0-9][0-9]"):
        if directory.is_dir():
            for stale in directory.iterdir():
                if stale.is_file() and (stale.name in ("SCHED.BIN", "REFERENCE.json") or
                                        (stale.name.startswith("F") and stale.suffix == ".BIN")):
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

    if timed_manifest is not None:
        export_timed_manifest(timed_manifest, out_dir, len(manifest["scenes"]))
    total = sum(f.stat().st_size for f in out_dir.rglob("*.BIN"))
    return count, total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-dir", type=Path, default=Path("build"))
    ap.add_argument("--timed-manifest", type=Path, help="hashed reference-indexed timed sequences")
    args = ap.parse_args()

    manifest_path = args.build_dir / "manifest.json"
    if not manifest_path.is_file():
        sys.exit(f"{manifest_path} not found -- run image_resolve.py "
                 f"(via import_game.py) first")
    if not (args.build_dir / "gfx").is_dir():
        sys.exit(f"{args.build_dir / 'gfx'} not found -- run convert_images.py first")

    count, total = export_cgpack(args.build_dir, args.timed_manifest)
    out_dir = args.build_dir / "cgpack"
    print(f"wrote {count} full-res CGs to {out_dir} ({total} bytes) -- "
          f"copy this directory's contents onto a FAT32 drive as /DDLC/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
