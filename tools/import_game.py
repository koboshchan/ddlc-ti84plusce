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
Chapter 0). Each compiled file becomes its own resident "chunk" (one DSCRn
AppVar); only one is ever resident on-calc at a time, swapped in by
src/assets.c's assets_load_chunk() whenever a Jump/Call crosses a chunk
boundary (see docs/FORMAT.md's "Chunking"), so --files=act1 (the full
ch0-ch4 set, plus the labels it calls into) now actually runs rather than
just compiling.
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
import vnasm
from compile_script import (VN_STR_BASE, CompileError, Compiler, link_chunks,
                            load_transform_animations, load_variable_defaults)
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
    "script-poemgame", "script-poemresponses", "script", "splash",
]

# Every real script file DDLC ships (all three acts, every exclusive route,
# both poem-response files) -- available via --files=all. script-ch30 (Act
# 3's finale) is the one file that alone exceeds a single AppVar's 65535
# bytes (67954 measured); compile_script.py's compile_file_chunked() splits
# it across chunks automatically, so this doesn't need special-casing here.
ALL_FILES = [
    "script-ch0", "script-ch1", "script-ch2", "script-ch3", "script-ch4",
    "script-ch5", "script-ch10", "script-ch20", "script-ch21", "script-ch22",
    "script-ch23", "script-ch30", "script-ch40",
    "script-exclusives-sayori", "script-exclusives-natsuki", "script-exclusives-yuri",
    "script-exclusives2-natsuki", "script-exclusives2-yuri",
    "script-poemgame", "script-poemresponses", "script-poemresponses2",
    "script", "splash",
]

DEFAULT_FILES = ["script", "script-ch0", "splash"]

ARCHIVE_BUDGET = 2_900_000  # ~2.9MB real hardware archive capacity, confirmed
                             # on-device (see docs/FORMAT.md's "Design
                             # constraints") -- checked against the raw sum
                             # of every packaged AppVar's own bytes, NOT the
                             # .b84 size: .b84 is a ZIP container, so its size
                             # reflects deflate compression on top of
                             # everything already inside it. A build that
                             # looks well under budget by .b84 size can still
                             # be several times over what actually has to
                             # land in archive -- confirmed as a real failure
                             # on-hardware, not just a docs gap.

# The budget the build is actually held to, below the raw capacity above so
# there is room to install and to keep saves. Exceeding it *fails the build*
# rather than printing a warning: a warning scrolls past in a few hundred
# lines of convimg output, and the failure it precedes -- an AppVar that
# won't fit in archive -- doesn't surface until the calculator is out of
# space mid-chapter. The full game measures 2,282,897 bytes today, so the
# margin exists to be spent deliberately on Act 2/3 art, not by accident.
# Override with --archive-budget if a build genuinely needs to run over.
ARCHIVE_LIMIT = 2_850_000

MAXVARSIZE = 65000  # safely under the 65535-byte TI variable cap; also the
                     # real per-chunk ceiling now, since each chunk ships as
                     # exactly one AppVar (no multi-piece packing like
                     # DSPR/DSCN) -- comfortably under the ~150KB one
                     # resident chunk actually has room for, so nothing
                     # measured so far has come close to needing more.

# Title screen cast, with DDLC's own image-level ATL constants from
# splash.rpyc (xcenter, ycenter, zoom against the 1280x720 canvas).
#
# The list index IS the id src/render.c draws by (see assets.h's title enum),
# so this order must stay in sync with it. The logo is appended after these
# (id 4) by resolver.title_logo(). The actual z-order is render.c's business
# and is not this list's order -- it interleaves: yuri, natsuki, nav panel,
# menu text, logo, sayori, monika.
#
# The nav panel is deliberately absent: its source overlay turns out to be two
# flat opaque rectangles (#FFE6F4 out to x=279, #FFBDE1 to x=309), so render.c
# draws it with two fills instead of shipping ~18KB of art -- which also makes
# its slide-in animation free.
TITLE_ART = [
    ("yuri",    "gui/menu_art_y.png",  600, 335, 0.60),  # behind the nav panel
    ("natsuki", "gui/menu_art_n.png",  750, 385, 0.58),  # behind the nav panel
    ("sayori",  "gui/menu_art_s.png",  510, 500, 0.68),  # in front of the logo
    ("monika",  "gui/menu_art_m.png", 1000, 640, 1.00),  # frontmost
]


