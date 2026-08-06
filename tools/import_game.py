#!/usr/bin/env python3
"""Single entry point for the DDLC -> TI-84 Plus CE asset import pipeline.

    python3 tools/import_game.py /path/to/DDLC-1.1.1-pc/game

Orchestrates, against the user's own legally obtained copy of the game:
  extract.py       (unrpa)              .rpa archives -> assets/raw/
  image_resolve.py (Pillow)             Show/Scene names -> baked PNGs + manifest
  compile_script.py                     AST -> bytecode, via vnasm.py
  convert_images.py (convimg)           PNGs -> quantized/compressed art
  convbin                               raw bytes -> split TI AppVars
  convbin (b84)                         program + AppVars -> one bundle

Nothing this script reads or produces is ever committed -- everything lives
under assets/ and build/, both gitignored (see LICENSE, README.md).

SCOPE NOTE: this compiles every imported chapter into ONE combined bytecode
chunk (so cross-file Jump/Call resolve correctly -- e.g. script.rpyc calling
into script-ch0.rpyc), not yet split into the <=16KB RAM-resident chunks
docs/FORMAT.md specifies. Real splitting needs a stitching loader in
src/assets.c, which doesn't exist yet (Milestone 3) -- neither does the code
to make main.c load these AppVars instead of the Milestone-1 demo script.
This script's job is the pipeline and its verification, not engine wiring.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import convert_images
import extract
from compile_script import CompileError, Compiler
from image_resolve import ImageResolver

# Act 1 (ch0-ch4) plus the labels it calls into that live in other files
# (side-story chapters, the poem minigame, and the top-level dispatcher).
# Anything these files Jump/Call into that ISN'T in this list -- Act 2/3
# content, credits, exclusives -- lands on compile_script's OP_END stub
# rather than failing the build (see vnasm.Assembler.patch_missing_labels).
ACT1_FILES = [
    "script-ch0", "script-ch1", "script-ch2", "script-ch3", "script-ch4",
    "script-ch10", "script-ch20", "script-ch21", "script-ch22", "script-ch23",
    "script-poemgame", "script-poemresponses", "script",
]

ARCHIVE_BUDGET = 2_500_000  # ~2.5MB target, from the plan
CHUNK_BUDGET = 16 * 1024    # docs/FORMAT.md per-chunk RAM budget (not yet enforced on-calc)

MAXVARSIZE = 65000  # convbin --maxvarsize; safely under the 65535-byte TI cap


def step(msg: str) -> None:
    print(f"\n=== {msg} ===")


def do_extract(game_dir: Path, raw_dir: Path, skip: bool) -> None:
    step("extract")
    if skip and raw_dir.exists():
        print(f"skipping (--skip-extract, {raw_dir} already exists)")
        return
    extract.extract(game_dir, raw_dir)


def do_compile(raw_dir: Path, build_dir: Path) -> tuple[Compiler, ImageResolver, list[str]]:
    step("compile script + resolve images")
    resolver = ImageResolver(raw_dir, build_dir)
    compiler = Compiler(resolver=resolver)

    for name in ACT1_FILES:
        path = raw_dir / f"{name}.rpyc"
        if not path.is_file():
            print(f"  ! {name}.rpyc not found in {raw_dir}, skipping")
            continue
        compiler.compile_file(path)

    missing = compiler.finish()
    resolver.write_manifest()

    print(f"code: {len(compiler.asm.code)} bytes, "
          f"{len(compiler.asm.strings)} strings, "
          f"{len(compiler.variables)} variables")
    print(f"sprites baked: {len(resolver.sprites)}, scenes baked: {len(resolver.scenes)}")
    if resolver.unsupported:
        print(f"unsupported images: {len(resolver.unsupported)}")
        for name, reason in resolver.unsupported:
            print(f"   {name} -> {reason}")
    if missing:
        print(f"labels outside the compiled set (stubbed to OP_END): {missing}")
    print(f"skipped statements: {len(compiler.skipped)} "
          f"(see build/skipped.json for the full list)")
    (build_dir / "skipped.json").write_text(json.dumps(
        [{"file": s.file, "line": s.line, "kind": s.kind, "reason": s.reason}
         for s in compiler.skipped], indent=2))

    return compiler, resolver, missing


def write_chunk(compiler: Compiler, build_dir: Path) -> Path:
    chunk = compiler.asm.to_chunk_bytes()
    path = build_dir / "script.vnb"
    path.write_bytes(chunk)

    if len(chunk) > CHUNK_BUDGET:
        print(f"  ! chunk is {len(chunk)} bytes, over the {CHUNK_BUDGET}-byte "
              f"per-chunk RAM budget from docs/FORMAT.md -- expected until "
              f"Milestone 3 splits it (see this file's module docstring)")
    return path


def do_convert_images(build_dir: Path, quality: int, skip: bool) -> None:
    step("convert images (convimg)")
    gfx_dir = build_dir / "gfx"
    if skip and gfx_dir.exists() and any(gfx_dir.iterdir()):
        print(f"skipping (--skip-convimg, {gfx_dir} already has output)")
        return

    manifest = json.loads((build_dir / "manifest.json").read_text())
    gfx_dir.mkdir(parents=True, exist_ok=True)
    doc = convert_images.build_yaml(manifest, build_dir / "img", gfx_dir, quality=quality)
    yaml_path = build_dir / "convimg.yaml"
    yaml_path.write_text(__import__("yaml").safe_dump(doc, sort_keys=False))
    convert_images.run_convimg(yaml_path)


def require(tool: str, hint: str) -> None:
    if shutil.which(tool) is None:
        sys.exit(f"{tool} not found on PATH. {hint}")


def package_appvar(bin_path: Path, name: str, out_dir: Path) -> list[Path]:
    """Split-pack a raw binary into TI AppVars via convbin."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_base = out_dir / f"{name.lower()}.8xv"
    subprocess.run([
        "convbin", "-i", str(bin_path), "-j", "bin", "-k", "8xv-split",
        "-n", name, "-o", str(out_base), "-m", str(MAXVARSIZE), "-r",
    ], check=True)
    return sorted(out_dir.glob(f"{name.lower()}.*.8xv"))


