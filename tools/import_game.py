#!/usr/bin/env python3
"""Single entry point for the DDLC -> TI-84 Plus CE asset import pipeline.

    python3 tools/import_game.py /path/to/DDLC-1.1.1-pc/game

Or, to also build the engine first and guarantee it's the one that ends up
in the bundle: `make bundle GAME_DIR=/path/to/DDLC-1.1.1-pc/game`.

Orchestrates, against the user's own legally obtained copy of the game:
  extract.py       (unrpa)              .rpa archives -> assets/raw/
  image_resolve.py (Pillow)             Show/Scene names -> baked PNGs + manifest
  compile_script.py                     AST -> bytecode, via vnasm.py
  convert_images.py (convimg)           PNGs -> quantized/compressed art
  convbin                               raw bytes -> split TI AppVars
  convbin (b84)                         program + AppVars -> one bundle

Nothing this script reads or produces is ever committed -- everything lives
under assets/ and build/, both gitignored (see LICENSE, README.md).

SCOPE NOTE: --files selects which imported chapters actually get compiled
and bundled (default: just `script` + `script-ch0`, a complete, working
Chapter 0 that fits in RAM as a single resident chunk and loads through
src/assets.c). The pipeline *can* compile the whole of Act 1 (ch0-ch4, pass
--files with the full list) into one combined chunk -- cross-file Jump/Call
resolve correctly across it -- but that combined chunk runs ~240KB, well
over the ~154KB of usable RAM, and there's no loader yet that can keep only
part of a multi-chapter script resident and swap chunks on a label jump.
Picking a subset that already fits is how "real assets, actually running"
ships today without waiting on that loader.
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import convert_images
import extract
from compile_script import CompileError, Compiler
from image_resolve import ImageResolver

# Full Act 1 (ch0-ch4) plus the labels it calls into that live in other
# files (side-story chapters, the poem minigame, and the top-level
# dispatcher). Anything these files Jump/Call into that ISN'T in this list
# -- Act 2/3 content, credits, exclusives -- lands on compile_script's
# OP_END stub rather than failing the build
# (see vnasm.Assembler.patch_missing_labels). Available via --files=act1;
# the default is just `script,script-ch0` -- see the module docstring.
ACT1_FILES = [
    "script-ch0", "script-ch1", "script-ch2", "script-ch3", "script-ch4",
    "script-ch10", "script-ch20", "script-ch21", "script-ch22", "script-ch23",
    "script-poemgame", "script-poemresponses", "script",
]
DEFAULT_FILES = ["script", "script-ch0"]

ARCHIVE_BUDGET = 2_500_000  # ~2.5MB target, from the plan
CHUNK_BUDGET = 16 * 1024    # docs/FORMAT.md per-chunk RAM budget
RAM_BUDGET = 150 * 1024     # conservative usable-RAM ceiling for the resident chunk

MAXVARSIZE = 65000  # safely under the 65535-byte TI variable cap


def step(msg: str) -> None:
    print(f"\n=== {msg} ===")


def do_extract(game_dir: Path, raw_dir: Path, skip: bool) -> None:
    step("extract")
    if skip and raw_dir.exists():
        print(f"skipping (--skip-extract, {raw_dir} already exists)")
        return
    extract.extract(game_dir, raw_dir)


def do_compile(raw_dir: Path, build_dir: Path,
               files: list[str]) -> tuple[Compiler, ImageResolver, list[str]]:
    step("compile script + resolve images")
    resolver = ImageResolver(raw_dir, build_dir)
    compiler = Compiler(resolver=resolver)

    for name in files:
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

    if len(chunk) > RAM_BUDGET:
        sys.exit(f"chunk is {len(chunk)} bytes, over the ~{RAM_BUDGET}-byte usable-RAM "
                 f"ceiling -- src/assets.c loads the whole chunk resident, so this "
                 f"file selection (--files) won't run on real hardware. Pick fewer "
                 f"files, e.g. the default `script,script-ch0`.")
    if len(chunk) > CHUNK_BUDGET:
        print(f"  ! chunk is {len(chunk)} bytes, over the conservative {CHUNK_BUDGET}-byte "
              f"per-chunk budget from docs/FORMAT.md (fine under the real "
              f"~{RAM_BUDGET}-byte ceiling; that budget targets a future multi-chunk "
              f"loader that can keep several chunks resident at once)")
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


def ti_name(prefix: str, index: int | None = None) -> str:
    """A valid TI AppVar name (<=8 chars) for @prefix, optionally suffixed
    with @index. Raises rather than silently truncating/colliding."""
    name = prefix if index is None else f"{prefix}{index}"
    if len(name) > 8:
        raise CompileError(f"AppVar name exceeds TI's 8-char limit: {name!r} "
                            f"-- shorten the prefix in import_game.py")
    return name


def write_appvar(data: bytes, name: str, appvar_dir: Path) -> Path:
    """Package one blob as one TI AppVar. Data must already fit in one
    AppVar (<=MAXVARSIZE) -- see pack_group, which guarantees that."""
    appvar_dir.mkdir(parents=True, exist_ok=True)
    src = appvar_dir / f"{name.lower()}.bin"
    src.write_bytes(data)
    out = appvar_dir / f"{name.lower()}.8xv"
    subprocess.run([
        "convbin", "-i", str(src), "-j", "bin", "-k", "8xv",
        "-n", name, "-o", str(out), "-r",
    ], check=True)
    return out


def pack_group(files: list[Path],
               max_size: int = MAXVARSIZE) -> tuple[list[bytes], list[tuple[int, int, int]]]:
    """Greedily pack files into <=max_size pieces, one AppVar's worth each,
    never splitting one file's bytes across two pieces -- so the reader
    only ever needs one ti_Open to get an image's complete compressed data,
    no cross-AppVar-boundary stitching.

    Returns (pieces, lut) where lut[i] = (piece_index, offset_in_piece,
    length) for input file i, in the same order as `files`.
    """
    pieces: list[bytearray] = [bytearray()]
    lut: list[tuple[int, int, int]] = []

    for f in files:
        data = f.read_bytes()
        if len(data) > max_size:
            raise CompileError(f"{f} is {len(data)} bytes, larger than a single "
                                f"AppVar can hold ({max_size})")
        if len(pieces[-1]) + len(data) > max_size:
            pieces.append(bytearray())
        piece_index = len(pieces) - 1
        offset = len(pieces[-1])
        pieces[-1] += data
        lut.append((piece_index, offset, len(data)))

    return [bytes(p) for p in pieces], lut


def build_lut(entries: list[tuple[int, int, int]]) -> bytes:
    """u16 count, then per entry: u8 appvar_index, u16 offset, u16 length."""
    out = bytearray(struct.pack("<H", len(entries)))
    for piece_index, offset, length in entries:
        out += struct.pack("<BHH", piece_index, offset, length)
    return bytes(out)


def package_group(files: list[Path], prefix: str, lut_name: str,
                  build_dir: Path, appvar_dir: Path) -> list[Path]:
    """Pack+ship a group of independently zx0-compressed images (sprites or
    scenes) as N data AppVars (@prefix + index, e.g. DSPR0, DSPR1, ...) plus
    one small LUT AppVar (@lut_name) recording which piece/offset/length
    each holds. One AppVar per image would mean hundreds of separate TI
    variables to manage; concatenating everything into one blob and
    splitting blindly risks straddling an image across an AppVar boundary.
    This is the middle ground: few AppVars, and every image whole in one.
    """
    pieces, lut_entries = pack_group(files)
    appvars = [write_appvar(piece, ti_name(prefix, i), appvar_dir)
               for i, piece in enumerate(pieces)]
    lut_bytes = build_lut(lut_entries)
    (build_dir / f"{prefix.lower()}.lut").write_bytes(lut_bytes)
    appvars.append(write_appvar(lut_bytes, lut_name, appvar_dir))
    return appvars


def do_package(build_dir: Path, appvar_dir: Path, manifest: dict) -> list[Path]:
    step("package AppVars (convbin)")
    require("convbin", 'export PATH="$HOME/CEdev/bin:$PATH"')
    gfx_dir = build_dir / "gfx"

    def bin_for(png_name: str) -> Path:
        return gfx_dir / (Path(png_name).stem + ".bin")

    appvars: list[Path] = [
        write_appvar((build_dir / "script.vnb").read_bytes(), "DSCRIPT", appvar_dir),
    ]

    sprite_files = [bin_for(s["file"]) for s in manifest["sprites"]]
    appvars += package_group(sprite_files, "DSPR", "DSPRLUT", build_dir, appvar_dir)

    scene_files = [bin_for(s["file"]) for s in manifest["scenes"]]
    appvars += package_group(scene_files, "DSCN", "DSCNLUT", build_dir, appvar_dir)

    appvars.append(write_appvar((gfx_dir / "pal_game.bin").read_bytes(), "DPALGAME", appvar_dir))

    cg_count = sum(1 for s in manifest["scenes"] if s["palette"] == "own")
    if cg_count:
        cg_pal_files = [gfx_dir / f"pal_cg_{i:03d}.bin" for i in range(cg_count)]
        appvars += package_group(cg_pal_files, "DCGPAL", "DCGPLUT", build_dir, appvar_dir)

    total = sum(p.stat().st_size for p in appvars)
    print(f"{len(appvars)} AppVars, {total} bytes total "
          f"({len(sprite_files)} sprites + {len(scene_files)} scenes)")
    return appvars


def do_bundle(prog_8xp: Path, appvars: list[Path], out_path: Path) -> None:
    step("bundle .b84")
    require("convbin", 'export PATH="$HOME/CEdev/bin:$PATH"')
    if not prog_8xp.is_file():
        sys.exit(f"{prog_8xp} not found -- build the engine first (`make`, or "
                 f"`make bundle GAME_DIR=...` to do both in one step)")
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
    ap.add_argument("--files", default="script,script-ch0",
                    help="comma-separated .rpyc stems to compile/bundle, or 'act1' "
                         "for the full ch0-ch4 set (won't fit resident RAM as one "
                         "chunk yet -- see the module docstring). "
                         "Default: script,script-ch0")
    ap.add_argument("--skip-extract", action="store_true")
    ap.add_argument("--skip-convimg", action="store_true")
    args = ap.parse_args()

    args.build_dir.mkdir(parents=True, exist_ok=True)
    files = ACT1_FILES if args.files == "act1" else args.files.split(",")

    try:
        do_extract(args.game_dir.expanduser().resolve(), args.raw_dir, args.skip_extract)
        compiler, resolver, _missing = do_compile(args.raw_dir, args.build_dir, files)
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