def step(msg: str) -> None:
    print(f"\n=== {msg} ===")


def do_extract(game_dir: Path, raw_dir: Path, skip: bool) -> None:
    step("extract")
    if skip and raw_dir.exists():
        print(f"skipping (--skip-extract, {raw_dir} already exists)")
        return
    extract.extract(game_dir, raw_dir)


def do_compile(raw_dir: Path, build_dir: Path,
               files: list[str]) -> tuple[Compiler, list[vnasm.Assembler], ImageResolver, list[str]]:
    step("compile script + resolve images")
    resolver = ImageResolver(raw_dir, build_dir)
    compiler = Compiler(resolver=resolver,
                        transform_animations=load_transform_animations(raw_dir))

    # A background no dialogue references by name, baked first and
    # unconditionally (regardless of --files) so its scene id is always the
    # same value -- matching src/main.c's SPLASH_LOGO_SCENE=0. Everything
    # dialogue bakes afterward gets whatever id comes next.
    resolver.explicit_bg_scene("bg/splash.png")  # Team Salvato logo (splash.rpyc's `intro` ATL)

    # The poem minigame's notebook background is unconditional too (same
    # reasoning), but full-screen and outside the DSCNn scene id space --
    # see poem_background()'s docstring.
    resolver.poem_background()

    # One Assembler (chunk) per compiled file, or more than one for a file
    # too big to fit a single chunk (compile_file_chunked() -- only
    # script-ch30 needs this today). compiler.variables/last_sprite/last_pos
    # stay on the shared Compiler instance across all of them (see
    # compile_script.py's Compiler docstring), only compiler.asm gets
    # swapped out before each new chunk.
    assemblers: list[vnasm.Assembler] = []
    for name in files:
        path = raw_dir / f"{name}.rpyc"
        if not path.is_file():
            print(f"  ! {name}.rpyc not found in {raw_dir}, skipping")
            continue
        compiler.asm = vnasm.Assembler(chunk_id=len(assemblers))
        assemblers.extend(compiler.compile_file_chunked(path, len(assemblers)))

    # The title screen's own art set (see TITLE_ART). These are plain `gui/`
    # files, not Ren'Py image names -- no dialogue references them, so the
    # resolver's script-driven sprite_id()/scene_id() never reach them and
    # they have to be named explicitly here.
    for name, rel_path, xcenter, ycenter, zoom in TITLE_ART:
        resolver.title_art(name, rel_path, xcenter, ycenter, zoom)
    resolver.title_logo()
    resolver.title_bg_strip()

    missing = link_chunks(assemblers)
    resolver.write_manifest()

    # Which interned strings (compiler.var_strings) also happen to name a
    # real compiled label -- the table OP_JUMP_VAR/OP_CALL_VAR resolve
    # against at runtime (see vn_host_t.resolve_label, assets.c's DVLBL).
    # Re-derives the same name -> (chunk_id, offset) mapping link_chunks()
    # built internally, rather than changing its return type, since every
    # asm._labels entry is already exactly that (chunk_id from asm.chunk_id,
    # a global label name never repeating between files -- Ren'Py enforces
    # that, link_chunks()'s own docstring already relies on it).
    global_labels: dict[str, tuple[int, int]] = {}
    for asm in assemblers:
        for name, offset in asm._labels.items():
            global_labels[name] = (asm.chunk_id, offset)
    compiler.var_labels: dict[int, tuple[int, int]] = {}   # interned id -> (chunk_id, offset)
    for text, str_id in compiler.var_strings.items():
        if text in global_labels:
            compiler.var_labels[str_id] = global_labels[text]

    # Which slots hold a Ren'Py `persistent.*` value -- the ones that must
    # survive past this playthrough (New Game, even a fresh install),
    # unlike every other story variable, which starts over from
    # load_variable_defaults() every time. Identified purely by name
    # convention: _ident_name() already renders `persistent.playthrough` or
    # `persistent.clear[0]` as a string starting with "persistent.", the
    # same convention Attribute/Subscript resolution already established
    # for these, nothing persistence-specific had to be taught to the
    # compiler itself. See src/persist.c for how this list gets used.
    compiler.persistent_slots = sorted(
        slot for name, slot in compiler.variables.items() if name.startswith("persistent."))

    # Ren'Py's `default` values (definitions.rpy), for every variable that
    # actually got a slot -- see load_variable_defaults()'s docstring for
    # why this can't just be done inside the Compiler as files compile
    # (definitions.rpyc is never one of the compiled files). Resolved here,
    # not left as raw text, so a string default is interned through this
    # same Compiler instance and lands in the one DVSTR pool everything
    # else's interned strings share -- calling compiler._intern() after
    # compilation is done still assigns a fresh id if this default is the
    # first thing to use that particular string.
    raw_defaults = load_variable_defaults(raw_dir)
    compiler.var_defaults = {}
    for name, slot in compiler.variables.items():
        if name not in raw_defaults:
            continue
        value = raw_defaults[name]
        compiler.var_defaults[slot] = compiler._intern(value) if isinstance(value, str) else value

    total_code = sum(len(a.code) for a in assemblers)
    total_strings = sum(len(a.strings) for a in assemblers)
    print(f"code: {total_code} bytes across {len(assemblers)} chunks, "
          f"{total_strings} strings, {len(compiler.variables)} variables")
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

    return compiler, assemblers, resolver, missing


