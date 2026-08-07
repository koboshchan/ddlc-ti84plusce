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
is regenerated. The quantizer fills 8..255.

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

Transitions for `OP_SCENE`: `0` cut, `1` fade. Fade currently presents as a cut
until the palette-ramp pass lands.

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

## Save data

A single appvar holding the chunk id, `pc`, the variable table, and the current
scene state.

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
