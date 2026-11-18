#!/usr/bin/env python3
"""Nonvisual original-source timed motion + vignette reference renderer.

Never emits screenshots or displays imagery. USB indices are quantized once
from authored RGB composition, keeping UI-reserved colors byte-identical.
Explicit per-layer ages are required; no guessed cross-scene timing.

Requires numpy in addition to tools/requirements.txt (`pip install numpy`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_cgpack import export_timed_manifest

NAMES = ("s_kill_bg", "s_kill_bg2", "s_kill", "s_kill2")


def _sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _ease(t: float) -> float:
    return (1.0 - math.cos(math.pi * t)) / 2.0


def _dizzy(ms: float, bg: bool) -> tuple[float, float]:
    t = ms / 1000.0
    ends = (0, 10, 5, -5, -3, -10, 0, 5, 0)
    x = t % 6.0
    seg = int(x / 0.75)
    phase = (x - seg * 0.75) / 0.75
    dx = ends[seg] + (ends[seg + 1] - ends[seg]) * _ease(phase)

    y = t % 4.0
    if y < 1.0:
        dy = 5.0 * _ease(y)
    elif y < 3.0:
        dy = 5.0 - 10.0 * _ease((y - 1.0) / 2.0)
    else:
        dy = -5.0 + 5.0 * (1.0 - math.cos((y - 3.0) * math.pi / 2.0))

    factor = 0.25 if bg else 1.0
    return dx * factor, dy * factor


def _pose(item: dict, ms: float, width: int, height: int) -> tuple[float, float, float, float]:
    tag = item["tag"]
    bg = "_bg" in tag
    zoom = 1.0
    xalign = yalign = 0.0
    dy = 0.0
    alpha = float(item.get("alpha", 1.0))

    if item["transform"] == "s_kill_bg_start":
        zoom = 1.1 - 0.1 * min(ms / 4000.0, 1.0)
        xalign = yalign = 0.5
    elif item["transform"] == "s_kill_start":
        q = min(ms / 4000.0, 1.0)
        zoom = 0.8 - 0.05 * q
        xalign = 0.3 + 0.015 * q
        yalign = 0.25
        dy = 10.0 * q
    elif tag.endswith("_zoom"):
        zoom = 2.0
        xalign = 0.2 if bg else 0.5
        yalign = 0.3 if bg else 0.05

    ox = (1280.0 - width * zoom) * xalign
    oy = (720.0 - height * zoom) * yalign + dy

    if tag.endswith("_zoom"):
        ddx, ddy = _dizzy(ms, bg)
        ox += ddx
        oy += ddy

    if tag in ("s_kill_bg2_zoom", "s_kill2_zoom"):
        ends = (
            (0.2, 0.2, 0.25, 0.2, 0.3, 0.25, 0.35, 0.3, 0.35, 0.2)
            if bg
            else (0.3, 0.3, 0.4, 0.3, 0.5, 0.4, 0.6, 0.5, 0.6, 0.4)
        )
        phase = (ms % 2250.0) / 250.0
        seg = int(phase)
        alpha *= ends[seg] + (ends[seg + 1] - ends[seg]) * (phase - seg)

    return ox, oy, zoom, alpha


def compose(
    layers: list[dict],
    ages: list[int],
    originals: dict[str, np.ndarray],
    elapsed: int,
    vignette: np.ndarray,
    vignette_age: int | None,
) -> np.ndarray:
    dst = np.zeros((180, 320, 3), np.float64)
    for i, item in enumerate(layers):
        if item.get("hidden"):
            continue
        raw = originals[item["tag"].replace("_zoom", "")]
        hh, ww = raw.shape[:2]
        ox, oy, zoom, alpha = _pose(item, ages[i] + elapsed, ww, hh)
        xs = np.floor((np.arange(320) * 4.0 - ox) / zoom).astype(np.int32)
        ys = np.floor((np.arange(180) * 4.0 - oy) / zoom).astype(np.int32)
        valid = (ys[:, None] >= 0) & (ys[:, None] < hh) & (xs[None, :] >= 0) & (xs[None, :] < ww)
        sample = raw[np.clip(ys, 0, hh - 1)[:, None], np.clip(xs, 0, ww - 1)[None, :]]
        aa = sample[:, :, 3] / 255.0 * alpha * valid
        dst = sample[:, :, :3] * aa[:, :, None] + dst * (1.0 - aa[:, :, None])

    # Source vignette black-overlay displayable alpha ramp, composited above motion.
    if vignette_age is not None:
        vig_alpha = 0.75 * min(max((vignette_age + elapsed) / 3000.0, 0.0), 1.0)
        va = vignette[:, :, 3] / 255.0 * vig_alpha
        dst = vignette[:, :, :3] * va[:, :, None] + dst * (1.0 - va[:, :, None])

    return np.clip(np.floor(dst + 0.5), 0, 255).astype(np.uint8)


def quantize(rgb: np.ndarray, ui_palette: bytes) -> tuple[bytes, bytes]:
    im = Image.fromarray(rgb).quantize(
        colors=206, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    )
    colors = np.asarray(im.getpalette(), dtype=np.uint8).reshape(-1, 3)[:206] >> 3
    pal = np.frombuffer(ui_palette, dtype="<u2").copy()
    pal[50:] = (
        (colors[:, 0].astype(np.uint16) << 10)
        | (colors[:, 1].astype(np.uint16) << 5)
        | colors[:, 2]
    )

    # Nearest search in exact hardware RGB1555; minimum index wins ties.
    channels = np.column_stack(((pal >> 10) & 31, (pal >> 5) & 31, pal & 31)).astype(np.int16)
    values = (rgb >> 3).reshape(-1, 3).astype(np.int16)
    unique, inverse = np.unique(values, axis=0, return_inverse=True)
    nearest = np.empty(len(unique), np.uint8)
    for off in range(0, len(unique), 1024):
        distances = (
            (unique[off : off + 1024, None, :].astype(np.int32) - channels[None, :, :]) ** 2
        ).sum(axis=2)
        nearest[off : off + 1024] = distances.argmin(axis=1).astype(np.uint8)

    return nearest[inverse].tobytes(), pal.astype("<u2").tobytes()


def generate(root: Path, out: Path, clocks: Path, duration: int, period: int) -> dict:
    if duration % period or not 1 <= duration // period <= 4096:
        raise ValueError("invalid cadence")

    manifest = json.loads((root / "build/manifest.json").read_text())
    specs = json.loads(clocks.read_text())
    if specs.get("version") != 1:
        raise ValueError("unknown clocks")

    originals = {
        name: np.asarray(Image.open(root / "assets/raw/images/cg" / f"{name}.png").convert("RGBA"))
        for name in NAMES
    }
    vig = np.asarray(
        Image.open(root / "assets/raw/images/bg/vignette.png")
        .convert("RGBA")
        .resize((320, 180), Image.Resampling.NEAREST)
    )

    out.mkdir(parents=True, exist_ok=True)
    seqs: list[dict] = []
    reserved_ui = list(range(8)) + list(range(10, 50))

    for spec in specs["sequences"]:
        sid = spec["scene"]
        entry = manifest["scenes"][sid]
        layers = sorted(entry["original_layers"], key=lambda x: x["zorder"])
        ages = spec["layer_ages_ms"]
        if len(ages) != len(layers) or any(type(a) is not int or a < 0 for a in ages):
            raise ValueError("missing layer clocks")

        ui_pal = (root / "build/gfx" / f"pal_cg_{entry['cg_palette_index']:03d}.bin").read_bytes()
        if len(ui_pal) != 512:
            raise ValueError("invalid UI palette")

        seq: dict = {
            "scene": sid,
            "period_ms": period,
            "frames": [],
            "source_layer_ages_ms": ages,
            "vignette_age_ms": spec.get("vignette_age_ms"),
        }
        for index in range(duration // period):
            at = index * period
            rgb = compose(layers, ages, originals, at, vig, spec.get("vignette_age_ms"))
            pixels, palette = quantize(rgb, ui_pal)
            for idx in reserved_ui:
                if palette[2 * idx : 2 * idx + 2] != ui_pal[2 * idx : 2 * idx + 2]:
                    raise AssertionError("UI reserved palette entry drifted")

            prefix = f"s{sid:03d}-f{index:04d}"
            (out / f"{prefix}.pixels").write_bytes(pixels)
            (out / f"{prefix}.palette").write_bytes(palette)
            seq["frames"].append({
                "timestamp_ms": at,
                "pixels": f"{prefix}.pixels",
                "pixels_sha256": _sha256(pixels),
                "palette": f"{prefix}.palette",
                "palette_sha256": _sha256(palette),
                "rgb_reference_sha256": _sha256(rgb.tobytes()),
            })

        seqs.append(seq)
        print(json.dumps({"scene": sid, "frames": len(seq["frames"]), "nonvisual": True}), flush=True)

    data = {
        "version": 1,
        "sequences": seqs,
        "source_hashes": {
            name: _sha256((root / "assets/raw/images/cg" / f"{name}.png").read_bytes())
            for name in NAMES
        },
        "clocks_sha256": _sha256(clocks.read_bytes()),
        "scope": specs["scope"],
        "noise": "excluded; applied by real ASM after frame",
    }
    (out / "manifest.json").write_text(json.dumps(data, indent=2) + "\n")
    export_timed_manifest(out / "manifest.json", out / "DDLC", len(manifest["scenes"]))
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--clocks", type=Path, required=True)
    ap.add_argument("--duration-ms", type=int, default=12000)
    ap.add_argument("--period-ms", type=int, default=100)
    args = ap.parse_args()
    generate(args.root, args.output, args.clocks, args.duration_ms, args.period_ms)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