def find_label_pc(assemblers: list[vnasm.Assembler], name: str) -> tuple[int, int] | None:
    """(chunk_id, local offset) for label @p name, wherever it landed --
    found by name rather than assumed to be any fixed chunk/offset, since
    --files order doesn't guarantee one (tools/vnasm.py's docs/FORMAT.md
    "Chunking" section). None if @p name isn't in the compiled --files
    selection at all."""
    for asm in assemblers:
        if name in asm._labels:
            return asm.chunk_id, asm._labels[name]
    return None


def find_entry_point(assemblers: list[vnasm.Assembler]) -> tuple[int, int]:
    """Ren'Py's real entry point, `label start`, usually lives in the
    `script` file."""
    found = find_label_pc(assemblers, "start")
    if found is not None:
        return found
    print("  ! no 'start' label in the compiled --files selection; "
          "defaulting entry to chunk 0 offset 0")
    return 0, 0


def write_chunks(assemblers: list[vnasm.Assembler], build_dir: Path) -> list[Path]:
    paths = []
    for asm in assemblers:
        chunk = asm.to_chunk_bytes()
        if len(chunk) > MAXVARSIZE:
            sys.exit(f"chunk {asm.chunk_id} is {len(chunk)} bytes, over the "
                     f"{MAXVARSIZE}-byte single-AppVar ceiling -- split this file "
                     f"selection into smaller per-file chunks (see docs/FORMAT.md's "
                     f"'Chunking' section).")
        path = build_dir / f"chunk{asm.chunk_id}.vnb"
        path.write_bytes(chunk)
        paths.append(path)
    return paths


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


