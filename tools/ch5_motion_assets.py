"""Generate Chapter 5 motion-layer assets as independently ZX0-compressed 8-row bands.

Only touches the four Ch5 motion originals (s_kill_bg, s_kill_bg2, s_kill, s_kill2);
global scenes, CG slots, and the external CG pack are unaffected.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _cedev import find_cedev_tool
from ch5_blend_luts import generate_blend_luts
from ch5_fg16_lut import generate_fg16_lut

NAMES = ("s_kill_bg", "s_kill_bg2", "s_kill", "s_kill2")
UI_INDICES = tuple(range(8)) + tuple(range(10, 50))
MOTION_INDICES = tuple(i for i in range(255) if i not in UI_INDICES)


def motion_palette(images: list[Image.Image], game_palette: bytes) -> tuple[Image.Image, list[int]]:
    if len(game_palette) != 512:
        raise ValueError("Expected full 256-entry game palette")
    fixed = struct.unpack("<256H", game_palette)

    atlas = Image.new("RGB", (max(im.width for im in images), sum(im.height for im in images)))
    y_off = 0
    for im in images:
        atlas.paste(im.convert("RGB"), (0, y_off))
        y_off += im.height

    colors = atlas.quantize(colors=len(MOTION_INDICES), method=Image.Quantize.MEDIANCUT).getpalette()
    rgb = [0] * 768
    words = [0] * 256

    for i in UI_INDICES:
        p = fixed[i]
        # LCD palette bit 15 is the low green bit, matching convimg's output.
        rgb[i * 3 : i * 3 + 3] = [
            ((p >> 10) & 31) * 8,
            ((p >> 5) & 31) * 8 + ((p >> 15) & 1) * 4,
            (p & 31) * 8,
        ]
        words[i] = p

    for src, dst in enumerate(MOTION_INDICES):
        r, g, b = colors[src * 3 : src * 3 + 3]
        rgb[dst * 3 : dst * 3 + 3] = [r, g, b]
        words[dst] = ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3)

    # Index 255 is reserved for transparency; duplicate black prevents opaque
    # quantization from picking an unrelated color that would turn transparent.
    rgb[765:768] = rgb[3:6]
    words[255] = words[1]

    common = Image.new("P", (1, 1))
    common.putpalette(rgb)
    return common, words


def generate(
    raw_dir: Path,
    build_dir: Path,
    appvar_dir: Path,
    write_appvar: Callable[[bytes, str, Path], Path],
) -> list[Path]:
    convbin = find_cedev_tool("convbin", env_var="CONVBIN_REAL")

    images: list[Image.Image] = []
    for name in NAMES:
        im = Image.open(raw_dir / "images/cg" / f"{name}.png").convert("RGBA")
        target_size = (320, 180) if "_bg" in name else (72, 263)
        images.append(im.resize(target_size, Image.Resampling.NEAREST))

    # Preserve the immutable textbox/namebox indices used by every game scene.
    game_palette = (build_dir / "gfx/pal_game.bin").read_bytes()
    common, rgb1555 = motion_palette(images, game_palette)

    output: list[Path] = []
    reports: list[dict] = []
    textures: list[bytes] = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_in = Path(tmp_dir) / "motion_in.bin"
        tmp_out = Path(tmp_dir) / "motion_out.bin"

        for idx, (name, im) in enumerate(zip(NAMES, images)):
            raw_idx = im.convert("RGB").quantize(palette=common, dither=Image.Dither.NONE).tobytes()
            # PIL may pick the duplicate entry at 255; remap opaque black to index 1.
            indexed = bytearray(1 if c == 255 else c for c in raw_idx)
            alpha = im.getchannel("A").tobytes()
            for px_idx, a in enumerate(alpha):
                if a < 128:
                    indexed[px_idx] = 255
            textures.append(bytes(indexed))

            bands: list[bytes] = []
            for y in range(0, im.height, 8):
                band_bytes = bytes(indexed[y * im.width : min(y + 8, im.height) * im.width])
                tmp_in.write_bytes(band_bytes)
                subprocess.run(
                    [
                        str(convbin),
                        "-i", str(tmp_in),
                        "-j", "bin",
                        "-c", "zx0",
                        "-k", "bin",
                        "-o", str(tmp_out),
                    ],
                    check=True,
                    capture_output=True,
                )
                bands.append(tmp_out.read_bytes())

            # u16 offsets relative to payload start; fixed max decoded band = 2560B.
            header = struct.pack("<4sHHBB", b"DMT1", im.width, im.height, 8, len(bands))
            start = len(header) + 2 * len(bands)
            offsets: list[int] = []
            payload = bytearray()
            for band in bands:
                offsets.append(start + len(payload))
                payload.extend(band)

            data = header + struct.pack(f"<{len(offsets)}H", *offsets) + bytes(payload)
            if len(data) > 65503:
                raise ValueError(f"DMOT{idx} exceeds TI AppVar payload cap ({len(data)} bytes)")

            output.append(write_appvar(data, f"DMOT{idx}", appvar_dir))
            reports.append({
                "name": name,
                "payload_bytes": len(data),
                "width": im.width,
                "height": im.height,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bands": len(bands),
                "decoded_band_max": im.width * 8,
            })

    output.append(write_appvar(struct.pack("<256H", *rgb1555), "DMOTPAL", appvar_dir))

    # Exhaustive offline RGB1555 -> fixed palette map (transparent 255 excluded;
    # lowest index wins ties). 32768B flash-resident table avoids runtime searches.
    channels = [((v >> 10) & 31, (v >> 5) & 31, v & 31) for v in rgb1555[:255]]
    nearest = bytearray(32768)
    for value in range(32768):
        rr, gg, bb = (value >> 10) & 31, (value >> 5) & 31, value & 31
        best = 100000
        chosen = 0
        for index, (pr, pg, pb) in enumerate(channels):
            distance = (rr - pr) ** 2 + (gg - pg) ** 2 + (bb - pb) ** 2
            if distance < best:
                best = distance
                chosen = index
        nearest[value] = chosen

    output.append(write_appvar(bytes(nearest), "DMOTRGB", appvar_dir))
    rgb15_masked = [p & 32767 for p in rgb1555]
    output.append(
        write_appvar(bytes(generate_fg16_lut(rgb15_masked, nearest, textures)), "DLUTQ", appvar_dir)
    )

    # Experimental full blend LUTs exceed full-bundle flash headroom; opt-in only.
    if os.environ.get("DDLC_EXPERIMENTAL_MOTION_LUT") == "1":
        groups, tables = generate_blend_luts(rgb15_masked, nearest)
        output.append(write_appvar(groups, "DMOTBGM", appvar_dir))
        for level, table in enumerate(tables):
            output.append(write_appvar(table, f"DMLUT{level:02d}", appvar_dir))

    manifest = json.loads((build_dir / "manifest.json").read_text())
    records = bytearray()
    for sid, entry in enumerate(manifest["scenes"]):
        layers = entry.get("original_layers", [])
        if not layers:
            continue
        layer_modes: list[tuple[int, int]] = []
        for item in sorted(layers, key=lambda a: a["zorder"]):
            if item.get("hidden"):
                continue
            tag = item["tag"]
            base = tag.replace("_zoom", "")
            if base not in NAMES:
                raise ValueError(f"unsupported dedicated motion tag {tag}")
            if item.get("alias") == "s_kill_zoom_trans":
                mode = 7
            elif item["transform"] == "s_kill_bg_start":
                mode = 1
            elif item["transform"] == "s_kill_start":
                mode = 2
            else:
                mode = 0
            if tag.endswith("_zoom"):
                mode = (3, 5, 4, 6)[NAMES.index(base)]
            # An opaque full-screen background (mode 0, 1, or 3) completely covers
            # every earlier lower/equal-z layer (e.g. preloaded s_kill_bg2/s_kill2).
            if base in ("s_kill_bg", "s_kill_bg2") and mode in (0, 1, 3):
                layer_modes.clear()
            layer_modes.append((NAMES.index(base), mode))
        pairs = [v for pair in layer_modes for v in pair]
        if len(pairs) > 16:
            raise ValueError("more than eight authored display instances")
        records.extend(bytes([sid, len(pairs) // 2] + pairs + [0] * (16 - len(pairs))))

    output.append(write_appvar(bytes(records), "DMOTMAP", appvar_dir))
    (build_dir / "motion-band-audit.json").write_text(json.dumps(reports, indent=2) + "\n")
    return output
