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
| User archive | ~2.9 MB real-hardware capacity | ~140 MB of source art must reduce ~50x |
| RAM | ~150 KB usable | Only one script chunk and one sprite resident at a time -- see "Chunking" |

**The archive budget is against the *uncompressed sum of every packaged
AppVar's own bytes*, not `.b84` size.** `.b84` is a ZIP container
(`convbin -k b84`), so its size reflects deflate compression on top of
whatever's already inside it — a `.b84` that looks well under budget can
still be several times over on-calculator, since AppVars land in archive at
their own declared size, not compressed further. This was a real bug, not
just a documentation gap: `import_game.py`'s own budget check compared
`.b84` size against the target, so a build could report "39% used" while
actually needing 2x the real archive. Confirmed on-hardware: a `.b84`
reporting 2.63MB (well under the old check) actually needed 5.8MB of real
AppVar bytes, and failed to fully transfer (some AppVars landed missing).
Check the sum of `build/appvars/*.8xv` sizes, not `build/DDLC.b84`'s.

## Screen layout

```
y   0 ┌────────────────────────────────┐
      │  scene area, 320x180           │   1280x720 source scales exactly 4:1
      │  sprite canvases hang from     │   render.c's ACTOR_BASELINE
y 180 ├──  y=185, DDLC's ypos 1.03  ───┤
      │  dialogue box, 320x60          │   opaque, so no alpha blending
y 240 └────────────────────────────────┘   (and painted over the overshoot)
```

