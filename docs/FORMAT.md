# On-calculator data formats

Specification for the bytecode and asset containers used by the engine.
Implemented by `src/vn.c` (VM), `src/assets.c` (AppVar loader),
`tools/vnasm.py` (assembler), `tools/compile_script.py` (bytecode compiler),
and `tools/import_game.py` (AppVar packaging).

## Design constraints

The TI-84 Plus CE imposes three hard limits that shape everything here:

| Constraint | Value | Consequence |
|---|---|---|
| Screen | 320x240, 8bpp palette | One global palette; images pre-quantized on the host |
| Max TI variable size | 65,535 bytes | Assets split across appvars via `convbin --oformat 8xv-split` |
| User archive | ~3 MB | ~140 MB of source art must reduce ~60x |
| RAM | ~64 KB usable | Only one script chunk and one sprite resident at a time |

## Screen layout

```
y   0 ┌────────────────────────────────┐
      │  scene area, 320x180           │   1280x720 source scales exactly 4:1
      │  sprites anchor feet to y=180  │
y 180 ├────────────────────────────────┤
      │  dialogue box, 320x60          │   opaque, so no alpha blending
y 240 └────────────────────────────────┘
```

## Reserved palette indices

Pinned via convimg `fixed-entries` so UI colors never shift when the palette
is regenerated. The quantizer fills 8..255 in the game palette (`DPALGAME`).
The title palette (`DPALTTL`) additionally pins 8-9 for its own use -- see
"Title screen" -- so its quantizer fills 10..255 instead.

| Index | Name | Use |
|---|---|---|
| 0 | `COL_TRANSPARENT` | sprite transparency (`rlet` key) |
| 1 | `COL_BLACK` | outlines, letterbox |
| 2 | `COL_WHITE` | dialogue text |
| 3 | `COL_BOX_FILL` | dialogue box interior |
| 4 | `COL_BOX_EDGE` | dialogue box border |
| 5 | `COL_NAME` | speaker name plate |
| 6 | `COL_HIGHLIGHT` | selected menu entry |
| 7 | `COL_SHADOW` | drop shadow |

## Bytecode

Little-endian throughout. `u24` operands are absolute offsets into the chunk's
code section. Strings are `u16` indices into the chunk's string pool.

