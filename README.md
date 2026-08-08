# ddlc-ti84plusce

A visual novel engine for the TI-84 Plus CE, built with the
[CE C/C++ toolchain](https://github.com/CE-Programming/toolchain), plus a local
pipeline that converts your own legally obtained copy of Doki Doki Literature
Club into calculator-ready assets.

## Status

The engine loads and plays real DDLC dialogue on real hardware/CEmu — not a
placeholder script. `tools/import_game.py` extracts, compiles, and packages
Chapter 0 (by default) end to end into a bundle the engine reads directly.
The full Act 1 selection (`--files=act1`) also works now, each chapter its
own resident chunk swapped in on demand, and **the entire game
(`--files=all`) now fits in one bundle's worth of real calculator archive
space** — see "Building the full bundle".

- [x] Bytecode VM with branching, menus, subroutines and story variables
- [x] 320x180 scene area + 60px dialogue box, typewriter text reveal --
      Ren'Py's inline text tags (`{i}`, `{cps=30}`, `{nw}`, ...) are stripped
      at compile time rather than shown as literal characters
- [x] Real asset pipeline: `.rpa` archives extracted, images composited and
      quantized, script compiled to bytecode, everything packaged into
      AppVars and bundled into one `.b84`
- [x] Character-presence AppVars (`src/chars.c`) for the Act 2/3 file-deletion
      effect
- [x] Horizontal character positioning taken directly from DDLC's own
      position transforms (`transforms.rpy`), not bucketed into a handful of
      fixed anchors -- a multi-character scene keeps its real, even spacing
- [x] A speaking pop: whichever character is currently talking rises
      slightly, easing in and back out as the speaker changes (an
      approximation of DDLC's 1.05x speaking zoom -- graphx can't scale a
      sprite at runtime)
- [x] Scene transitions: a real dissolve-to-black-and-back for DDLC's
      scene-level transitions, done as a palette ramp rather than a redraw
- [x] A title screen built from DDLC's actual menu art (`gui/menu_art_*.png`,
      the logo, the scrolling background), with its own palette and its
      entrance animation at close to DDLC's own pacing
- [x] Startup sequence: the real Team Salvato logo, DDLC's content warning,
      and (first launch only) a name-entry screen, typed directly on the
      keypad -- the name is substituted into every `[player]` in dialogue
      from then on
- [x] Pause menu (`mode`), 3 save slots, and a help screen listing the
      keybinds
- [x] Per-CG palettes: a CG renders under its own 256-entry palette rather
      than the shared game one, swapped in sync with scene transitions
- [x] Host simulator for testing without a calculator, including replaying
      real compiled chunks (`--vnb`, repeatable for a multi-chunk build)
- [x] The poem-writing minigame: the real 228-word bank, real 20-round/
      10-word-a-round mechanics, a real winner computed from the real
      scoring -- see docs/FORMAT.md's "Poem minigame" for what's
      deliberately not reproduced (the easter eggs gated behind
      multi-playthrough state this engine doesn't track, and cosmetic
      character-sticker animations)
- [x] Multi-chunk script loading: every compiled file gets its own resident
      chunk, swapped on demand as Jump/Call crosses a boundary, so
      `--files=act1` (the full ch0-ch4 set, plus what it calls into) now
      actually runs rather than only compiling -- see docs/FORMAT.md's
      "Chunking"
- [x] The whole game fits real hardware archive space: most character
      sprites are a body pose plus an expression layer under the hood, and
      rather than baking every body+expression combination into its own
      full image, each distinct body/expression is baked once and
      composited at draw time; backgrounds are zx0-compressed and
      decompressed once per scene change instead of shipped raw. Brought
      `--files=all`'s real on-calc footprint from ~9MB down to ~2.16MB
      against a real ~2.9MB archive -- see docs/FORMAT.md's "Image assets"

## Building the engine

Requires the CE toolchain. If `cedev-config` is not on your `PATH`:

```bash
export PATH="$HOME/CEdev/bin:$PATH"
```

Then:

```bash
make
```

This produces `bin/DDLC.8xp`. On its own it shows an "assets not found" screen
— it needs the AppVars from the asset pipeline too (see below) to have
anything to play.

### Controls

| Key | Action |
|---|---|
| `2nd` / `enter` | Advance text, confirm a choice |
| `up` / `down` | Move between menu options |
| `mode` | Pause menu (in-game); cancel out of a submenu |
| `clear` | Quit |

The name-entry screen (first launch only) is typed directly on the keypad,
same as it would be in the TI-OS itself -- letters follow the printed ALPHA
labels (`MATH`=A, `APPS`=B, `PRGM`=C, …), plus `del` to erase and `enter` to
confirm.

## Building the full bundle

