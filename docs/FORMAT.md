# On-calculator data formats

Specification for the bytecode and asset containers used by the engine.
Implemented by `src/vn.c` (VM) and `tools/vnasm.py` (assembler).

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