Characters deliberately overshoot the scene area: DDLC anchors every
character transform at `yanchor 1.0, ypos 1.03`, so the sprite canvas's
bottom edge sits 3% of the screen height *below* the screen bottom and the
lower body is cut off. Here that overshoot falls under the dialogue box,
which is drawn afterwards. Sprites are scaled by a fixed
`image_resolve.py`'s `SPRITE_SCALE` (DDLC's own `z=0.80` character zoom
times the 4:1 background ratio), *not* normalized to a common on-screen
height — the characters are genuinely different heights in the source art
(Natsuki 781 canvas px against Yuri's 899) and normalizing flattened that
out, lining all four heads up along the top of the screen.

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

Little-endian throughout. `u24` operands (`OP_JUMP`/`OP_CALL`/`OP_IF`/
`OP_MENU`'s `tgt`) are **packed cross-chunk addresses**, not plain offsets --
`(chunk_id << 16) | local_offset` -- see "Chunking" below. Strings are `u16`
indices into the *currently resident* chunk's string pool (chunk-local, not
global).

| Op | Value | Operands | Behavior |
|---|---|---|---|
| `OP_NOP` | `0x00` | — | Ignored. Unsupported Ren'Py nodes lower to this |
| `OP_SAY` | `0x01` | `spk:u8 text:u16` | Show a line; blocks for player input. `spk=0xFF` is narration |
| `OP_SCENE` | `0x02` | `bg:u8 trans:u8` | Change background and **clear all actors** (Ren'Py semantics) |
| `OP_SHOW` | `0x03` | `ch:u8 sprite:u16 overlay:u16 pos:u8 flags:u8` | Place/update an actor. `sprite`/`overlay` are pre-baked image ids drawn at the same anchor, `overlay` `VN_NO_OVERLAY` for a single-layer sprite — see "Image assets" below. `flags` is `VN_FLAG_ZOOM` (0x01) / `VN_FLAG_HOP` (0x02) / `VN_FLAG_SINK` (0x04), DDLC's real per-line speaking/movement signal — see "The speaking pop" |
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
| `OP_MINIGAME` | `0x0F` | `winner_var:u8 s_var:u8 n_var:u8 y_var:u8` | Runs a host-side minigame screen; stores the winner (`TAG_TO_CHAR` order: 0 sayori, 1 natsuki, 2 yuri) in `vars[winner_var]` and each character's cumulative appeal total in `vars[s_var]`/`vars[n_var]`/`vars[y_var]`. Only the poem word-picking game exists today — see "Poem minigame" |

`sprite:u16` (not `u8`): the full game needs 406 distinct sprite ids across
Act 1, over a byte's range. Scene/background ids stay `u8` -- only 17 are
ever needed.

Comparison selectors for `OP_IF`: `0` EQ, `1` NE, `2` LT, `3` LE, `4` GT, `5` GE.

`OP_SHOW`'s `pos` is half the on-screen center X: `screen_x = pos * 2`, 2px
granularity across the 320px scene. `tools/compile_script.py`'s
`_pos_from_x()` computes it straight from the real X a DDLC position
transform (`transforms.rpy`'s `t21`, `f22`, ...) carries on Ren'Py's
1280-wide canvas, scaled by the same 0.25 factor used for backgrounds
(halved again, `/8` not `/4`, so it fits a byte). An earlier version instead
bucketed X into one of 5 fixed anchors (left/center/right/far-left/
far-right); that lost DDLC's real spacing -- a 4-character scene's own
transforms place them at roughly even quarters of the canvas, but two of
those always landed in adjacent buckets while the middle bucket sat unused,
rendering as two overlapping pairs with a gap between them instead of four
evenly spaced characters.

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

`OP_SAY`/`OP_MENU` text is plain -- no Ren'Py inline tags (`{i}`, `{cps=30}`,
`{w=0.5}`, `{nw}`, ...). `compile_script.py`'s `_strip_text_tags()` removes
every `{...}` from `Say.what`/`Menu` captions at compile time (`{{` is
Ren'Py's own escape for a literal `{`, preserved rather than stripped); the
renderer has no italics, per-span color, or variable typing speed to give
any of them meaning, so leaving them in would only show up as literal brace
characters in the dialogue box.

### Limits

| Limit | Value | Notes |
|---|---|---|
| `VN_MAX_VARS` | 256 | The format ceiling -- every opcode encodes a variable as `u8` |
| `VN_CALL_DEPTH` | 8 | Overflow halts with `VN_ERR_STACK` |
| `VN_MAX_CHOICES` | 6 | Extra options are parsed but not offered, keeping `pc` valid |
| `VN_MAX_CHARS` | 4 | A fifth simultaneous `SHOW` replaces the last slot |
| `TEXT_MAX_LINES` | 4 | The compiler must split longer lines into separate `OP_SAY`s |

Every operand read is bounds-checked by construction now, not by a runtime
guard: with `VN_MAX_VARS` at the `u8` operand's own ceiling, every possible
slot index is valid, so the opcodes that index `vars[]` (`OP_SET`/`OP_ADD`/
`OP_IF`/`OP_MINIGAME`) do so unchecked -- a `_Static_assert` in `vn.c` fails
the build if `VN_MAX_VARS` is ever lowered back under 256, since that would
silently reopen an out-of-bounds index. A truncated or corrupt chunk still
stops the VM with `VN_ERR_BOUNDS` everywhere else an operand needs range
validation (jump targets, string indices).

### Story variables: numbers and interned strings

`vars[]` holds `int16_t` and nothing else -- there is no runtime string type.
DDLC's own scripts frequently assign a **string** to a story variable
(`nextscene = "sayori_end"`, `s_name = "???"`) and later compare it for
equality (`if ch2_winner == "Natsuki":`). Since the value is only ever
compared, never concatenated or measured, an integer serves exactly as well
as the string it stands for -- so `tools/compile_script.py` interns every
distinct string literal assigned to or compared against a variable, at
compile time, into an integer at or above `VN_STR_BASE` (16384). `OP_SET`/
`OP_IF` then work on it exactly like any numeric variable; no VM change was
needed.

The pool of interned strings ships once, in `DVSTR` (not per-chunk, unlike
dialogue text): `u16 count`, then per string `u16 byte_length`, the UTF-8
bytes, and a trailing NUL, same layout as a chunk's own string pool. It is
small (32 strings across the full game, 528 bytes) and loaded once at
startup rather than swapped with the resident chunk, since a value set in
one chunk is routinely read back several chunks later -- `src/assets.c`'s
`assets_var_string(value)` resolves an `i16` back to its text, returning
`NULL` for an ordinary number (`value < VN_STR_BASE`).

Ids start at 16384 rather than 0 so a string-valued variable's value can
never collide with the small integers a numeric variable holds -- nothing in
the game compares one variable against both kinds, so this is redundant
safety, but it also makes a wrong value obvious on sight in a trace (a
`vars[]` dump) instead of looking like a plausible counter.

DDLC also keeps per-chapter state in plain Python lists --
`poemwinner[1]`, `n_poemappeal[0]` -- rather than one variable per chapter.
The VM has no notion of a list or of runtime indexing, but every real use of
these in the whole game indexes with a literal integer, never a variable, so
none is needed: `tools/compile_script.py`'s `_ident_name()` turns
`name[literal_int]` into its own distinct variable name (`"poemwinner[1]"`)
at compile time, and everything downstream (`_var_slot()`, `_intern()`)
treats it exactly like a variable the script had actually named that. A
non-constant index (`poemwinner[chapter]`) can't resolve to one fixed slot
this way and degrades like any other unsupported expression -- see "Poem
minigame" for how the real chapter-aware access pattern gets handled.
Leading-underscore bases (`_history_list`, Ren'Py's own internal state, not
DDLC's) are deliberately excluded from this -- see `_ident_name`'s comment.

Four variable slots are reserved before anything else can claim one:
`s_name`/`n_name`/`y_name`/`m_name` get slots `0..3`, in the same order as
`TAG_TO_CHAR`'s character ids (`Compiler.NAME_VARS`, applied in
`__post_init__`). That makes a character's id *also* the slot holding her
currently displayed name (`VN_NAME_VAR(ch)` in `vn.h`), so
`src/main.c`'s `speaker_display_name()` can render the dialogue box's name
plate straight from `vars[VN_NAME_VAR(speaker)]` with nothing shipped to map
between them. This is what makes "???" work: DDLC's own script sets these
variables to `"???"` before a character's introduction and to her real name
at the moment she gives it, so the plate is driven by story state instead of
a fixed table naming every character from her first line.

## Chunking

`tools/compile_script.py` gives every *selected* source file
(`import_game.py --files`, default `script,script-ch0`) its own chunk --
its own code, its own string pool, its own `vnasm.Assembler`. Only one
chunk is ever resident on-calc at a time (`src/assets.c`'s
`assets_load_chunk()`, ~150KB budget for one resident buffer); a Jump/Call
that crosses a chunk boundary swaps the resident one out.

This wasn't always true: an earlier version compiled every selected file
into one combined chunk. That worked for a small selection (the default
`script,script-ch0`) but broke down for anything bigger -- the full Act 1
set's combined chunk measured 254,842 bytes, well over one resident
buffer's budget, and (separately) needed 406 distinct sprite ids against
`OP_SHOW`'s then-`u8` `sprite` operand (fixed alongside this, see the
Bytecode table above). Per-file chunking sidesteps both: every file except
one fits in one AppVar with room to spare (ch0=20242 bytes; the largest
that fits whole, `script-poemresponses`, is 62004), so `--files=act1` and
`--files=all` (every script file DDLC ships -- `import_game.py`'s
`ALL_FILES`, all three acts and every exclusive route) are both valid,
working selections now, not just compiles-but-can't-run ones.

The one exception is `script-ch30` (Act 3's finale), whose own compiled
output alone is 67,954 bytes -- just over a single AppVar. `Compiler.
compile_file_chunked()` splits a file like this automatically: it tracks
code+string size as it walks the file's top-level nodes, and if a size
threshold is crossed right before a top-level `Label`, it flushes any
pending scene, emits an explicit `Jump` to that label, and starts a fresh
chunk there. This is safe *only* at a top-level Label boundary -- Ren'Py
lets sequential top-level labels fall through into each other with no
explicit Jump between them, so replacing that fall-through with a real one
at a chosen label is behaviorally identical, not a semantic change; splitting
inside a label's own block isn't attempted, and no single label has come
close to the budget by itself. `--files=all` produces 23 chunks this way
(`script-ch30` becomes two); its packaged bundle measures ~3.14MB, over
this project's own ~3MB hardware archive figure (see "Design constraints"
above) -- it compiles, links, and replays correctly through `host_sim`, but
isn't expected to fit a stock calculator's user archive as one bundle.
`--files=act1`'s ~2.63MB does fit that ceiling, just not the more
conservative 2.5MB internal target.

**Packed addresses.** `OP_JUMP`/`OP_CALL`/`OP_IF`/`OP_MENU`'s `u24` target,
`vn_vm_t.pc`, and `vn_vm_t.stack[]` all encode `(chunk_id << 16) |
local_offset` (`vn.h`'s `VN_PACK_ADDR`/`VN_CHUNK_ID`/`VN_CHUNK_OFFSET`) --
free, not an added cost: the chunk container's `code_length` is already
capped at `u16` (65535 below), so 16 bits was already the most any local
offset could need, and a single-chunk build (chunk_id always 0) packs to
exactly the same value a flat offset would have. `src/vn.c`'s `vn_step()`
compares the resident chunk against a target's packed chunk_id on every
step and calls the host's `load_chunk` to swap when they differ --
optional (`NULL` means a single-chunk build; any cross-chunk target then
fails with `VN_ERR_BOUNDS`, same as any other invalid address).

**Compiling and linking.** `tools/import_game.py`'s `do_compile()` gives
each file its own `vnasm.Assembler(chunk_id=N)` in `--files` order; the
`Compiler` instance (and its `variables`/`last_sprite`/`last_pos`) stays
shared across all of them, since `vars[]` is one flat array regardless of
which chunk is resident. `compile_script.py`'s `link_chunks()` then merges
every chunk's local labels into one `name -> (chunk_id, offset)` table and
resolves every chunk's pending Jump/Call/If/Menu targets against it --
Ren'Py enforces globally-unique label names, so this can't collide. A name
missing from every chunk (content outside the compiled `--files` set) is
stubbed to a local `OP_END` in whichever chunk references it first (same
"content wasn't imported" idea as before), registered into the merged
table so a second chunk referencing the same missing name lands on that
same stub instead of getting a second one.

**Entry point.** Ren'Py's real entry, `label start`, lives in the `script`
file -- but `--files` order doesn't guarantee which chunk that ends up in
(`ACT1_FILES` puts `script` last; the default file list puts it first), so
assuming pc=0 isn't safe. `find_entry_point()` looks it up by name instead
and packages the result as `DENTRY` (see the AppVar table below); `src/
main.c` reads it at startup and before every "New Game"/"Continue" (a
previous session may have swapped to a different chunk, and
`assets_load_chunk()` frees the old buffer on every swap, so re-using a
`code`/`code_size` pointer captured earlier would be stale).

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

## AppVar naming and lookup tables

`tools/import_game.py` packages everything under 8-character TI names,
matched by `src/assets.c`:

| AppVar(s) | Contents |
|---|---|
| `DSCR0`, `DSCR1`, … | One chunk container (above) per compiled file, whole — every chunk measured so far fits one AppVar; see "Chunking" |
| `DENTRY` | Packed `(chunk_id << 16) \| offset` for `label start` — see "Chunking" |
| `DVSTR` | The interned variable-string pool, `u16 count` + per-string `u16 len` + UTF-8 bytes + NUL — see "Story variables: numbers and interned strings" |
| `DPOEM` | The poem minigame's word bank — see "Poem minigame" |
| `DSPR0`, `DSPR1`, … | Sprite bytes, greedily packed so no sprite's bytes ever straddle an AppVar boundary |
| `DSPRLUT` | Lookup table: which `DSPRn` holds sprite id *i*, and where |
| `DSPROFF` | Per-sprite `i16 dx, i16 dy` draw-offset, `DSPRLUT` order -- see "Image assets" |
| `DSCN0`, `DSCN1`, … | Background/CG bytes, packed the same way |
| `DSCNLUT` | Lookup table for scene ids |
| `DPALGAME` | The 256-entry shared palette, loaded whole into `gfx_palette` |
| `DTIL0`, `DTIL1`, … + `DTILLUT` | Title screen art (the four cast members, then the logo), packed like sprites |
| `DTILPOS` | Title art layout: `i16 x, y, dx, dy` per id — see "Title screen" |
| `DTILBG` | The title's 370x50 scrolling background strip — see "Title screen" |
| `DPALTTL` | The title screen's own 256-entry palette |
| `DPOEMBG0`, `DPOEMBG1` | The poem minigame's notebook background, full-screen (320x240 = 76800 bytes) raw indices under the shared game palette, split in two at a fixed 65000-byte boundary (over the single-AppVar ceiling; no LUT, just the one resource) — see "Poem minigame" |
| `DCGPAL0`, `DCGPAL1`, … + `DCGPLUT` | One 256-entry palette per CG, packed like sprites -- see "Image assets" |
| `DCGIDX` | Scene id -> `DCGPLUT` index (`0xFF` for a scene with no own palette), one byte per scene in `DSCNLUT` order |
| `DSAVE1`, `DSAVE2`, `DSAVE3` | One player save slot each -- see "Save data" |
| `DNAME` | The saved player name, raw bytes, no NUL -- see "Startup sequence" |

**Lookup table format** (`tools/import_game.py: build_lut()`), little-endian:

```
u16  entry_count
per entry, indexed by sprite/scene id (this is OP_SHOW's sprite:u16 or
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
  needs. `DCGIDX` (see the AppVar table above) is the bridge: it maps a scene
  id to its `DCGPLUT` index, or `0xFF` for a `"shared"` scene.
  `assets_scene_palette()` reads it and hands back a pointer to the right
  256-entry palette, defaulting to the shared game palette whenever a scene
  has no entry (including every id when no CG was baked at all — the whole
  `DCGIDX`/`DCGPAL*` group is optional, same as the title assets).
  `main.c`'s `host_update()` applies the returned pointer to `gfx_palette`,
  not `assets_scene_palette()` itself — see "Per-CG palettes" below for why
  that split matters.
- **Backgrounds** (`palette: "shared"`) — scaled to 320x180, quantized
  against the shared game palette, zx0-compressed (a background's
  decompressed size is always exactly 320x180 — fixed and known, so a
  bounds-checked destination is trivial). `src/assets.c`'s `assets_scene()`
  is called every frame `render_scene()` runs (every typewriter tick, every
  idle-bob redraw), not just on an actual scene change, and it decompresses
  straight into the caller's destination buffer on every one of those calls
  — a small malloc'd cache (decompress only when the requested id changes,
  `memcpy` from the cache the rest of the time) was tried, but even sized to
  exactly one background (57.6KB) it overflowed real on-device RAM alongside
  the graphics draw buffer and a resident script chunk, so it was reverted.
  Backgrounds stay compressed anyway (uncompressed, 24 backgrounds alone need
  ~1.3MB against a real ~2.9MB archive, which doesn't fit at all) — this
  trades the per-frame decompress cost for archive space that has no cheaper
  alternative, rather than the reverse. Solid-color scenes (`scene black`,
  `scene white`, …) are classified here too — cheap enough to render as a
  flat 320x180 fill.
- **Sprites** — DDLC composites each shown pose from Ren'Py `Image`
  declarations (`im.Composite(...)`), most commonly two "body" layers (e.g.
  left/right halves) plus one "expression" layer, all layered at (0, 0) on
  a shared per-character canvas. A character's ~10 bodies and ~30
  expressions combine multiplicatively into 300+ Show-able combos; flattening
  every combo into its own full bake (the original approach) ships the same
  body pixels hundreds of times over. Instead, `tools/image_resolve.py`'s
  `sprite_layers()` bakes each distinct body ("base") and expression
  ("overlay") atom exactly once — measured on Act 1's actual sprite set,
  406 combos reduce to 203 baked atoms, and total baked pixel area drops
  ~4x, since expression atoms are far smaller crops than a full body.
  `OP_SHOW` carries both ids; `overlay` is `VN_NO_OVERLAY` for anything that
  doesn't fit this shape (a non-composite image, fewer than two layers, or
  any layer positioned off (0, 0) — a handful of accessory overlays are),
  which falls back to one full flattened bake exactly as before layering
  existed.

  Alignment: every atom is scaled by the fixed `SPRITE_SCALE` and gets a
  baked-in `(dx, dy)` — see `DSPROFF` in the AppVar table — chosen so
  `src/assets.c`'s existing `center_x - w/2, feet_y - h` anchor math
  reproduces the atom's position *within its own composite canvas*
  (`_canvas_offsets`). Measuring against the canvas rather than against the
  character's content box is what Ren'Py itself does — `xcenter` centers
  the displayable and `yanchor` pins its edge, both regardless of where the
  drawn pixels fall inside it — and it means two atoms cropped from the
  same canvas line up automatically, with no shared per-character reference
  to establish or keep consistent.

  Stored `rlet` (transparent-run encoded) but **not** zx0-compressed: a
  sprite's decompressed size varies per image and isn't recorded anywhere,
  and `zx0_Decompress` has no bounds-checked API to safely guard a
  fixed-size scratch buffer against it (unlike backgrounds, where the
  decompressed size is always the fixed `SCENE_BYTES`). Shipping sprites
  uncompressed instead means `assets_draw_sprite()` can point
  `gfx_RLETSprite()` directly at the AppVar's flash bytes — zero-copy,
  zero-decompress. The layering dedup above already does most of the real
  work sprite compression would have (measured ZX0 on RLE-encoded sprite
  data separately: only ~18% smaller, since RLE already removes most of
  the redundancy compression would otherwise find).
- **CGs** (`palette: "own"`) — rendered full-screen and alone, scaled to fit
  320x180 preserving aspect (letterboxed), so each is meant to carry its own
  palette rather than share the game's. `convert_images.py` quantizes it
  against its own 256-entry palette (fixed-entries 0-7 pinned, same as
  `pal_game`/`pal_title`, so the dialogue box drawn over a CG still reads the
  right colors) and packs it into the `DCGPAL*`/`DCGPLUT` group.

## The speaking pop

DDLC's own ATL (`transforms.rpy`'s `focus`/`hopfocus` vs. `tcommon`/`hop`)
zooms whichever character is currently speaking to 1.05x, and separately
bounces briefly for `hop`-flagged lines. A third, unrelated transform,
`sink`, drifts a character down and *holds* there until a later line lands
them back on `t`/`f` -- rarer than zoom (~30 uses game-wide vs. ~650 for
zoom) but common enough early in Chapter 0 (twice in the first 45 lines) to
be one of the first things a player notices. All three are authored
directly per line in the real script -- confirmed by decompiling
`transforms.rpy` and grepping every `Show`/`Say` `at` list across
`script-ch0..ch4.rpyc`: each screen position has several named transform
variants (`t11` base/1.00x zoom, `f11` focused/speaking/1.05x zoom, `h11` a
one-shot bounce at 1.00x, `hf11` both together, `s11` sink), and
`f{pos}`/`t{pos}` show up in near-equal counts per position (e.g. ch2:
`f32`=29, `t32`=29) -- the real script explicitly authors "at f32" for the
exact line where that position's character is speaking, "at t32" otherwise.
`tools/compile_script.py`'s `load_transform_animations()` reads this
straight out of the compiled transform names (classifying each name's
letter prefix) rather than inferring "who's speaking" from `OP_SAY`'s
speaker field the way an earlier version of this engine did, and `OP_SHOW`
carries the result as a `flags:u8` bitmask (`VN_FLAG_ZOOM`/`VN_FLAG_HOP`/
`VN_FLAG_SINK` -- see the bytecode table above).

Sink's recovery is authored on the *landing* transform, not on sink itself:
decompiling `tcommon`/`focus` found both ease `ypos` back to their resting
1.03 over .15s in their own "replace" handler (the transition applied when
a Show switches a still-visible character onto them), in parallel with
whatever zoom change is also happening -- confirmed by reading their
compiled ATL directly rather than assumed from the name. `render.c`'s
`sink_offset()` reproduces this as a persistent on/off state (like
`zoom_fallback_offset()` below, not `hop_offset()`'s one-shot bounce) but
with asymmetric durations per direction: 500ms easing down to
`SINK_PX` (DDLC's real 0.03 `ypos` fraction of the 720-tall canvas, scaled
by this engine's existing 0.25 canvas-to-screen ratio) when a Show is
sink-flagged, 150ms easing back to 0 the next time it isn't. Sink never
combines with zoom or hop in the real script (they're mutually exclusive
transform families), so `draw_actor()` just adds its offset to `feet_y`
unconditionally alongside the hop check, no interaction to resolve.

`src/assets.c`'s `assets_draw_sprite_zoomed()` performs the real 1.05x scale:
a fixed 21:20 nearest-neighbor resample (DDLC never uses another zoom value
here, so no general scaler is needed -- and `graphx`'s own
`gfx_RotateScaleSprite` requires a *square* input sprite anyway, which
character atoms never are). It decodes the sprite's `rlet` data
(`gfx_ConvertFromRLETSprite()` -- `graphx` has no incremental/streaming
decode, so a full decode is unavoidable), resamples it into a scaled plain
sprite, and **caches that bitmap**.

The cache is what makes the scale affordable, and it works because the zoom
is *constant*: DDLC's speaking zoom is one fixed 1.05x, and this engine
animates a character's position (hop, sink, the eased fallback rise) but
never its scale — so across every frame of a speaking line the scaled pixels
are identical and only the destination y moves. Building them per frame
meant an AppVar open, a full decode and a full resample for a bitmap that
never changed; now the first frame pays that and every frame after is a
single `gfx_TransparentSprite()` blit, no dearer than an ordinary sprite.

Two cache slots, which is measured rather than guessed: replaying all of
Act 1 through `tools/host_sim --trace`, no scene state ever carries more
than one `VN_FLAG_ZOOM` actor (72 of 281 have exactly one, the rest none),
and a zoomed actor needs at most two bitmaps — its body atom and its
expression atom. Two slots hold a whole speaking character with nothing to
evict, and a new speaker replaces both within one frame.

`assets_load_chunk()` drops the cache before reading, since a script chunk
(up to ~62KB) is this program's largest allocation and a cached body atom is
~14KB — losing a redraw optimization is cheap, failing a chunk load is not.
Building a bitmap can still fail for want of memory, so `render.c`'s
`draw_actor()` falls back to the small eased vertical rise this engine used
exclusively before real scaling existed (`zoom_fallback_offset()`, kept for
exactly this) whenever the real scale doesn't run, whether because the
sprite wasn't flagged for it or because the bitmap couldn't be built.
`assets_zoom_prepare()` commits both of a character's layers to that
decision together, so a small expression atom can never succeed on a frame
where the large body atom failed (which drew a 1.05x head on a 1.00x body).
The rise eases in/out over 250ms using the same integer
LUT/`ease()` the title screen's entrance uses; the one-shot hop
(`hop_offset()`) is two back-to-back applications of that same `ease()`
primitive covering DDLC's real `.1s` down / `.1s` up, triggered by
`vn_actor_t.show_seq` (bumped on every real `OP_SHOW`, not part of the wire
format -- see the bytecode table) rather than diffing the drawn sprite id,
since DDLC sometimes uses `hop` purely for emphasis on an otherwise-unchanged
pose.

Two properties of the scaled path are load-bearing enough to be worth
stating outright, because getting either wrong is visible on the calculator
rather than merely slow:

- **A character zooms whole or not at all.** `draw_actor()` calls
  `assets_zoom_prepare()` for both layers before drawing either, so one
  answer covers the pair. Deciding per layer instead let them disagree under
  memory pressure: the body is by far the bigger request, so it was the one
  that failed, while the small expression atom allocated fine immediately
  after — putting a 1.05x head on a 1.00x body.
- **No division in the inner loop.** The resample ratio is fixed, so the
  source index is walked with a Bresenham accumulator (advance one source
  pixel per output pixel, except every 21st, which repeats) instead of
  computing `x * 20 / 21` per pixel. The eZ80 has no divide instruction, so
  each of those was a software routine costing far more than the byte copy
  it guarded, over ~25000 pixels per layer. This now runs once per cached
  bitmap rather than once per frame, and writing into a plain destination
  sprite means no per-pixel clipping either — `gfx_TransparentSprite()`
  handles that at blit time.

Redraw frequency matters as much as redraw cost here, which is
`render_scene_lazy()`'s job (see `src/render.h`). It skips the whole
background-decode-plus-actor-pass when the previous `render_scene()` left
every offset above at rest. The three offset helpers each report whether
their own ease is still in flight, so "at rest" is exact. An earlier version
instead waited out a fixed 500ms — the longest transition any of them can
run — after every `OP_SHOW`, which meant a full rebuild of the scene on
every typewriter tick for half a second even in the common case where
nothing was easing at all and the first frame drawn was already final.

**The moving-actor plate.** When something *is* easing, the frame rate is
what decides whether the motion reads as smooth or stepped, and the cost is
almost entirely `draw_background()`: a zx0 decode of all 57,600 bytes of the
scene area, dwarfing the sprite blits over it. At that price only two or
three frames fit inside a 200ms hop, so a motion with six distinct pixel
positions showed as two or three.

Caching the decoded background outright was tried and reverted (commit
`5c5604a`) — at 57.6KB it does not fit beside the ~77KB draw buffer and a
resident script chunk. But an animation doesn't change a whole background:
one actor moves, by a few pixels, so the only region that can differ is that
actor's own rectangle. So `render.c` saves exactly that rectangle during a
full redraw — taken *after* the background and every actor behind the mover
are down, which is what preserves z-order — and later frames paste it back
and redraw only the mover and whatever draws in front of it. No decode, no
full-scene pass.

The plate reconstructs exactly one moving actor, so a second simultaneous
mover disables it and everything falls back to full redraws; replaying Act 1
through `host_sim` finds no scene state carrying more than one
zoom/hop/sink flag, but correctness doesn't rest on that. It's freed the
moment the scene settles, so it holds nothing while the player is reading,
and like every allocation here it's best-effort: no plate simply means
animating frames stay full redraws.

None of that needs hardware to check — it's geometry over the baked sprites.
`tools/check_plate.py` mirrors `actor_rect()` / `draw_actor()` /
`render_scene_moving()` and compares plate frames against the full redraws
they must be indistinguishable from, pixel for pixel, over every mover slot,
the whole offset range, zoomed and not, in scenes of two to four overlapping
characters. Two details there are worth keeping if it's ever rewritten:
it composites with *binary* alpha (the real sprites are 8bpp indexed with
index 0 transparent, so drawing is idempotent — antialiased alpha reports
false differences where the plate redraws an unmoved actor over itself), and
it walks a whole trajectory from one capture rather than checking a single
frame. The second matters more than it sounds: the engine captures once per
animation and reuses the plate for every frame of it, so a frame must erase
wherever the *previous* one left the actor. Checking one frame per capture
hides exactly what `PLATE_SLACK` prevents, since the rectangle covers the
resting position either way and only the following step strands pixels.

Both this and the scaled-sprite cache above are why
`compile_script.py`'s `CHUNK_SIZE_BUDGET` is 24000 rather than sitting just
under the 65000-byte AppVar ceiling: the resident chunk shares ~150KB with
the draw buffer, and 58000-budget chunks (which reached 62.8KB) left ~10KB
free — too little for either allocation to succeed on the busiest chapters,
which are precisely the ones that need them.

An earlier version still ran a continuous idle sine wobble unrelated to who
was talking and never settling -- wrong on both counts, and superseded by
all of the above.

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

### Per-CG palettes

Most scenes render under the shared game palette, but a CG carries its own
(see "Image assets"). `main.c`'s `host_update()` looks it up once per update
via `assets_scene_palette()` and applies it differently depending on `trans`:

- **Cut** (`render_apply_palette()`): writes it straight to `gfx_palette`.
  Instant, which is what a cut means anyway.
- **Fade** (`render_fade_retarget()`): called between `render_fade_out()` and
  drawing the new scene, while the screen is already held at black. It
  overwrites what `render_fade_in()` will later ramp *up to*, without
  touching what's currently on screen (it re-applies the same full-black
  hold, just computed from the new palette's values instead of the old
  one's). Applying the new palette directly at this point instead — the same
  thing `render_apply_palette()` does for a cut — would pop it to full
  brightness immediately: the *pixels* on screen are still the old scene's
  (the new one hasn't been drawn yet), so they'd flash under a palette that
  doesn't correspond to them, for exactly one frame, before drawing catches
  up. Sandwiching the retarget inside the black hold instead means the
  palette and the pixel data change together, both hidden.

## The tear glitch effect

DDLC's own `show screen tear(...)`/`hide screen tear` is Act 2's signature
visual: real DDLC implements `tear` as a custom Python `Displayable` class
(`effects.rpy`), not as ATL or a compiled screen body, so unlike everything
else this pipeline reads out of a `.rpyc`, its exact pixel algorithm is
**not recoverable** from the compiled game -- there is no bytecode for it to
decompile. `OP_TEAR_SHOW`/`OP_TEAR_HIDE` (`0x14`/`0x15`) and `render.c`'s
`apply_tear()` are a faithful reinterpretation from the effect's name, its
five parameters, and what "screen tearing" means as a genre convention --
not a claim of matching the original frame-for-frame.

`tools/compile_script.py`'s `_parse_tear_call()` reads the real call's
arguments (`show screen tear(20, 0.1, 0.1, 0, 40)`, defaults from the real
`screen tear(number=10, offtimeMult=1, ontimeMult=1, offsetMin=0,
offsetMax=50, srf=None)`) and emits `OP_TEAR_SHOW(chunks, offset_min,
offset_max, period_ms)`. `offtimeMult`/`ontimeMult` scale some on/off
cadence this engine has no access to; folding both into one re-roll period
(`max(offtimeMult, ontimeMult) * TEAR_BASE_MS`) is a reinterpretation of
"the glitch flickers faster or slower", not a precision claim.

At render time, the scene area splits into `chunks` horizontal bands. Each
band independently re-rolls a random horizontal displacement (magnitude
uniform in `[offset_min, offset_max]`, sign random -- every real call site
passes `offsetMin=0`, so randomizing sign too is what keeps the bands from
all drifting one direction, which would read as a slide rather than a
tear) every `period_ms`, applied by shifting each row in the band with
wraparound at the screen edges rather than exposing a blank strip. Bands
drift out of sync with each other over time, which is what makes it read
as a glitch rather than the whole scene sliding as one piece.

The effect never lets `render_scene_lazy()` settle while shown (every band
keeps re-rolling, so no frame is ever identical to the last) and
disqualifies the moving-actor plate for the same frame (`apply_tear()`
touches rows across the whole scene area, not one actor's rectangle, so the
plate's single-rectangle repair can't reconstruct it) -- both handled in
`render_scene()` right after the ordinary actor draw.

Two Act 2 images -- Natsuki's "realistic mouth" and her `ghost4` variant --
turned out, on closer inspection, to be genuine ATL *animations* rather
than static composites (confirmed by decompiling their actual `image`
definitions), and stayed out of scope here: this engine bakes one flat PNG
per sprite, with no path to feed an animation into that. `ShowLayer`
(whole-scene zoom/rotate/pan, a *different* mechanism from `tear` bundled
into the same original task) is tracked separately for the same reason --
a materially different rendering problem, not a variant of this one.

## Window hide/show

`OP_WINDOW_HIDE`/`OP_WINDOW_SHOW` (`0x16`/`0x17`) compile DDLC's own
`window hide`/`window hide(...)`/`window show(...)`/`window auto`
statements, all of which mean one of exactly two things here: `hide`
(any transition argument is accepted but ignored -- this engine has no
cross-fade to run one under) suppresses the dialogue box; `show`/`auto`
restores it. `auto` and an explicit `show` collapsing to the same opcode
is a real equivalence for this engine specifically, not a shortcut: Ren'Py's
"automatically show the window when there's dialogue to display" has
nothing to distinguish itself against here, since every real `OP_SAY`
already draws the box unconditionally regardless of this flag.

Real Ren'Py's window hide can expand the scene to fill the space the box
would have used; this engine can't reproduce that without a wider art
pipeline change, since backgrounds are baked at a fixed 320x180 assuming
the box always covers the bottom 60 rows (see "Image assets"). `render_box()`
fills those rows with plain black instead when hidden -- not the identical
effect, but it captures the part that reads as the effect in the moment:
the dialogue disappearing for a clean, textless shot.

## Save data

Each `DSAVEn` is one fixed-layout `save_blob_t` (`src/save.c`), written with a
single `ti_Write`: `pc`, the call stack + `sp`, the story `vars[]`, and the
current scene (background, `actors[]`, speaker, and the *string-pool index*
of the current line -- not a pointer, since `vn_scene_t.text` points into
this run's malloc'd chunk copy (`src/assets.c`'s `assets_load_chunk()`),
which won't land at the same address next launch).

This is a position in the *compiled bytecode*, not an abstract story
checkpoint: loading just overwrites those fields on the live `vn_vm_t` and
lets `vn_step()` carry on from `pc` as if nothing happened, since it always
re-reads `pc` fresh rather than caching it across steps. `pc`/`stack[]` are
already the packed chunk-aware values (see "Chunking"), so a save whose
`pc` names a different chunk than the one currently resident needs no
special handling either -- the next `vn_step()` notices the mismatch and
swaps chunks exactly like any other cross-chunk jump would. That also means
a save only replays correctly against the exact compiled chunks (and engine
build) it was written against -- re-running the import pipeline with a
different `--files` selection changes the bytecode layout and invalidates
old saves.

## Persistent data

Separate from both an ordinary story variable (reset every New Game) and a
`DSAVEn` slot (one resume point the player chose to keep) is Ren'Py's own
`persistent.*` -- progress that survives *both*: how many times the game has
been started, which routes are cleared, whether a one-time surprise has
already happened. DDLC's Act 2/3 pacing and its easter eggs depend on this;
the ghost menu and Monika's poem-game eyes are each meant to happen once
ever, not once per playthrough.

Which variable slots qualify is decided entirely at compile time and needs
no code in `compile_script.py` beyond what string/attribute-name resolution
already does: `tools/import_game.py` filters `compiler.variables` for every
name starting with `"persistent."` and ships that list as `DPSLOT` (`u16`
count, one `u8` slot per entry) -- 22 entries for the full game today.
`src/persist.c` doesn't know or care what any of them mean, only that
they're the ones to carry forward.

`persist_load()` overlays the last saved value for each `DPSLOT` slot onto
`vars[]`, called once per New Game/Continue right after
`assets_apply_var_defaults()` (so a real persisted value wins over a plain
Ren'Py default) and before a possible `save_load()` (so a loaded save's own
`vars[]` — already reflecting whatever was persistent as of that save —
isn't second-guessed). A first-ever run has no `DPERSIST` AppVar yet, so
this is correctly a no-op, leaving the plain defaults in place.

`persist_save()` writes the current value of every `DPSLOT` slot back out
to `DPERSIST` (one `i16` per slot, `DPSLOT` order) -- called in `main.c`
right after `vn_run()` returns, which covers the story finishing normally,
the player quitting mid-story, and returning to the title screen from the
pause menu, in one checkpoint. A player who quits straight from the title
screen without starting anything never reaches that call, correctly: `vm`
hasn't been through `vn_init()` yet on that path, and there is nothing new
to persist anyway.

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
bounce-drops, at close to DDLC's own ~3.45s pace (`render.c`'s `F_*` keyframe
constants, `TITLE_INTRO_MS` in `render.h`) -- an earlier version compressed
this to under a second, which read as the cast popping into place rather than
animating: eased motion needs enough real time on screen to actually show the
curve. Easing uses 32-entry integer LUTs (the eZ80 has no
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
(`ImageResolver.explicit_bg_scene()`) rather than through the title's own art
pipeline -- it needs no title-style positioning or palette, so riding the
existing `DSCNn`/`DPALGAME`/`assets_scene()` path as-is costs zero new C code.
It's baked *first*, before any dialogue is compiled, specifically so its scene
id is always 0 (`src/main.c`'s `SPLASH_LOGO_SCENE`) regardless of which
chapters get compiled in. The poem minigame's notebook background is baked
unconditionally too, right after, but through its own path
(`ImageResolver.poem_background()`, `DPOEMBG`) rather than as a scene -- see
"Poem minigame" for why.

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
first launch), typed directly on the keypad rather than picked from a list:
`run_name_entry()` reads `os_GetCSC()` (`ti/getcsc.h`) and looks each
scancode up in the exact table given in that header's own doc comment, which
is also what the TI-OS's own text-entry routines use -- the same mapping
printed above each key in ALPHA mode (`MATH`=A, `APPS`=B, `PRGM`=C, …), so a
name types the same way it would from the OS's own `Input`/`Prompt`. `Del`
erases, `Enter`/`2nd` confirms once at least one letter's been typed, `Clear`
quits like everywhere else. Every `host_string()` call then substitutes
`[player]` for the saved name before the engine ever sees the text --
`assets_string()`/`vn.c` are unaware substitution
happens at all. `save.c`'s `save_load()` goes through `vm->host->string()`
rather than `assets_string()` directly for the same reason: a loaded line
needs the substitution exactly as much as one reached by playing forward does.

## Debug menu

Entering the classic Konami code -- up up down down left right left right B A
B A -- from the title screen or during play opens a debug menu for testing
engine features directly instead of playing to reach them. Mapped onto this
keypad's real buttons: `2nd` (already this engine's primary confirm/advance
key) stands in for A, and `Alpha` (a real physical key, otherwise unused by
this engine) stands in for B.

`src/main.c`'s `konami_check()` runs on every `input_poll()` call --
i.e. every screen's input loop gets it for free -- as a small rolling state
machine: each new key-down edge either advances a match against the fixed
12-key sequence or resets it (to 1, not 0, if the wrong key happens to be a
valid first key, so mashing Up before starting doesn't burn a real attempt).
A full match sets a global `debug_menu_requested` flag, mirroring
`quit_requested`'s pattern; `wait_for_advance()` (mid-story) and
`run_title_screen()` each check and clear it, opening `run_debug_menu(vm)`.

The title screen has no live `vn_vm_t` yet (`vn_init()` only runs once a
New Game/Continue/Load choice is made -- see "Startup sequence"), so
`run_debug_menu()` takes `vm` as a possibly-NULL pointer: items that need
live story state (the tear/window/deleted-character toggles) are simply
left off the list when `vm` is NULL, rather than touching uninitialized
memory. The poem minigame test and save erasure need no `vm` and are always
available.

Menu items: running the real poem minigame screen standalone (`poem_run()`,
the same code a `call poem` site drives) and showing its winner plus every
character's appeal total; toggling the tear glitch and window-hidden state
on the live scene (see "The tear glitch effect", "Window hide/show");
toggling each character's `persistent.deleted_*` flag (see "Character
presence AppVars" below -- this writes the same slots
`delete_character()`/`restore_all_characters()` do, via `vn.h`'s
`VN_DELETED_VAR(ch)`, and calls `persist_save()` immediately so a toggle
survives past this session exactly like the real mechanic); and erasing all
saves (`save_delete_all()`, gated behind its own confirm screen, defaulted
to "No").

## Startup splashscreen check (the ghost menu)

DDLC's real `game/splash.rpy` runs a `label splashscreen` before the main
menu ever shows, every launch. Decompiling it found it's almost entirely
things this engine's own C-side startup already does differently: Windows
process/anti-cheat probing (`subprocess`/`wmic`, meaningless on-calc), `.chr`
file existence checks (this engine already tracks character deletion its
own way — see "Character presence AppVars" below and `VN_DELETED_VAR`), a
first-run content warning that would show twice if compiled alongside
`main.c`'s own (fixed, now two lines — see "Startup sequence"), an
`after_load`/`autoload`/`autoload_yurikill` anti-cheat and Act 3 corruption-
escalation framework keyed on `persistent.anticheat`/`persistent.yuri_kill`,
and an alternate CG sequence (`s_kill_early`, triggered when
`characters/sayori.chr` is missing at the start of a fresh playthrough) —
all deliberately out of scope here, tracked separately.

One self-contained, still-meaningful piece survives:
the **ghost menu** easter egg. `tools/compile_script.py`'s `_emit_Label`
special-cases `"splashscreen"` the same way it special-cases `"poem"` —
instead of walking the real body, it locates the one `If` node whose
condition mentions `seen_ghost_menu` (a decompiled-source marker, not
hand-typed, so a source-shape change would visibly break the search rather
than silently miscompile) and hand-emits just that: the real condition
(`persistent.playthrough == 2 and not persistent.seen_ghost_menu and
renpy.random.randint(0, 63) == 0`, unmodified — compound and/not/randint
conditions were already supported, see "`If` conditions" below), then a
black screen, a 1-second pause, DDLC's real ending-card image ("end"),
another 3-second pause, and `persistent.seen_ghost_menu`/
`persistent.ghost_menu` both set. `config.main_menu_music`/
`renpy.music.play()` are dropped, same as everywhere else (no CE audio —
see `OP_SOUND`'s doc comment). The label keeps its real name (`splashscreen`)
rather than a synthetic one, so `tools/import_game.py` can find its address
the same way it already finds `label start` — packaged as a new, optional
`DSPLASH` AppVar (packed pc, same format as `DENTRY`; simply absent if
`splash.rpyc` wasn't in this build's `--files` selection, in which case
`assets_splash_pc()` returns false and the check is skipped entirely).
`src/main.c`'s `run_splashscreen_check()` runs it once at launch, right
after the studio logo/content-warning splash (before the title screen), via
a throwaway one-shot `vn_run()` sharing the same host callbacks as real
play — persistent state is loaded before and flushed after, same as any
other checkpoint.

Two non-character images this pulled in that had no existing resolution
path: `show black`/`show end` are bare non-character `Show`s, and
`OP_SHOW`'s `ch:u8` operand is always one of the 4 cast members — a
full-screen standalone image is much closer to what `OP_SCENE` already does,
so the hand-emitted ghost menu compiles both as scene changes instead (see
`Compiler._emit_ghost_menu_check`). "black"/"white" are Ren'Py-builtin
`Solid()` colors no DDLC `.rpyc` ever declares — `image_resolve.py`'s
`build_image_table()` now seeds them as synthetic solid-color `ImageDef`s if
nothing else claims the name first. "end" is a real declared image
(`definitions.rpy`, `gui/end.png`) and already resolved via the existing
ATL-first-string path.

**`persistent.playthrough` is real (a fixed slot, `vn.h`'s
`VN_PLAYTHROUGH_VAR`, reserved the same way `VN_DELETED_VAR` is — see
`Compiler.PLAYTHROUGH_VAR`), and this engine deliberately never writes it —
it always reads 0.** This isn't a gap to close: `script.rpy`'s own `label
start` branches straight on this value to pick which act to jump into
(`0` → `ch0_main`, `1` → `ch10_main`, `2` → `ch20_main`, `3` → straight to
`ch30_main`, the Act 3 finale, `4` → straight to credits). An earlier
version of this engine had `main.c` increment it on every New Game, meant
to make the ghost menu reachable — a real, confirmed bug: it collided with
`label start`'s own dispatch, so every New Game after the first skipped
straight into later, never-tested Act 2/3 content instead of restarting
Act 1 (exactly the symptom a real playtester hit: repeat New Game
eventually reaching "the end"). **The ghost menu (gated on
`playthrough == 2`) is consequently unreachable in this engine today** —
an honest limitation, not a silently-broken feature. Reaching it for real
would need genuine multi-playthrough support (treating `ch10_main`/
`ch20_main`/etc as legitimate alternate entry routes, which in turn need
Act 2/3 content this engine doesn't otherwise render) — a substantially
bigger feature than this section originally assumed.

## Character presence AppVars

`src/chars.c` creates four empty AppVars — `SAYORI`, `NATSUKI`, `YURI`,
`MONIKA` — as the on-calc stand-in for each character's Ren'Py `.chr` file.
They carry no content; only their existence is meaningful. The Act 2/3 "file
deletion" meta effect is implemented as `ti_Delete` on the relevant AppVar,
and `chars_present()` lets the engine ask the filesystem directly rather than
tracking deletion state separately. `chars_init()` only creates AppVars that
are missing, so a deletion from a prior session persists across restarts.

## Poem minigame

The real `label poem:` (`script-poemgame.rpy`) is a custom interactive
screen (`ui.textbutton`/`ui.interact()` in a Python `while True` loop) --
fundamentally not expressible in this VM's linear bytecode model, unlike
most of the compiler's gaps, which are about *unsupported syntax* rather
than *a different execution model entirely*. `compile_script.py`'s
`_emit_Label` special-cases the label by name: instead of walking the real
body, it emits a call into the real (C-side) minigame, `src/poem.c`, via
`OP_MINIGAME` (see the Bytecode table above).

**Chapter-indexed, inlined at the call site.** Real DDLC scores each
chapter's poem game into its own slot -- `poemwinner[chapter]`,
`s_poemappeal[chapter]`/`n_poemappeal[chapter]`/`y_poemappeal[chapter]` --
where `chapter` is whichever act invoked `call poem`. The single shared
`label poem:` has no way to know which chapter is calling it, so instead of
emitting one `OP_MINIGAME` there, `_emit_Call` inlines `OP_MINIGAME`
directly at each *static* `call poem` site, targeting that call's own
`poemwinner[N]`/`s_poemappeal[N]`/`n_poemappeal[N]`/`y_poemappeal[N]` slots.
`N` comes from `Compiler.last_chapter`, tracked by watching for the literal
`chapter = N` assignment DDLC's own script always makes immediately before
every real `call poem` -- confirmed by decompiling `script.rpyc`'s node
sequence, not assumed. `label poem:` itself is kept only as a fallback stub
for the one call site whose chapter can't be proven at compile time (Act
3's finale, in `script-ch30.rpyc`, compiled before `script.rpyc` in file
order so cross-file chapter tracking wouldn't help either) -- it uses
generic, non-indexed scratch slots (`poem_winner`/`poem_s_appeal`/
`poem_n_appeal`/`poem_y_appeal`) instead.

One consequence worth being explicit about: because every resolvable call
site now bypasses `label poem:` entirely, its own body -- which in real
DDLC contains the Monika's-eyes jump-scare check and generic post-game
reaction dialogue -- is unreachable by construction, not just unwalked.
That reaction logic needs to live at each inlined call site instead, the
same relocation problem `splash.rpyc`'s reachable easter eggs have.

**Reproduced faithfully**, from the real Python (`PoemWord` class,
`poemwords.txt`): 20 rounds, 10 words shown per round (2 columns x 5 rows,
matching DDLC's real layout) drawn from the real 228-word bank
(`assets/raw/poemwords.txt`, packaged as `DPOEM`: `u16 count` then per word
`u8 len, word bytes (no NUL), u8 sPoint, u8 nPoint, u8 yPoint`) -- all 10
shown each round are removed from the pool regardless of which one is
picked (200 of 228 consumed over a full game, 28 spare, so there's no need
to shrink the word count to fit). Cumulative `sPoint`/`nPoint`/`yPoint`
totals; winner = highest total, `TAG_TO_CHAR` order (0 sayori, 1 natsuki,
2 yuri) -- matching DDLC's own `persistent.playthrough == 0` branch (max of
the three). This engine has no persistent multi-playthrough state, so it
always takes that branch, never DDLC's `playthrough > 0` one
(natsuki/yuri-only, sayori excluded).

**Not reproduced:** the real body's Monika's-eyes jump-scare and
word-corruption easter egg (gated behind `persistent.playthrough == 2/3`)
and the animated reaction "stickers" / ambient wandering background
characters (cosmetic ATL, same category as the title screen's already-
dropped zoom -- see "The speaking pop"). The playthrough-gated content
isn't a cosmetic simplification like the stickers are: it's structurally
unreachable, the same way the eyes/corruption branches are dead code under
this project's single-playthrough model regardless of whether the compiler
walks the real body at all. Kept: the real `bg/notebook.png` background
(`explicit_bg_scene()`, always scene id 1 -- see "Startup sequence") and
the "N/20" progress counter DDLC also shows.

**`poemwinner[N]`/`*_poemappeal[N]` are real indexed variables, and DDLC's
own script does read them downstream -- just not compilably yet.**
Confirmed by decompiling `script-ch1.rpyc`/`script-ch2.rpyc`: right after
each poem game, DDLC computes `nextscene = poemwinner[N] + "_exclusive_" +
str(eval(poemwinner[N][0] + "_appeal"))` and dynamically calls it -- e.g.
"sayori_exclusive_3" -- to play the winning character's own bonus scene,
picked further by an appeal-score threshold. Two real gaps block this
today, not one:
- `poemwinner[N]` holds real DDLC's winner as a **string** tag name
  (`"sayori"`); this engine's `OP_MINIGAME` stores the numeric `TAG_TO_CHAR`
  id instead (0-3) -- there's no conversion back to the tag string yet.
- `_dynamic_target_var()` only resolves a *bare identifier* holding a
  previously-assigned label name (see "Support dynamic jump/call targets");
  this is a two-piece *constructed* name (a converted winner id, plus an
  appeal-threshold-bucketed suffix), a materially different, more general
  pattern it doesn't attempt.
Left unresolved, `Call expression nextscene` correctly degrades to
`VN_FINISHED` (vn.h's documented fallback for an unresolvable dynamic
target) rather than crashing -- which is *why* every `--files=act1` replay
before this was found had been ending in the middle of Chapter 1 without
any visible error. Tracked as its own task (see the task list): reaching
it needs a winner-id-to-tag-string table, appeal-bucket logic matching
DDLC's real thresholds, and generalizing dynamic-call resolution for a
two-piece constructed name -- plus compiling in the 5
`script-exclusives*.rpyc` files, none of which are in `--files=act1` yet.

**A real, session-wide verification gap, found and fixed alongside this:**
`tools/host_sim`'s `--pc=` was being hand-picked once early in this
project and never revisited. It happened to land inside `script-ch0`'s own
chunk rather than through `label start`'s real dispatch, and stayed
numerically "valid" (a real address, not out of bounds) as more files were
added over many sessions -- so replaying it kept reporting the same
line count and "finished cleanly" without ever actually exercising `label
start`, the poem-minigame chapter dispatch, or anything past `ch0_main`.
The exclusive-route gap above was only found by switching to the real
entry point. `host_sim` now has `--dentry=path` (reads a real
DENTRY/DSPLASH-format packed address instead of a hand-picked number) --
prefer it over a hand-picked `--pc=` for "does the real game play
through" checks going forward.

## Parameterized label calls

Ren'Py labels can declare parameters with defaults (`label showpoem(poem=None,
music=True, track=None, revert_music=True, img=None, where=i11, paper=None):`),
and a `call`/`jump` site can override any of them by position or keyword
(`call showpoem(poem_y1, img="yuri 3t")`). This engine's variables are flat
global slots, not a real per-call stack frame, so a parameter is just an
ordinary variable named after it -- `Compiler.label_params` (populated by
`load_label_params()`, scanned across every file in a build's `--files`
selection *before* any file compiles, since a call site is very often in
a different, earlier-compiled file than the label it targets --
`showpoem` is called from `poemresponses.rpyc` but declared in
`poems.rpyc`) records each parameterized label's real (name, default)
list. `_emit_Call` binds **every** declared parameter on every call to
that label, defaults included, not only the ones a given call overrides --
otherwise a parameter this call leaves at its default would read whatever
a *previous*, different call happened to leave behind, since nothing else
resets it between calls.

Only a literal (or Ren'Py's `None`, mapped to `0` -- every VM variable
already starts there) binds; a name reference (`where=i11`, a transform
name, not a constant) is left unresolved and skipped, same
degrade-gracefully convention as everywhere else in this compiler. A
label's own body still can't reconstruct a full **object** passed as an
argument -- `showpoem`'s `poem` parameter is really one of DDLC's own
hand-written `Poem(author=, title=, text=)` instances, not a scalar, and
displaying its actual title/text is real content work, tracked separately
(see the task list) -- but the parameter-binding mechanism itself is
general and already covers every other parameterized call found so far.

## `If` conditions: compound `and`/`or`/`not` are supported; `Menu` item conditions are not

`compile_script.py`'s `_emit_If` originally only understood a single bare
comparison (`IDENT <cmp> CONST`) per branch; anything else -- any `and`/`or`,
any bare flag check like `if s_readpoem:` -- degraded to "take this branch
unconditionally, later entries become unreachable". Real DDLC condition
strings are compound almost everywhere routes/affection/chapter state is
checked (`poemsread < 3 or (persistent.playthrough == 0 and poemsread < 4)`,
`not y_readpoem and not y_ranaway`, ...), so this wasn't a rare edge case --
this specific compound condition is `script-poemresponses.rpyc`'s own
poem-sharing loop's *exit* check, and degrading it to "unconditionally take
the loop-again branch" produced a real infinite loop in compiled bytecode,
not just a wrong-content bug: replayed through the host simulator with a
naive "always pick option 0" auto-player, the poem-sharing sequence never
terminated, hitting the simulator's line-count safety cap instead of a clean
finish.

`_emit_If`/`_emit_condition` now recursively compile any `and`/`or`/`not`
tree of comparisons (or bare flags, treated as `!= 0`) into short-circuit
branching bytecode -- `and`'s left side jumping to false on failure, `or`'s
left side jumping to true on success, `not` swapping its operand's true/false
targets -- entirely with the existing `OP_IF`/`OP_JUMP` opcodes (negating a
comparator, e.g. NOT(EQ) = NE, gets the "jump if false" edge `OP_IF` doesn't
have natively). No bytecode format change. Verified against the real
poem-sharing loop's exit condition directly (`poemsread`/`playthrough` swept
across both branches, matched real Python semantics exactly) and end to end:
a full `--files=all` replay driven by a naive "always pick option 0"
auto-player now reaches a real `OP_END` finish (2098 real lines, 16 real
menu picks) instead of the previous infinite loop.

**Still open:** `renpy.ast.Menu.items` is a list of `(caption, condition,
block)`; `_emit_Menu` reads `caption` and `block` but still ignores
`condition` -- every item is always offered, regardless of story state. The
poem-sharing menu itself still shows all four girls every time (not narrowed
as each is visited), it just no longer *loops forever* doing so, since the
loop's own exit condition (an `If`, not the `Menu` itself) now evaluates
correctly. Fixing the `Menu` side needs per-item conditions evaluated at
display time (story state can change between visits to the same menu), which
the current `OP_MENU n:u8 [text:u16 tgt:u24]*` encoding has no room for --
unlike the `If` fix above, this one is a bytecode format change, not a purely
compiler-side one, so it's deferred.