def do_poem_words(raw_dir: Path) -> bytes | None:
    """Parses poemwords.txt (assets/raw's own copy, one `word,sPoint,nPoint,
    yPoint` line each -- see docs/FORMAT.md's "Poem minigame" section) into
    DPOEM's format: u16 count, then per word u8 len, word bytes (no NUL,
    len-prefixed instead), u8 sPoint, u8 nPoint, u8 yPoint. None if the file
    isn't present (extraction wasn't run, or this is a very old raw_dir) --
    shipped unconditionally otherwise regardless of --files, same as the
    title screen's assets: harmless and tiny (~2.5KB) if the poem minigame
    isn't reachable from whatever was compiled.
    """
    path = raw_dir / "poemwords.txt"
    if not path.is_file():
        return None

    words: list[tuple[bytes, int, int, int]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        word, s, n, y = line.split(",")
        word_bytes = word.encode("utf-8")
        if len(word_bytes) > 255:
            raise CompileError(f"poem word too long for a u8 length prefix: {word!r}")
        # Real values are always small whole numbers (1-3) despite the
        # source file spelling them as floats ("3.0") -- see poemgame's
        # PoemWord class, which does the same float() conversion.
        words.append((word_bytes, int(float(s)), int(float(n)), int(float(y))))

    out = bytearray(struct.pack("<H", len(words)))
    for word_bytes, s, n, y in words:
        out += struct.pack("<B", len(word_bytes))
        out += word_bytes
        out += struct.pack("<BBB", s, n, y)
    return bytes(out)


def do_package(build_dir: Path, appvar_dir: Path, raw_dir: Path, manifest: dict,
               chunk_paths: list[Path], entry_chunk: int, entry_offset: int,
               compiler: Compiler, splash_pc: tuple[int, int] | None = None) -> list[Path]:
    step("package AppVars (convbin)")
    require("convbin", 'export PATH="$HOME/CEdev/bin:$PATH"')
    gfx_dir = build_dir / "gfx"

    def bin_for(png_name: str) -> Path:
        return gfx_dir / (Path(png_name).stem + ".bin")

    appvars: list[Path] = []
    for chunk_path in chunk_paths:
        chunk_id = int(chunk_path.stem.removeprefix("chunk"))
        appvars.append(write_appvar(chunk_path.read_bytes(), ti_name("DSCR", chunk_id), appvar_dir))

    # Packed (chunk_id << 16 | offset) for `label start` -- see vn.h's
    # VN_PACK_ADDR and find_entry_point(). src/assets.c reads this instead of
    # assuming pc=0 is the entry, since --files order no longer guarantees
    # that (see docs/FORMAT.md's "Chunking").
    appvars.append(write_appvar(struct.pack("<I", (entry_chunk << 16) | entry_offset),
                                "DENTRY", appvar_dir))

    # Optional, like DVSTR/DVDEF/DVLBL below: only shipped if splash.rpyc
    # was in this build's --files selection and its `label splashscreen`
    # (compile_script.py's curated stub -- see _emit_Label) actually
    # compiled. src/assets.c's assets_splash_pc() degrades gracefully to
    # "no startup splashscreen check" when this AppVar is simply missing.
    if splash_pc is not None:
        splash_chunk, splash_offset = splash_pc
        appvars.append(write_appvar(struct.pack("<I", (splash_chunk << 16) | splash_offset),
                                    "DSPLASH", appvar_dir))

    # The interned string pool (compile_script.py's VN_STR_BASE). Ships
    # separately from the per-chunk dialogue strings, and not inside any
    # chunk, because a variable set in one chunk is read in another: a name
    # assigned by script.rpyc has to still render three chunks later. Tiny --
    # 37 strings, under a kilobyte for the whole game -- so it stays resident
    # rather than being swapped like a chunk.
    #
    # Same container layout as a chunk's string pool (see vnasm.Assembler's
    # to_bytes): u16 count, then per string a u16 byte length, the UTF-8
    # bytes, and a trailing NUL so src/assets.c can hand out a `const char *`
    # pointing straight into the archived AppVar.
    pool = bytearray(struct.pack("<H", len(compiler.var_strings)))
    for text in compiler.var_strings:                 # insertion order == id order
        encoded = text.encode("utf-8")
        pool += struct.pack("<H", len(encoded)) + encoded + b"\x00"
    appvars.append(write_appvar(bytes(pool), "DVSTR", appvar_dir))

    # The dynamic-jump/call label table (compiler.var_labels, built above
    # right after link_chunks()) -- u16 count, then per entry u16 (interned
    # id - VN_STR_BASE) + u32 packed (chunk_id<<16 | offset). Relative ids
    # keep every entry in u16 rather than needing the full i16 range signed;
    # src/assets.c adds VN_STR_BASE back when matching a lookup against it.
    labels = bytearray(struct.pack("<H", len(compiler.var_labels)))
    for str_id, (chunk_id, offset) in sorted(compiler.var_labels.items()):
        labels += struct.pack("<HI", str_id - VN_STR_BASE, (chunk_id << 16) | offset)
    appvars.append(write_appvar(bytes(labels), "DVLBL", appvar_dir))

    # Which slots are `persistent.*` (compiler.persistent_slots, built above)
    # -- u16 count, then one u8 slot per entry. Read-only, compiler-generated,
    # small (22 entries for the full game today); see src/persist.c for how
    # this drives what actually gets saved/restored across a New Game.
    pslots = bytearray(struct.pack("<H", len(compiler.persistent_slots)))
    for slot in compiler.persistent_slots:
        pslots += struct.pack("<B", slot)
    appvars.append(write_appvar(bytes(pslots), "DPSLOT", appvar_dir))

    # Ren'Py's `default` values, applied to a fresh vars[] before a New
    # Game/Continue starts (see src/main.c's assets_apply_var_defaults()) --
    # u16 count, then per entry u8 slot + i16 value. Small and fixed
    # (32 entries today), so like DVSTR it isn't per-chunk.
    defaults = bytearray(struct.pack("<H", len(compiler.var_defaults)))
    for slot, value in sorted(compiler.var_defaults.items()):
        defaults += struct.pack("<Bh", slot, value)
    appvars.append(write_appvar(bytes(defaults), "DVDEF", appvar_dir))

    poem_words = do_poem_words(raw_dir)
    if poem_words is not None:
        appvars.append(write_appvar(poem_words, "DPOEM", appvar_dir))

    sprite_files = [bin_for(s["file"]) for s in manifest["sprites"]]
    appvars += package_group(sprite_files, "DSPR", "DSPRLUT", build_dir, appvar_dir)

    # Per-sprite (dx, dy) draw-offset table, matching DSPRLUT's order --
    # nonzero only for a layered "expression" atom (image_resolve.py's
    # _bake_layer_atom()), letting src/assets.c's assets_draw_sprite() draw
    # it in alignment with its body atom using the same center/feet anchor
    # math as an ordinary sprite, just nudged. (0, 0) for every other
    # sprite reproduces today's behavior exactly -- see docs/FORMAT.md's
    # "Layered sprites".
    if manifest["sprites"]:
        sprite_offsets = b"".join(struct.pack("<hh", s.get("dx", 0), s.get("dy", 0))
                                  for s in manifest["sprites"])
        appvars.append(write_appvar(sprite_offsets, "DSPROFF", appvar_dir))

    scene_files = [bin_for(s["file"]) for s in manifest["scenes"]]
    appvars += package_group(scene_files, "DSCN", "DSCNLUT", build_dir, appvar_dir)

    appvars.append(write_appvar((gfx_dir / "pal_game.bin").read_bytes(), "DPALGAME", appvar_dir))

    # Title screen: art + its layout table + the scrolling background strip,
    # all quantized against their own palette (see convert_images.py).
    title_art = manifest.get("title_art") or []
    if title_art:
        title_files = [bin_for(t["file"]) for t in title_art]
        appvars += package_group(title_files, "DTIL", "DTILLUT", build_dir, appvar_dir)

        # Resting position and entrance offset per title art id, computed at
        # bake time (alpha-cropping shifts them, so render.c can't rederive
        # them from DDLC's ATL constants alone): i16 x, y, dx, dy.
        pos = b"".join(struct.pack("<hhhh", t["x"], t["y"], t["dx"], t["dy"])
                       for t in title_art)
        appvars.append(write_appvar(pos, "DTILPOS", appvar_dir))
        appvars.append(write_appvar((gfx_dir / "pal_title.bin").read_bytes(),
                                    "DPALTTL", appvar_dir))

    if manifest.get("title_bg"):
        appvars.append(write_appvar(
            bin_for(manifest["title_bg"]["file"]).read_bytes(), "DTILBG", appvar_dir))

    if manifest.get("poem_bg"):
        # 320x240 raw (76800 bytes) is over the 65535-byte single-AppVar
        # ceiling -- split at a fixed, MAXVARSIZE-safe boundary rather than
        # through pack_group()/a LUT (there's only ever this one resource,
        # a LUT would be pure overhead), matching the split src/assets.c's
        # assets_poem_bg() expects.
        poem_bg_bytes = bin_for(manifest["poem_bg"]["file"]).read_bytes()
        appvars.append(write_appvar(poem_bg_bytes[:MAXVARSIZE], "DPOEMBG0", appvar_dir))
        appvars.append(write_appvar(poem_bg_bytes[MAXVARSIZE:], "DPOEMBG1", appvar_dir))

    cg_count = sum(1 for s in manifest["scenes"] if s["palette"] == "own")
    if cg_count:
        cg_pal_files = [gfx_dir / f"pal_cg_{i:03d}.bin" for i in range(cg_count)]
        appvars += package_group(cg_pal_files, "DCGPAL", "DCGPLUT", build_dir, appvar_dir)

        # Scene id -> index into DCGPLUT, one byte per scene in DSCNLUT order
        # (0xFF for a "shared"/background scene, which has no own palette).
        # src/assets.c's assets_scene_palette() is the reader.
        cg_index = bytes(s["cg_palette_index"] if s["cg_palette_index"] is not None else 0xFF
                         for s in manifest["scenes"])
        appvars.append(write_appvar(cg_index, "DCGIDX", appvar_dir))

    total = sum(p.stat().st_size for p in appvars)
    print(f"{len(appvars)} AppVars, {total} bytes total "
          f"({len(sprite_files)} sprites + {len(scene_files)} scenes)")
    return appvars


def do_bundle(prog_8xp: Path, appvars: list[Path], out_path: Path,
              limit: int = ARCHIVE_LIMIT) -> None:
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

    # The real constraint: what actually has to sit in the calculator's
    # archive, uncompressed -- NOT out_path's size. .b84 is a ZIP container
    # (convbin -k b84), so its size reflects deflate on top of everything
    # already inside it, and can look well under budget while still being
    # several times over on-hardware -- see ARCHIVE_BUDGET's comment.
    raw_total = sum(f.stat().st_size for f in inputs)
    b84_size = out_path.stat().st_size
    print(f"{out_path}: {b84_size} bytes (.b84, deflate-compressed -- "
          f"informational only)")
    print(f"on-calc archive usage: {raw_total} bytes (every AppVar's own "
          f"size, plus the program)")
    if raw_total > limit:
        biggest = sorted(inputs, key=lambda f: f.stat().st_size, reverse=True)[:5]
        detail = "\n".join(f"    {f.stat().st_size:>9} {f.name}" for f in biggest)
        sys.exit(
            f"  ! {raw_total} bytes is over the {limit}-byte archive budget "
            f"by {raw_total - limit}.\n"
            f"  This is the uncompressed total that has to live in the "
            f"calculator's archive; {out_path.name}'s own size is not the "
            f"measure (it is deflate-compressed).\n"
            f"  Largest contributors:\n{detail}\n"
            f"  Reduce assets, or pass --archive-budget to raise the ceiling "
            f"deliberately (hard capacity is ~{ARCHIVE_BUDGET}).")
    print(f"  within the {limit}-byte archive budget "
          f"({raw_total / limit:.0%} used, {limit - raw_total} bytes spare)")


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
                    help="comma-separated .rpyc stems to compile/bundle, 'act1' "
                         "for the full ch0-ch4 set, or 'all' for every script "
                         "file DDLC ships (all three acts, every exclusive route). "
                         "Each file becomes its own resident chunk (or several, "
                         "for the one file too big for one -- see the module "
                         "docstring. Default: script,script-ch0")
    ap.add_argument("--skip-extract", action="store_true")
    ap.add_argument("--skip-convimg", action="store_true")
    ap.add_argument("--archive-budget", type=int, default=ARCHIVE_LIMIT,
                    help=f"fail the build if on-calc archive usage exceeds this "
                         f"many bytes (default {ARCHIVE_LIMIT})")
    args = ap.parse_args()

    args.build_dir.mkdir(parents=True, exist_ok=True)
    if args.files == "act1":
        files = ACT1_FILES
    elif args.files == "all":
        files = ALL_FILES
    else:
        files = args.files.split(",")

    try:
        do_extract(args.game_dir.expanduser().resolve(), args.raw_dir, args.skip_extract)
        compiler, assemblers, resolver, _missing = do_compile(args.raw_dir, args.build_dir, files)
        entry_chunk, entry_offset = find_entry_point(assemblers)
        splash_pc = find_label_pc(assemblers, "splashscreen")
        chunk_paths = write_chunks(assemblers, args.build_dir)
        do_convert_images(args.build_dir, args.quality, args.skip_convimg)
        manifest = json.loads((args.build_dir / "manifest.json").read_text())
        appvars = do_package(args.build_dir, args.appvar_dir, args.raw_dir, manifest,
                             chunk_paths, entry_chunk, entry_offset, compiler, splash_pc)
        do_bundle(args.prog, appvars, args.out, args.archive_budget)
    except CompileError as e:
        sys.exit(f"compile error: {e}")
    except subprocess.CalledProcessError as e:
        sys.exit(f"{e.cmd[0]} failed with exit code {e.returncode}")

    print(f"\ndone. {len(chunk_paths)} chunks + {args.build_dir / 'gfx'} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