The asset pipeline needs its own Python environment (Pillow, PyYAML, unrpa)
and `convimg`/`convbin` from the CE toolchain. A one-time setup:

```bash
python3 -m venv .venv
.venv/bin/pip install -r tools/requirements.txt
export PATH="$HOME/CEdev/bin:$(pwd)/.venv/bin:$PATH"
```

Then, against your own legally obtained copy of the game:

```bash
make bundle GAME_DIR=/path/to/DDLC-1.1.1-pc/game
```

This runs the whole pipeline and produces `build/DDLC.b84`, containing the
engine and every converted AppVar. Subsequent runs can skip the slow steps
(`--skip-extract`, `--skip-convimg` via `IMPORT_FLAGS`) if only the engine or
the script changed — see `tools/import_game.py --help`.

By default only `script` + `script-ch0` (a complete, working Chapter 0) are
compiled. Pass `IMPORT_FLAGS="--files=act1"` to compile and bundle the whole
of Act 1 instead (ch0-ch4 plus what they call into, including the poem
minigame), or `IMPORT_FLAGS="--files=all"` for every script file DDLC ships
— all three acts, every exclusive route. Either way each file becomes its
own resident chunk (or several, for the one file too big for one — see
docs/FORMAT.md's "Chunking"), so this actually runs rather than just fitting
on disk.

**The whole game fits in one bundle now**, on real hardware: `--files=all`
needs ~2.16MB of actual on-calc archive space (every AppVar's own size, plus
the program), against a real ~2.9MB archive — see `import_game.py`'s build
output for the exact current numbers (printed against `build/appvars/*`,
not `.b84` size — see the note below). That's down from an original ~9MB:
most character art is a body pose plus an expression layer under the hood,
and rather than baking every body+expression *combination* into its own
full sprite, each distinct body and expression is baked once and composited
at draw time (docs/FORMAT.md's "Image assets"), plus backgrounds are
zx0-compressed and decompressed once per scene change rather than shipped
raw.

**Don't judge archive fit by `.b84` size.** `.b84` is a ZIP container, so its
size reflects deflate compression on top of everything already inside it —
a `.b84` can look well under budget while what actually has to land in the
calculator's archive (every AppVar at its own uncompressed size) is several
times bigger. This was a real failure mode, not just a docs footnote: an
earlier build's `.b84` measured 2.63MB against a 2.5MB internal target and
looked fine, but actually needed 5.8MB on-calc and failed to fully transfer.
`make bundle`'s own build output now reports both numbers — check the
second one.

## Layout

| Path | Purpose |
|---|---|
| `src/vn.c` | Bytecode VM — platform-independent |
| `src/text.c` | Word wrapping — platform-independent |
| `src/render.c` | Scene composition, title screen, and transitions via graphx |
| `src/assets.c` | AppVar loader — reads everything tools/import_game.py packages |
| `src/save.c` | Save/load slots |
| `src/name.c` | Player name storage + `[player]` dialogue substitution |
| `src/poem.c` | The poem-writing minigame (`OP_MINIGAME`'s host callback) |
| `src/chars.c` | Character-presence AppVars (file-deletion effect) |
| `src/main.c` | Entry point: keypad input, splash/name-entry, title screen, pause menu, VM wiring |
| `src/demo.c` | Placeholder script (generated), used as a host-simulator regression check |
| `tools/vnasm.py` | Bytecode assembler + chunk container format |
| `tools/gen_demo.py` | Regenerates `src/demo.c` |
| `tools/rpyc_ast.py` | Safe stub-unpickler for compiled `.rpyc` files |
| `tools/image_resolve.py` | Resolves Ren'Py image names (and DDLC's title art) to baked PNGs (Pillow) |
| `tools/compile_script.py` | AST -> bytecode |
| `tools/extract.py` | Unpacks `.rpa` archives (unrpa) |
| `tools/convert_images.py` | Manifest -> `convimg.yaml`, runs convimg |
| `tools/import_game.py` | Single entry point tying the above together |
| `tools/host_sim/` | Native test harness |
| `docs/FORMAT.md` | Bytecode and asset format spec |

## Loading it onto a calculator or CEmu

`build/DDLC.b84` contains the engine and every converted AppVar, but it is
itself a ZIP archive (that's what `convbin -k b84` produces) — CEmu's
drag-and-drop does not accept it directly. Send the individual files instead:
`bin/DDLC.8xp` plus every `.8xv` under `build/appvars/`. TI Connect CE, or
another tool that understands `.b84` group files directly, can use the bundle
as-is.

## License

Code for the engine and the asset pipeline is licensed under the MIT License. See [LICENSE](LICENSE) for details. Any DDLC assets you use must be obtained separately and legally, in accordance with Team Salvato's IP Guidelines.

Not affiliated with or endorsed by Team Salvato.