| Op | Value | Operands | Behavior |
|---|---|---|---|
| `OP_NOP` | `0x00` | — | Ignored. Unsupported Ren'Py nodes lower to this |
| `OP_SAY` | `0x01` | `spk:u8 text:u16` | Show a line; blocks for player input. `spk=0xFF` is narration |
| `OP_SCENE` | `0x02` | `bg:u8 trans:u8` | Change background and **clear all actors** (Ren'Py semantics) |
| `OP_SHOW` | `0x03` | `ch:u8 sprite:u8 pos:u8` | Place/update an actor. `sprite` is a single pre-baked image id — see "Image assets" below |
| `OP_HIDE` | `0x04` | `ch:u8` | Remove an actor |
| `OP_MENU` | `0x05` | `n:u8` then `n` x (`text:u16 tgt:u24`) | Choice menu; jumps to the chosen target |
| `OP_JUMP` | `0x06` | `tgt:u24` | Unconditional jump |
| `OP_CALL` | `0x07` | `tgt:u24` | Push return address, jump |
| `OP_RETURN` | `0x08` | — | Pop and jump. **At depth 0 this ends the script** |
| `OP_SET` | `0x09` | `var:u8 val:i16` | Assign a story variable |
| `OP_IF` | `0x0A` | `var:u8 cmp:u8 val:i16 tgt:u24` | Jump **if the comparison holds** |
| `OP_PAUSE` | `0x0B` | `frames:u8` | Idle |
| `OP_SOUND` | `0x0C` | `id:u8` | Reserved. Always a no-op — the CE has no audio hardware. The operand is still consumed so the stream stays aligned |
| `OP_END` | `0x0D` | — | Halt |
| `OP_ADD` | `0x0E` | `var:u8 delta:i16` | Add to a story variable, **saturating** at the `i16` bounds. Lowers Ren'Py's `$ x += 1` |

Comparison selectors for `OP_IF`: `0` EQ, `1` NE, `2` LT, `3` LE, `4` GT, `5` GE.

Sprite anchors for `OP_SHOW`: `0` left, `1` center, `2` right, `3` far-left,
`4` far-right.

Transitions for `OP_SCENE`: `0` cut, `1` fade -- a real dissolve-to-black,
hold, dissolve-back (`src/render.c`'s `render_fade_out()`/`render_fade_in()`),
matching what DDLC's own scene-level transitions all reduce to. See "Scene
transitions" below. `compile_script.py`'s `_emit_With` maps any transition
name containing `_scene` to fade and everything else to cut: DDLC names
every scene-level transition `*_scene*` (`dissolve_scene_full`,
`wipeleft_scene`, ...), and each one is a `MultipleTransition` built the same
way -- dissolve/wipe out to `Solid("#000")`, pause, dissolve/wipe back in --
so matching the name catches the black hold regardless of which visual
effect surrounds it. A bare `dissolve`/`wipeleft` between images (not a
scene change) has no black step and needs alpha blending the 8bpp renderer
doesn't have, so those stay cuts.

### Limits

| Limit | Value | Notes |
|---|---|---|
| `VN_MAX_VARS` | 64 | Out-of-range access halts with `VN_ERR_BOUNDS` |
| `VN_CALL_DEPTH` | 8 | Overflow halts with `VN_ERR_STACK` |
| `VN_MAX_CHOICES` | 6 | Extra options are parsed but not offered, keeping `pc` valid |
| `VN_MAX_CHARS` | 4 | A fifth simultaneous `SHOW` replaces the last slot |
| `TEXT_MAX_LINES` | 4 | The compiler must split longer lines into separate `OP_SAY`s |

Every operand read is bounds-checked. A truncated or corrupt chunk stops the VM
with `VN_ERR_BOUNDS` rather than reading past the buffer.

## Chunking

Scripts are split so that **code + string pool stays under 16 KB decompressed**,
keeping one resident chunk comfortably within RAM. Each chunk is zx0-compressed
into its own appvar; `OP_JUMP`/`OP_CALL` targets are chunk-local, and
cross-chunk transitions are resolved by the loader in `assets.c`.

**Chunk container** (`tools/vnasm.py: Assembler.to_chunk_bytes()`), before
zx0 compression — little-endian throughout:

```
u16  code_length
<code_length bytes of bytecode>
u16  string_count
per string, in pool order (this is the index OP_SAY/OP_MENU's text:u16 uses):
    u16  byte_length
    <byte_length bytes of UTF-8 text>
    1 trailing NUL byte (not counted in byte_length)
```

The trailing NUL lets `src/assets.c` hand out `const char *` pointers straight
into the AppVar (archived, so `ti_GetDataPtr` addresses it directly with no
copy) instead of copying every accessed string into a scratch buffer first.

**Current status**: `tools/compile_script.py` compiles every *selected*
source file (`import_game.py --files`, default `script,script-ch0`) into
one combined chunk. `src/assets.c` loads exactly one such chunk resident and
`src/main.c` runs it — real, on-device gameplay, not just a pipeline
self-test. What's still missing is a loader that can hold multiple chunks
and stitch a jump from one into another: the full Act 1 set (`--files
act1`) compiles fine, but at ~240KB it's well over the ~150KB usable-RAM
ceiling `import_game.py` enforces, so it isn't a valid `--files` selection
yet. Picking a subset that already fits (a chapter, or a few) is how real
assets ship today.

## AppVar naming and lookup tables

`tools/import_game.py` packages everything under 8-character TI names,
matched by `src/assets.c`:

| AppVar(s) | Contents |
|---|---|
| `DSCRIPT` | One chunk container (above), whole — the compiled selection always fits one AppVar in practice |
| `DSPR0`, `DSPR1`, … | Sprite bytes, greedily packed so no sprite's bytes ever straddle an AppVar boundary |
| `DSPRLUT` | Lookup table: which `DSPRn` holds sprite id *i*, and where |
| `DSCN0`, `DSCN1`, … | Background/CG bytes, packed the same way |
| `DSCNLUT` | Lookup table for scene ids |
| `DPALGAME` | The 256-entry shared palette, loaded whole into `gfx_palette` |
| `DTIL0`, `DTIL1`, … + `DTILLUT` | Title screen art (the four cast members, then the logo), packed like sprites |
| `DTILPOS` | Title art layout: `i16 x, y, dx, dy` per id — see "Title screen" |
| `DTILBG` | The title's 370x50 scrolling background strip — see "Title screen" |
| `DPALTTL` | The title screen's own 256-entry palette |
| `DSAVE1`, `DSAVE2`, `DSAVE3` | One player save slot each -- see "Save data" |
| `DNAME` | The saved player name, raw bytes, no NUL -- see "Startup sequence" |

**Lookup table format** (`tools/import_game.py: build_lut()`), little-endian:

```
u16  entry_count
per entry, indexed by sprite/scene id (this is OP_SHOW's sprite:u8 or
OP_SCENE's bg:u8):
    u8   appvar_index    -- e.g. 2 means DSPR2 / DSCN2
    u16  offset           -- byte offset within that AppVar
    u16  length            -- byte length of this entry's data
```

Packing greedily per-AppVar (rather than concatenating everything into one
blob and splitting blindly at the 64KB TI variable boundary) means the
reader never has to stitch bytes across two AppVars for one image — one
`ti_Open` always gets a complete entry.

## Image assets

- **Backgrounds and CGs share one id space.** `OP_SCENE` has a single
  `bg:u8` operand with no room for a bucket discriminator, so
  `tools/image_resolve.py` allocates backgrounds and CGs from one combined
  list (and therefore one `DSCNn` id space) rather than two. Each manifest
  entry carries a `palette` tag (`"shared"` or `"own"`) recording which it
  needs — **not yet consumed by the engine**: `assets_init()` only loads
  `DPALGAME`, so a CG (`palette: "own"`) would currently render with the
  wrong colors. No CG is reachable from the current default `--files`
  selection, so this hasn't been hit in practice, but it's a real gap for
  whenever one is included.
- **Backgrounds** (`palette: "shared"`) — scaled to 320x180, quantized
  against the shared game palette, zx0-compressed (a background's
  decompressed size is always exactly 320x180 — fixed and known, so a
  bounds-checked destination is trivial). `src/assets.c` decompresses
  straight into the graphx draw buffer (`gfx_vbuffer`), which costs no
  extra RAM. Solid-color scenes (`scene black`, `scene white`, …) are
  classified here too — cheap enough to render as a flat 320x180 fill.
- **Sprites** — DDLC composites each shown pose from 2-3 layers (e.g. body
  halves + mouth) onto a shared 960x960 canvas, defined per named combo in
  the Ren'Py `Image` declarations (`im.Composite(...)`), not as a fixed
  body/face pair. Rather than layer at runtime, the converter composites
  each *used* combo once at build time with Pillow, crops to its
  non-transparent bounding box, and scales it down to one flat sprite.
  `OP_SHOW` references this single pre-baked id. Stored `rlet` (transparent-run
  encoded) but **not** zx0-compressed, unlike backgrounds: a sprite's
  decompressed size varies per image and isn't recorded anywhere, and
  `zx0_Decompress` has no bounds-checked API to safely guard a fixed-size
  scratch buffer against it. Shipping sprites uncompressed instead means
  `assets_sprite()` can point `gfx_RLETSprite()` directly at the AppVar's
  flash bytes — zero-copy, zero-decompress, and simpler than getting the
  scratch-buffer sizing right.
- **CGs** (`palette: "own"`) — rendered full-screen and alone, scaled to fit
  320x180 preserving aspect (letterboxed), so each is meant to carry its own
  palette, swapped in on display and restored afterward — see the palette
  gap noted above.

## Scene transitions

`render_fade_out()`/`render_fade_in()` (`src/render.c`) reproduce DDLC's
scene-level transitions by ramping the palette toward black and back with
`gfx_Darken()`, rather than by drawing anything. On an 8bpp display that is
enough: darkening every palette entry darkens the whole screen without
touching a single pixel of the framebuffer, so a fade is 256 palette writes
per step instead of a full redraw. `main.c`'s `host_update()` calls
`render_fade_out()` *before* drawing the new scene (fading out whatever is
still displayed, which is the previous one) and `render_fade_in()` after
presenting the new one under the blacked-out palette.

Pacing this needed `msleep()` (`sys/timers.h`), not the `gfx_Wait()` used
everywhere else in the engine: `gfx_Wait()` only blocks on a pending
`gfx_SwapDraw()`, and a fade never swaps -- it only re-tints an
already-presented frame. Pacing it with `gfx_Wait()` would have made the
whole fade, hold included, collapse into a handful of back-to-back palette
writes with no visible time passing.

## Save data

Each `DSAVEn` is one fixed-layout `save_blob_t` (`src/save.c`), written with a
single `ti_Write`: `pc`, the call stack + `sp`, the story `vars[]`, and the
current scene (background, `actors[]`, speaker, and the *string-pool index*
of the current line -- not a pointer, since `vn_scene_t.text` points into
this run's malloc'd `DSCRIPT` copy (`src/assets.c`), which won't land at the
same address next launch).

This is a position in the *compiled bytecode*, not an abstract story
checkpoint: loading just overwrites those fields on the live `vn_vm_t` and
lets `vn_step()` carry on from `pc` as if nothing happened, since it always
re-reads `pc` fresh rather than caching it across steps. That also means a
save only replays correctly against the exact `script.vnb` (and engine
build) it was written against -- re-running the import pipeline with a
different `--files` selection changes the bytecode layout and invalidates
old saves.

## Title screen

DDLC's main menu shares **nothing** with the in-game sprites — it has its own
art (`gui/menu_art_{s,n,y,m}.png`), logo, background, and nav panel. None of it
is referenced by any dialogue, so `image_resolve.py` resolves these by explicit
path (`title_art()` / `title_logo()` / `title_bg_strip()`) rather than through
the script-driven `sprite_id()`/`scene_id()` used for everything else.

**Layout.** DDLC composes on 1280x720; a uniform 0.25 scale maps that to
320x180, and the remaining 60 rows of the 4:3 screen go *above* the cast
(`CAST_DY`) so the girls stay standing on the bottom edge instead of floating.
The logo is the exception — it's scaled to the *screen* (104px), not
proportionally, because at 0.25 it would be 77px and its wordmark unreadable.

Positions are computed at bake time and shipped in `DTILPOS` rather than being
rederived in C: each sheet is alpha-cropped first (they're 1080-tall canvases
with the figure occupying part of it), which shifts the placement by an amount
only the baker knows. That crop is also load-bearing for a hard limit —
`gfx_rletsprite_t` stores width/height as `uint8_t`, and Monika bakes to 270px
tall untrimmed, over the 255 ceiling. Anything extending past the screen edge is
trimmed for the same reason.

**Background.** `gui/menu_bg.png` is a heart lattice that repeats *exactly* on a
200x200 period (verified: zero pixel difference at both a 200px horizontal and a
200px vertical shift). That is what makes the scroll affordable: a screen-sized
320x240 image would be 76,800 bytes, over the 65,535-byte AppVar ceiling, but one
period scales to exactly 50x50 calculator px, so `DTILBG` ships a 370x50 strip
(one tile wider than the screen) instead — 18,500 bytes. Each screen row is then
a single 320-byte copy from `strip + ((y + py) % 50) * 370 + px`, and DDLC's
infinite diagonal scroll is just advancing `px`/`py`. The strip is downscaled
from the *middle* of a 3x3 block of periods, not from a lone period, because
LANCZOS clamps at image edges and that error would show as a moving seam.

**Palette.** The title uses its own `DPALTTL`, swapped into `gfx_palette` on
entry and swapped back on exit (`assets_use_title_palette()`). Both palettes pin
the same reserved entries 0-7, so every `COL_*` keeps its meaning under either
one and the shared help/save-slot screens render identically. The title palette
additionally pins indices 8-9 (`TITLE_FIXED_ENTRIES`) — the nav panel's two flat
colors. The panel's source overlay turns out to be exactly two opaque
rectangles, so `render.c` fills them rather than shipping ~18KB of art, and its
slide-in animation costs nothing.

**Animation.** DDLC's entrance is reproduced from the ATL in `splash.rpyc`: the
cast rises and slides in, the nav panel slides from the left, and the logo
bounce-drops -- compressed from DDLC's own ~3.45s down to under a second
(`render.c`'s `F_*` keyframe constants), since a homebrew title screen doesn't
need DDLC's patience-testing intro length and the player sees it every time
they back out to the title. Easing uses 32-entry integer LUTs (the eZ80 has no
FPU), linearly interpolated between samples (`render.c`'s `ease()`) rather than
snapped to the nearest one -- 32 samples spread across the intro is coarse
enough that snapping visibly steps the motion, and the bounce curve in
particular has enough high-frequency detail near its end that snapping reads
as jitter instead of a bounce. DDLC also *zooms* the cast during the
entrance; that is deliberately dropped, since graphx has no cheap runtime
scaler for rlet sprites and baking a second scaled set would cost ~35KB to
produce what reads as a jump cut. The background scroll is the only
perpetual motion, matching the real game.

`render_title_screen()`'s time parameter is real elapsed milliseconds
(`clock()`/`CLOCKS_PER_SEC`, `main.c`'s `run_title_screen()`), not a frame
count. An earlier version counted rendered frames on the assumption that
frames arrive at a roughly fixed rate. They don't: the title's per-frame cost
(a full background copy plus five sprite draws) varies enough that a
fixed-duration animation window could span fewer real frames than it had
steps, jumping straight from "not started" to "done" between two draws --
observed as both a "too slow" character entrance and a nav panel that
appeared to snap into place instead of sliding. Driving every curve off
wall-clock time means each frame shows the position correct for the moment
it was drawn, however many or few frames that turns out to be.

Title art is drawn with the underlying AppVar handle kept open across a whole
frame's worth of draws (`assets_draw_title()`/`assets_title_end()`), unlike
`assets_draw_sprite()`'s open-draw-close per call. The title redraws all five
pieces every single frame (the gameplay screens redraw far less densely, and
only ever a few sprites at a time), so five `ti_Open`/`ti_Close` pairs a frame
was worth avoiding. This still never holds a `ti_GetDataPtr` pointer across an
*unrelated* AppVar open -- see `assets.c`'s file comment for why that matters.

## Startup sequence

Before the title screen ever shows, `main()` runs, in order: the Team Salvato
logo, DDLC's content warning, and (only if no name is saved yet) the
player-name entry screen.

The logo is `bg/splash.png`, baked as an ordinary background scene
(`ImageResolver.splash_scene()`) rather than through the title's own art
pipeline -- it needs no title-style positioning or palette, so riding the
existing `DSCNn`/`DPALGAME`/`assets_scene()` path as-is costs zero new C code.
It's baked *first*, before any dialogue is compiled, specifically so its scene
id is always 0 (`src/main.c`'s `SPLASH_LOGO_SCENE`) regardless of which
chapters get compiled in.

The content warning is DDLC's real line (`splash.rpy`'s
`splash_message_default`): "This game is not suitable for children / or those
who are easily disturbed." It's plain text over a white backdrop, not a baked
image -- DDLC itself renders it as text (`ParameterizedText`), so there was
nothing to bake.

Both screens fade in, hold, and fade out (`render_fade_out()`/`render_fade_in()`,
see "Scene transitions"); any key skips ahead to the next screen rather than
past both at once.

**Player name.** DDLC asks for this once, at the start of Act 1
(`renpy.input()`), and substitutes it into every later `[player]` in dialogue
(confirmed: exactly the literal token, no Ren'Py formatting tags, 10
occurrences across `script`+`script-ch0`, never more than one per line).
There's no bytecode support for suspending mid-script for player text input --
`OP_SAY`/`OP_MENU`'s fixed operand shapes have no room for it, and adding one
would mean a new opcode for what's really a one-time setup question. Instead,
`src/main.c` asks once at startup (whenever `DNAME` doesn't exist yet, i.e.
first launch) via a classic name-entry-screen letter picker, and every
`host_string()` call substitutes `[player]` for the saved name before the
engine ever sees the text -- `assets_string()`/`vn.c` are unaware substitution
happens at all. `save.c`'s `save_load()` goes through `vm->host->string()`
rather than `assets_string()` directly for the same reason: a loaded line
needs the substitution exactly as much as one reached by playing forward does.

## Character presence AppVars

`src/chars.c` creates four empty AppVars — `SAYORI`, `NATSUKI`, `YURI`,
`MONIKA` — as the on-calc stand-in for each character's Ren'Py `.chr` file.
They carry no content; only their existence is meaningful. The Act 2/3 "file
deletion" meta effect is implemented as `ti_Delete` on the relevant AppVar,
and `chars_present()` lets the engine ask the filesystem directly rather than
tracking deletion state separately. `chars_init()` only creates AppVars that
are missing, so a deletion from a prior session persists across restarts.

## Known compiler gap: `Menu` item conditions are not evaluated

`renpy.ast.Menu.items` is a list of `(caption, condition, block)`.
`compile_script.py`'s `_emit_Menu` reads `caption` and `block` but ignores
`condition` — every item is always offered, regardless of story state.

This showed up concretely in `script-poemresponses.rpyc`'s poem-sharing menu
("Sayori / Natsuki / Yuri / Monika"), which DDLC narrows as girls are visited
(a condition like "not yet shared with"). Replayed through the host simulator
with a naive "always pick option 0" auto-player, an unnarrowed menu never
exhausts, so the poem-sharing sequence loops until the simulator's line-count
safety cap trips it — not a crash or an unknown opcode, but not a clean
finish either. Verified independently of this: `script-ch0.rpyc` alone
replays to a real `OP_RETURN` finish, and the full combined chunk correctly
plays real dialogue and a real menu selection from `label start` before
reaching this section.

Fixing this needs per-item conditions evaluated at menu-*display* time (story
state can change between visits to the same menu), which the current
`OP_MENU n:u8 [text:u16 tgt:u24]*` encoding has no room for. Deferred
alongside the rest of the poem minigame (see README's Status list) rather
than extending the bytecode format for it now.