def build_lut(entries: list[tuple[int, int]]) -> bytes:
    """u16 count, then per entry a u24 offset + u16 length (little-endian).

    Matches vnasm's u24 convention. Lets the (future) loader seek directly
    to sprite/scene N's independently-zx0-compressed bytes inside the
    concatenated group blob, without decompressing every entry ahead of it.
    """
    import struct
    out = bytearray(struct.pack("<H", len(entries)))
    for offset, length in entries:
        out += struct.pack("<I", offset)[:3]
        out += struct.pack("<H", length)
    return bytes(out)


def concat_group(files: list[Path]) -> tuple[bytes, bytes]:
    """Concatenate files back-to-back. Returns (data, lut_bytes)."""
    data = bytearray()
    entries = []
    for f in files:
        chunk = f.read_bytes()
        entries.append((len(data), len(chunk)))
        data += chunk
    return bytes(data), build_lut(entries)


def do_package(build_dir: Path, appvar_dir: Path, manifest: dict) -> list[Path]:
    step("package AppVars (convbin)")
    require("convbin", 'export PATH="$HOME/CEdev/bin:$PATH"')
    gfx_dir = build_dir / "gfx"

    def bin_for(png_name: str) -> Path:
        return gfx_dir / (Path(png_name).stem + ".bin")

    appvars: list[Path] = []
    appvars += package_appvar(build_dir / "script.vnb", "DDLCSCR", appvar_dir)

    # Sprites and scenes each get ONE concatenated blob + one LUT AppVar,
    # rather than one AppVar per image (199 sprites alone would mean 199
    # separate TI variables -- impractical to ship/manage). Each image
    # stays independently zx0-compressed inside the blob; the LUT is how a
    # loader finds where image N starts without decompressing images 0..N-1.
    sprite_files = [bin_for(s["file"]) for s in manifest["sprites"]]
    sprite_data, sprite_lut = concat_group(sprite_files)
    (build_dir / "sprites.bin").write_bytes(sprite_data)
    (build_dir / "sprites.lut").write_bytes(sprite_lut)
    appvars += package_appvar(build_dir / "sprites.bin", "DDLCSPR", appvar_dir)
    appvars += package_appvar(build_dir / "sprites.lut", "DDLCSPL", appvar_dir)

    scene_files = [bin_for(s["file"]) for s in manifest["scenes"]]
    scene_data, scene_lut = concat_group(scene_files)
    (build_dir / "scenes.bin").write_bytes(scene_data)
    (build_dir / "scenes.lut").write_bytes(scene_lut)
    appvars += package_appvar(build_dir / "scenes.bin", "DDLCSCN", appvar_dir)
    appvars += package_appvar(build_dir / "scenes.lut", "DDLCSCL", appvar_dir)

    # Palettes: pal_game ships whole (tiny, fixed), one per-CG palette
    # concatenated with a LUT indexed by each scene's cg_palette_index.
    appvars += package_appvar(gfx_dir / "pal_game.bin", "DDLCPAL", appvar_dir)
    cg_count = sum(1 for s in manifest["scenes"] if s["palette"] == "own")
    if cg_count:
        cg_pal_files = [gfx_dir / f"pal_cg_{i:03d}.bin" for i in range(cg_count)]
        cg_pal_data, cg_pal_lut = concat_group(cg_pal_files)
        (build_dir / "cg_palettes.bin").write_bytes(cg_pal_data)
        (build_dir / "cg_palettes.lut").write_bytes(cg_pal_lut)
        appvars += package_appvar(build_dir / "cg_palettes.bin", "DDLCCPL", appvar_dir)
        appvars += package_appvar(build_dir / "cg_palettes.lut", "DDLCCPX", appvar_dir)

    total = sum(p.stat().st_size for p in appvars)
    print(f"{len(appvars)} AppVars, {total} bytes total "
          f"({len(sprite_files)} sprites + {len(scene_files)} scenes concatenated)")
    return appvars


