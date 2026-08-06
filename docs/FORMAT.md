# On-calculator data formats

Specification for the bytecode and asset containers used by the engine.
Implemented by `src/vn.c` (VM), `tools/vnasm.py` (assembler), and
`tools/compile_script.py` (bytecode compiler).

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
```

**Current status**: `tools/compile_script.py` compiles every imported source
file into *one* combined chunk, not yet split at the 16 KB boundary — doing
that requires a loader that can stitch multiple resident chunks back
together for a single script (`src/assets.c`), which doesn't exist yet
(Milestone 3). The combined chunk is still useful today: it's what
`tools/import_game.py` size-checks and packages, and what proves the
compiler and asset resolution work end to end via the host simulator.

## Image assets

- **Backgrounds and CGs share one id space.** `OP_SCENE` has a single
  `bg:u8` operand with no room for a bucket discriminator, so
  `tools/image_resolve.py` allocates backgrounds and CGs from one combined
  list rather than two. Each entry carries a `palette` tag (`"shared"` or
  `"own"`) so the loader (Milestone 3) knows whether to use `pal_game` or
  swap in a per-image palette — the id alone doesn't say which.
- **Backgrounds** (`palette: "shared"`) — scaled to 320x180, quantized
  against the shared game palette, zx0-compressed. Decompressed directly
  into the graphx back buffer (`gfx_buffer`, 76,800 bytes), which costs no
  heap. Solid-color scenes (`scene black`, `scene white`, …) are classified
  here too — cheap enough to render as a flat 320x180 fill.
- **Sprites** — DDLC composites each shown pose from 2-3 layers (e.g. body
  halves + mouth) onto a shared 960x960 canvas, defined per named combo in
  the Ren'Py `Image` declarations (`im.Composite(...)`), not as a fixed
  body/face pair. Rather than layer at runtime, the converter composites
  each *used* combo once at build time with Pillow, crops to its
  non-transparent bounding box, and scales it down to one flat sprite.
  `OP_SHOW` references this single pre-baked id — the VM and renderer never
  composite layers. Stored `rlet` (transparent-run encoded) + zx0.
- **CGs** (`palette: "own"`) — rendered full-screen and alone, scaled to fit
  320x180 preserving aspect (letterboxed), so each carries its own palette,
  swapped in on display and restored afterward.

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