def do_bundle(prog_8xp: Path, appvars: list[Path], out_path: Path) -> None:
    step("bundle .b84")
    require("convbin", 'export PATH="$HOME/CEdev/bin:$PATH"')
    if not prog_8xp.is_file():
        print(f"  ! {prog_8xp} not found -- run `make` first; bundling AppVars alone")
        inputs = appvars
    else:
        inputs = [prog_8xp] + appvars

    args = ["convbin"]
    for f in inputs:
        args += ["-i", str(f)]
    args += ["-k", "b84", "-o", str(out_path)]
    subprocess.run(args, check=True)

    size = out_path.stat().st_size
    print(f"{out_path}: {size} bytes")
    if size > ARCHIVE_BUDGET:
        print(f"  ! over the ~{ARCHIVE_BUDGET}-byte archive budget from the plan")
    else:
        print(f"  within the ~{ARCHIVE_BUDGET}-byte archive budget "
              f"({size / ARCHIVE_BUDGET:.0%} used)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game_dir", type=Path, help="path to DDLC's 'game' directory")
    ap.add_argument("--build-dir", type=Path, default=Path("build"))
    ap.add_argument("--raw-dir", type=Path, default=Path("assets/raw"))
    ap.add_argument("--appvar-dir", type=Path, default=Path("build/appvars"))
    ap.add_argument("--prog", type=Path, default=Path("bin/DDLC.8xp"))
    ap.add_argument("--out", type=Path, default=Path("build/DDLC.b84"))
    ap.add_argument("--quality", type=int, default=8)
    ap.add_argument("--skip-extract", action="store_true")
    ap.add_argument("--skip-convimg", action="store_true")
    args = ap.parse_args()

    args.build_dir.mkdir(parents=True, exist_ok=True)

    try:
        do_extract(args.game_dir.expanduser().resolve(), args.raw_dir, args.skip_extract)
        compiler, resolver, _missing = do_compile(args.raw_dir, args.build_dir)
        chunk_path = write_chunk(compiler, args.build_dir)
        do_convert_images(args.build_dir, args.quality, args.skip_convimg)
        manifest = json.loads((args.build_dir / "manifest.json").read_text())
        appvars = do_package(args.build_dir, args.appvar_dir, manifest)
        do_bundle(args.prog, appvars, args.out)
    except CompileError as e:
        sys.exit(f"compile error: {e}")
    except subprocess.CalledProcessError as e:
        sys.exit(f"{e.cmd[0]} failed with exit code {e.returncode}")

    print(f"\ndone. {chunk_path} + {args.build_dir / 'gfx'} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
