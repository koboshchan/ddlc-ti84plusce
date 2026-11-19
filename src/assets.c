/**
 * @file assets.c
 * @brief AppVar loader. See assets.h and docs/FORMAT.md's "Chunking" and
 * "Image assets" sections for the on-disk formats this reads.
 *
 * Every AppVar is read with the same pattern: open, ti_Read() its bytes into
 * a malloc'd buffer this module owns, close. No pointer from ti_GetDataPtr()
 * is ever held past the ti_Open/ti_Close that produced it.
 *
 * An earlier version tried to be clever: DSCRIPT/DSPRLUT/DSCNLUT/DPALGAME
 * were opened once in assets_init() and their ti_GetDataPtr() pointers were
 * cached for the rest of the program, on the theory that keeping the handle
 * open kept the pointer valid. It doesn't: fileioc.h's own docs warn
 * ti_GetDataPtr's pointer "can easily be invalidated" by creating/deleting/
 * resizing *any* variable, and opening an archived DSPRn/DSCNn later in the
 * same session -- which every sprite/scene draw does -- counts. Observed on
 * real hardware/CEmu as a sprite that rendered correctly a few times, then
 * appeared "stuck" on one expression for the rest of the run: the cached
 * DSPRLUT pointer had gone stale, so every later lookup kept re-reading
 * whatever bytes happened to still be at that old address instead of the
 * real table. Copying each of these four into a private buffer up front
 * costs a few KB of RAM (script+ch0 is ~13KB total) and makes the loaded
 * data immune to whatever the OS does with memory afterward.
 */

#include "assets.h"
#include "cgpack.h"
#include "pak.h"
#include "render.h"
#include "fast_ops.h"
#include "text.h"

#include <compression.h>
#include <fileioc.h>

#include <stdio.h>
#include <stdlib.h>
#include "perf_memory.h"
#include <string.h>

/* A single resident chunk's string pool, generously capped -- the largest
 * single compiled chunk seen so far (script-poemresponses) uses 1257. Table
 * of pointers, not copies: each points directly into script_buf (this
 * module's private copy of the current chunk's bytes). */
#define MAX_STRINGS 512

static uint8_t         *script_buf; /* owns the current chunk's bytes */
static const uint8_t   *script_code;
static size_t            script_code_size;
static const char       *string_ptrs[MAX_STRINGS];
static uint16_t          string_count;

/* The interned variable-string pool (DVSTR). Small and fixed for the
 * whole game -- ~37 strings, under a kilobyte -- so unlike the per-chunk
 * dialogue pool above it is loaded once and never swapped: a name
 * assigned in one chunk has to still resolve several chunks later. */
#define MAX_VAR_STRINGS 64
static uint8_t          *var_string_buf;
static const char       *var_string_ptrs[MAX_VAR_STRINGS];
static uint16_t          var_string_count;

/* Ren'Py `default` values (DVDEF): u16 count, then per entry u8 slot + i16
 * value -- see tools/import_game.py's packaging and assets_apply_var_defaults()
 * below. */
static uint8_t           *var_defaults_buf;
static uint16_t           var_defaults_size;

/* Dynamic jump/call label table (DVLBL): u16 count, then per entry
 * u16 (interned id - VN_STR_BASE) + u32 packed (chunk_id<<16 | offset) --
 * see assets_resolve_label() and OP_JUMP_VAR/OP_CALL_VAR in vn.h. */
static uint8_t           *var_labels_buf;
static uint16_t           var_labels_size;

/* Debug menu's "jump to chapter" table (DCHJMP): u16 count, then per entry
 * u8 name_len + name bytes (no NUL) + u32 packed (chunk_id<<16 | offset) --
 * see tools/import_game.py's packaging and assets_debug_chapter_pc() below.
 * Raw bytes, linear-scanned by index rather than unpacked into a table,
 * same reasoning as var_labels_buf: this is a handful of entries, walked
 * only while the debug menu's chapter submenu is actually open. */
static uint8_t           *chapter_jump_buf;
static uint16_t           chapter_jump_size;

static uint32_t          entry_pc; /* packed (chunk_id<<16)|offset, from DENTRY */

/* Sprite/scene lookup tables: tools/import_game.py's build_lut() format --
 * u16 count, then per entry u8 appvar_index, u16 offset, u16 length. Each is
 * this module's own small private copy (DSPRLUT/DSCNLUT are at most a few
 * hundred entries), not a pointer into the AppVar. */
static uint8_t   *sprite_lut_buf;
static const uint8_t *sprite_lut;
static uint16_t   sprite_lut_count;
static uint8_t   *scene_lut_buf;
static const uint8_t *scene_lut;
static uint16_t   scene_lut_count;

/* Per-sprite (i16 dx, i16 dy) draw-offset table, DSPRLUT order -- see
 * tools/import_game.py's DSPROFF packaging and assets_draw_sprite(). Also
 * this module's own private copy; optional, same reasoning as the title/CG
 * tables below (a bundle predating layered sprites ships none). */
static uint8_t   *sprite_off_buf;
static uint16_t   sprite_off_count;

/* Title screen: its own art set, layout table, and palette (see
 * tools/import_game.py's TITLE_ART and docs/FORMAT.md). All optional -- a
 * bundle without them still boots straight into a playable game, so a
 * missing AppVar here leaves the ids unresolvable rather than failing
 * assets_init(). */
static uint8_t   *title_lut_buf;
static const uint8_t *title_lut;
static uint16_t   title_lut_count;
static uint8_t   *title_pos_buf;      /* i16 x, y, dx, dy per title art id */
static uint16_t   title_pos_count;
static uint16_t   title_palette[256]; /* swapped into gfx_palette on the title */
static uint16_t   game_palette[256];  /* kept so it can be swapped back */
static bool       title_palette_ok;

/* Per-CG palettes: DCGIDX maps a scene id to an index into DCGPLUT (or 0xFF
 * for a scene that renders under the shared game palette). Both optional --
 * same reasoning as the title assets above. */
static uint8_t   *cg_index_buf;
static uint16_t   cg_index_count;
static uint8_t   *scene_full_buf;   /* optional DSCNH: 1 byte per scene (1 = 240px) */
static uint16_t   scene_full_count;
static uint8_t   *cgpal_lut_buf;
static const uint8_t *cgpal_lut;
static uint16_t   cgpal_lut_count;
static uint16_t   cg_palette_scratch[256]; /* assets_scene_palette()'s return buffer */

/* DCGVER: this build's full-res CG pack fingerprint (tools/export_cgpack.py's
 * build_fingerprint(), matched against a pack's own /DDLC/BUILD.ID by
 * src/cgpack.c). Optional, same reasoning as the CG palette tables above --
 * a bundle with no CGs baked ships neither. */
static uint8_t    cgpack_fingerprint[4];
static bool       cgpack_fingerprint_ok;

static uint16_t read_u16le(const uint8_t *p)
{
    return (uint16_t)(p[0] | (uint16_t)(p[1] << 8));
}

/** Reads @p name's whole contents into a freshly malloc'd buffer the caller
 * owns, closing the handle before returning. NULL if @p name can't be
 * opened or the allocation fails. */
static uint8_t *read_whole(const char *name, uint16_t *size_out)
{
    return pak_read_whole(name, size_out);
}

bool assets_load_chunk(uint8_t chunk_id)
{
    /* Only one chunk is ever resident (see docs/FORMAT.md's "Chunking") --
     * the previous one's buffer has to go before loading the next, or a long
     * session that crosses many chunk boundaries would leak one chunk's
     * worth of RAM per crossing. First call has nothing to free yet. */
    free(script_buf);
    script_buf = NULL;
    script_code = NULL;
    script_code_size = 0;
    string_count = 0;

    /* Same reasoning, one step further: cached sprite data is worth redraw
     * savings, and the chunk about to be read is worth continuing to run.
     * Drop the sprite cache here so script_buf settles at low heap below it. */
    assets_sprite_cache_clear();
    assets_zoom_cache_clear();

    char name[9];
    sprintf(name, "DSCR%u", chunk_id);

    /* Chunks ship zx0-compressed now (tools/import_game.py's
     * zx0_compress()): a 2-byte uncompressed-size header (unlike a
     * background's fixed SCENE_BYTES, a chunk's real size varies, so it
     * has to travel with the data) followed by the compressed stream.
     * Safe the same way assets_scene() already is -- the header gives an
     * exact malloc size before zx0_Decompress ever writes a byte, so
     * there's no risk of it overrunning a too-small destination. This
     * only runs once per chunk *swap* (not per frame, unlike
     * assets_scene()'s per-redraw decompress), so the decode cost here is
     * negligible next to what it buys back in archive space. */
    pak_view_t v;
    if (pak_view_open(name, &v)) {
        if (v.size < 4) {
            pak_view_close(&v);
            return false;
        }
        uint16_t total = read_u16le(v.ptr);
        script_buf = malloc(total);
        if (!script_buf) {
            pak_view_close(&v);
            return false;
        }
        zx0_Decompress(script_buf, v.ptr + 2);
        pak_view_close(&v);
    } else {
        uint8_t vol, flags;
        uint16_t off;
        size_t packed_len;
        if (!pak_stat(name, &vol, &off, &packed_len, &flags) ||
            packed_len < 4 || packed_len > 0xFFFFu) {
            return false;
        }
        uint8_t *tmp = malloc(packed_len);
        if (!tmp) {
            return false;
        }
        if (!pak_read_span(vol, off, 0, tmp, packed_len)) {
            free(tmp);
            return false;
        }
        uint16_t total = read_u16le(tmp);
        script_buf = malloc(total);
        if (!script_buf) {
            free(tmp);
            return false;
        }
        zx0_Decompress(script_buf, tmp + 2);
        free(tmp);
    }

    uint16_t code_len = read_u16le(script_buf);
    script_code = script_buf + 2;
    script_code_size = code_len;

    const uint8_t *p = script_buf + 2 + code_len;
    string_count = read_u16le(p);
    p += 2;

    if (string_count > MAX_STRINGS) {
        free(script_buf);
        script_buf = NULL;
        script_code = NULL;
        script_code_size = 0;
        string_count = 0;
        return false;
    }

    for (uint16_t i = 0; i < string_count; i++) {
        uint16_t len = read_u16le(p);
        p += 2;
        string_ptrs[i] = (const char *)p;
        p += (size_t)len + 1; /* +1: trailing NUL, see vnasm.py's to_chunk_bytes */
    }
    return true;
}

static bool load_lut(const char *name, uint8_t **buf_out,
                     const uint8_t **lut_out, uint16_t *count_out)
{
    uint16_t total;
    uint8_t *buf = read_whole(name, &total);
    if (!buf) {
        return false;
    }
    *buf_out = buf;
    *count_out = read_u16le(buf);
    *lut_out = buf + 2;
    return true;
}

assets_status_t assets_init(void)
{
    pak_init();

    uint16_t entry_size;
    uint8_t *entry = read_whole("DENTRY", &entry_size);
    if (!entry || entry_size < 4) {
        free(entry);
        return ASSETS_ERR_ENTRY;
    }
    entry_pc = (uint32_t)entry[0] | ((uint32_t)entry[1] << 8) |
              ((uint32_t)entry[2] << 16) | ((uint32_t)entry[3] << 24);
    free(entry);

    if (!assets_load_chunk((uint8_t)(entry_pc >> 16))) {
        return ASSETS_ERR_SCRIPT_OPEN;
    }
    if (!load_lut("DSPRLUT", &sprite_lut_buf, &sprite_lut, &sprite_lut_count)) {
        return ASSETS_ERR_SPRITE_LUT;
    }

    /* Optional, like the tables below: a bundle whose script never assigned
     * a string to a variable ships no DVSTR, and assets_var_string() simply
     * finds nothing to resolve. Loaded once and held for the whole run --
     * see the pool's declaration for why it isn't per-chunk. */
    uint16_t var_str_size;
    var_string_buf = read_whole("DVSTR", &var_str_size);
    if (var_string_buf && var_str_size >= 2) {
        var_string_count = read_u16le(var_string_buf);
        if (var_string_count > MAX_VAR_STRINGS) {
            var_string_count = MAX_VAR_STRINGS;
        }
        const uint8_t *vp = var_string_buf + 2;
        for (uint16_t i = 0; i < var_string_count; i++) {
            uint16_t len = read_u16le(vp);
            vp += 2;
            var_string_ptrs[i] = (const char *)vp;
            vp += (size_t)len + 1;   /* +1: trailing NUL, as the chunk pool */
        }
    } else {
        var_string_count = 0;
    }

    /* Optional, same as DVSTR above: a bundle built before this existed
     * ships no DVDEF, and assets_apply_var_defaults() just has nothing to
     * apply -- every variable starts at plain 0 from vn_init()'s memset,
     * same as always. Held as the raw AppVar bytes rather than unpacked
     * into a table, since it's only ever walked once per New Game/Continue,
     * not per frame -- no lookup structure earns its cost. */
    var_defaults_buf = read_whole("DVDEF", &var_defaults_size);

    /* Optional, same reasoning again: a bundle whose script has no dynamic
     * jump/call ships no DVLBL, and assets_resolve_label() has nothing to
     * match -- every OP_JUMP_VAR/OP_CALL_VAR then falls through to
     * VN_FINISHED, same as a real lookup miss. Kept as raw bytes and
     * linear-scanned rather than unpacked into a sorted/hashed structure:
     * this resolves at most a few times per session (a dynamic jump only
     * runs when the story actually takes one), not per frame, so build-time
     * simplicity wins over a lookup structure that would only ever save
     * scanning a few hundred bytes. */
    var_labels_buf = read_whole("DVLBL", &var_labels_size);

    /* Optional, same reasoning: a bundle built before the debug menu's
     * chapter-jump feature existed just ships no DCHJMP, and the submenu
     * has nothing to list. */
    chapter_jump_buf = read_whole("DCHJMP", &chapter_jump_size);

    /* Optional: a bundle built before layered sprites existed ships no
     * DSPROFF, and every sprite just draws with (0, 0) offset -- see
     * assets_draw_sprite(). */
    uint16_t off_size;
    sprite_off_buf = read_whole("DSPROFF", &off_size);
    sprite_off_count = sprite_off_buf ? (uint16_t)(off_size / 4) : 0;

    if (!load_lut("DSCNLUT", &scene_lut_buf, &scene_lut, &scene_lut_count)) {
        return ASSETS_ERR_SCENE_LUT;
    }

    uint16_t pal_size;
    uint8_t *pal = read_whole("DPALGAME", &pal_size);
    if (!pal) {
        return ASSETS_ERR_PALETTE;
    }
    render_apply_palette((const uint16_t *)pal);
    memcpy(game_palette, pal, sizeof(game_palette));
    free(pal);

    /* Title screen assets are optional -- see the declarations above. */
    load_lut("DTILLUT", &title_lut_buf, &title_lut, &title_lut_count);

    uint16_t pos_size;
    title_pos_buf = read_whole("DTILPOS", &pos_size);
    title_pos_count = title_pos_buf ? (uint16_t)(pos_size / 8) : 0;

    uint16_t tpal_size;
    uint8_t *tpal = read_whole("DPALTTL", &tpal_size);
    if (tpal) {
        if (tpal_size >= sizeof(title_palette)) {
            memcpy(title_palette, tpal, sizeof(title_palette));
            title_palette_ok = true;
        }
        free(tpal);
    }

    /* Per-CG palettes are optional too -- a bundle with no CGs baked ships
     * neither AppVar, and every scene just falls back to the game palette. */
    cg_index_buf = read_whole("DCGIDX", &cg_index_count);
    scene_full_buf = read_whole("DSCNH", &scene_full_count);
    load_lut("DCGPLUT", &cgpal_lut_buf, &cgpal_lut, &cgpal_lut_count);

    /* Optional, same reasoning: a bundle with no CGs baked ships no DCGVER
     * either, and src/cgpack.c's fingerprint check simply never matches --
     * an external pack, if one happens to be plugged in, just goes unused. */
    uint16_t ver_size;
    uint8_t *ver = read_whole("DCGVER", &ver_size);
    if (ver && ver_size >= sizeof(cgpack_fingerprint)) {
        memcpy(cgpack_fingerprint, ver, sizeof(cgpack_fingerprint));
        cgpack_fingerprint_ok = true;
    }
    free(ver);

    uint8_t font_h = ti_Open("DFONTS", "r");
    if (!font_h) {
        return ASSETS_ERR_FONTS;
    }
    ti_Close(font_h);

    return ASSETS_OK;
}

bool assets_cgpack_fingerprint(uint8_t out[4])
{
    if (!cgpack_fingerprint_ok) {
        return false;
    }
    memcpy(out, cgpack_fingerprint, sizeof(cgpack_fingerprint));
    return true;
}

void assets_use_title_palette(bool on)
{
    if (on && !title_palette_ok) {
        return; /* no title palette shipped -- leave the game one in place */
    }
    render_apply_palette(on ? title_palette : game_palette);
}

const char *assets_status_str(assets_status_t status)
{
    switch (status) {
        case ASSETS_OK:                    return "ok";
        case ASSETS_ERR_ENTRY:             return "DENTRY missing/unreadable";
        case ASSETS_ERR_SCRIPT_OPEN:       return "entry chunk's DSCRn missing/unreadable";
        case ASSETS_ERR_SPRITE_LUT:        return "DSPRLUT missing/unreadable";
        case ASSETS_ERR_SCENE_LUT:         return "DSCNLUT missing/unreadable";
        case ASSETS_ERR_PALETTE:           return "DPALGAME missing/unreadable";
        case ASSETS_ERR_FONTS:             return "DFONTS missing/unreadable";
        default:                           return "unknown";
    }
}

uint32_t assets_entry_pc(void)
{
    return entry_pc;
}

/* Optional, same reasoning as DVSTR/DVDEF/DVLBL above: a bundle built
 * without splash.rpyc in its --files selection (or an older bundle from
 * before this existed) ships no DSPLASH, and the startup splashscreen check
 * (the ghost menu today -- see main.c's run_splashscreen_check()) simply
 * never runs. Read on demand rather than held at startup like DENTRY: this
 * is looked up exactly once per launch, not per frame. */
bool assets_splash_pc(uint32_t *out)
{
    uint16_t size;
    uint8_t *buf = read_whole("DSPLASH", &size);
    if (!buf || size < 4) {
        free(buf);
        return false;
    }
    *out = (uint32_t)buf[0] | ((uint32_t)buf[1] << 8) |
           ((uint32_t)buf[2] << 16) | ((uint32_t)buf[3] << 24);
    free(buf);
    return true;
}

const uint8_t *assets_script(size_t *size_out)
{
    if (size_out) {
        *size_out = script_code_size;
    }
    return script_code;
}

const char *assets_string(uint16_t index)
{
    return index < string_count ? string_ptrs[index] : "";
}

const char *assets_var_string(int16_t value)
{
    if (value < VN_STR_BASE) {
        return NULL;          /* a number, not an interned string */
    }
    uint16_t index = (uint16_t)(value - VN_STR_BASE);
    return index < var_string_count ? var_string_ptrs[index] : "";
}

void assets_apply_var_defaults(vn_vm_t *vm)
{
    if (!var_defaults_buf || var_defaults_size < 2) {
        return;
    }
    uint16_t count = read_u16le(var_defaults_buf);
    const uint8_t *p = var_defaults_buf + 2;
    /* 3 bytes/entry (u8 slot, i16 value); trust the AppVar's own declared
     * count but never read past what actually arrived -- a truncated
     * transfer degrades to "fewer defaults applied", not an out-of-bounds
     * read. */
    uint16_t max_count = (uint16_t)((var_defaults_size - 2) / 3);
    if (count > max_count) {
        count = max_count;
    }
    /* No `slot < VN_MAX_VARS` guard: slot is a uint8_t and VN_MAX_VARS is
     * 256, the full range that type can hold -- see vn.c's _Static_assert,
     * the same invariant src/persist.c also leans on. */
    for (uint16_t i = 0; i < count; i++) {
        uint8_t slot  = p[0];
        int16_t value = (int16_t)read_u16le(p + 1);
        p += 3;
        vm->vars[slot] = value;
    }
}

bool assets_resolve_label(void *ctx, int16_t str_id, uint32_t *addr_out)
{
    (void)ctx;   /* vn_host_t.ctx is main.c's vn_vm_t*, unused here --
                  * every AppVar this needs is this module's own global
                  * state, not per-vm. */
    if (!var_labels_buf || var_labels_size < 2 || str_id < VN_STR_BASE) {
        return false;
    }
    uint16_t want  = (uint16_t)(str_id - VN_STR_BASE);
    uint16_t count = read_u16le(var_labels_buf);        /* the buffer's own
                                                          * leading count, not
                                                          * a guess from size */
    const uint8_t *p = var_labels_buf + 2;               /* past that count */
    for (uint16_t i = 0; i < count; i++) {
        uint16_t id = read_u16le(p);
        if (id == want) {
            *addr_out = read_u16le(p + 2) | ((uint32_t)read_u16le(p + 4) << 16);
            return true;
        }
        p += 6;
    }
    return false;
}

/* Walks past entry @p idx's name to its 4-byte packed pc, or NULL if @p idx
 * is out of range. Shared by all three accessors below so the "u8 len, name
 * bytes, u32 packed" layout is only spelled out once. */
static const uint8_t *chapter_entry_pc_ptr(uint8_t idx)
{
    if (!chapter_jump_buf || chapter_jump_size < 2) {
        return NULL;
    }
    uint16_t count = read_u16le(chapter_jump_buf);
    if (idx >= count) {
        return NULL;
    }
    const uint8_t *p = chapter_jump_buf + 2;
    for (uint8_t i = 0; i < idx; i++) {
        p += 1 + *p + 4;   /* len byte + name bytes + packed pc */
    }
    return p + 1 + *p;    /* past this entry's own len byte + name bytes */
}

uint8_t assets_debug_chapter_count(void)
{
    if (!chapter_jump_buf || chapter_jump_size < 2) {
        return 0;
    }
    uint16_t count = read_u16le(chapter_jump_buf);
    return count > 0xFF ? 0xFF : (uint8_t)count;
}

void assets_debug_chapter_name(uint8_t idx, char *out, size_t out_size)
{
    out[0] = '\0';
    if (!chapter_jump_buf || chapter_jump_size < 2 || out_size == 0) {
        return;
    }
    uint16_t count = read_u16le(chapter_jump_buf);
    if (idx >= count) {
        return;
    }
    const uint8_t *p = chapter_jump_buf + 2;
    for (uint8_t i = 0; i < idx; i++) {
        p += 1 + *p + 4;
    }
    uint8_t len = *p++;
    if (len >= out_size) {
        len = (uint8_t)(out_size - 1);
    }
    memcpy(out, p, len);
    out[len] = '\0';
}

uint32_t assets_debug_chapter_pc(uint8_t idx)
{
    const uint8_t *p = chapter_entry_pc_ptr(idx);
    if (!p) {
        return 0;
    }
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
          ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* LUT entry layout: u8 appvar_index, u16 offset, u16 length (5 bytes). @p id
 * is u16 (not every LUT this indexes has more than 255 entries, but the
 * sprite one does -- 406 for the full game -- so the shared helper takes the
 * wider type for all callers rather than having two near-identical copies). */
static bool lut_lookup(const uint8_t *lut, uint16_t count, uint16_t id,
                       uint8_t *appvar_out, uint16_t *offset_out, uint16_t *len_out)
{
    if (id >= count) {
        return false;
    }
    const uint8_t *e = lut + (size_t)id * 5;
    *appvar_out = e[0];
    *offset_out = read_u16le(e + 1);
    if (len_out) {
        *len_out = read_u16le(e + 3);
    }
    return true;
}

/* Shared by assets_draw_sprite() and assets_draw_sprite_zoomed(): resolves
 * @p id to its LUT entry plus its DSPROFF draw offset (0,0 if DSPROFF is
 * missing or doesn't cover this id -- see the DSPROFF load in
 * assets_init()). */
static bool sprite_lookup(uint16_t id, uint8_t *appvar_out, uint16_t *offset_out,
                          uint16_t *len_out, int16_t *dx_out, int16_t *dy_out)
{
    if (!lut_lookup(sprite_lut, sprite_lut_count, id, appvar_out, offset_out, len_out)) {
        return false;
    }
    *dx_out = 0;
    *dy_out = 0;
    if (id < sprite_off_count) {
        const uint8_t *e = sprite_off_buf + (size_t)id * 4;
        *dx_out = (int16_t)read_u16le(e);
        *dy_out = (int16_t)read_u16le(e + 2);
    }
    return true;
}

/* Sprites ship ZX0-compressed per atom ([u16 raw_size][zx0 bitstream]) and
 * decode on cache miss into a single contiguous LRU RAM arena instead of
 * per-draw malloc/free pairs. Repeated frames during hop/sink/zoom/slide
 * animations hit RAM in O(1) with zero flash opens, zero decompression, and
 * zero heap fragmentation. */
#ifdef __TICE__
#define SPRITE_CACHE_ARENA_CAP 36864u
#define SPRITE_CACHE_SLOTS     12u
#else
#define SPRITE_CACHE_ARENA_CAP 16384u
#define SPRITE_CACHE_SLOTS     8u
#endif

typedef struct {
    uint16_t id;
    uint16_t offset;
    uint16_t size;
    int16_t  dx;
    int16_t  dy;
    uint32_t last_use;
} sprite_cache_slot_t;

static uint8_t            *sprite_arena;
static uint16_t            sprite_arena_cap;
static uint16_t            sprite_arena_used;
static sprite_cache_slot_t sprite_cache[SPRITE_CACHE_SLOTS];
static uint8_t             sprite_cache_count;
static uint32_t            sprite_lru_tick;
static bool                sprite_cache_oversize;

static uint8_t sprite_handle;
static uint8_t sprite_handle_idx;

void assets_sprite_end(void)
{
    if (sprite_handle) {
        ti_Close(sprite_handle);
        sprite_handle = 0;
    }
}

void assets_sprite_cache_clear(void)
{
    assets_sprite_end();
    free(sprite_arena);
    sprite_arena = NULL;
    sprite_arena_cap = 0;
    sprite_arena_used = 0;
    sprite_cache_count = 0;
    sprite_cache_oversize = false;
}

static bool sprite_cache_evict_lru(void)
{
    if (sprite_cache_count == 0) {
        return false;
    }
    uint8_t victim = 0;
    for (uint8_t i = 1; i < sprite_cache_count; i++) {
        if (sprite_cache[i].last_use < sprite_cache[victim].last_use) {
            victim = i;
        }
    }
    uint16_t rm_off = sprite_cache[victim].offset;
    uint16_t rm_sz = sprite_cache[victim].size;
    uint16_t tail = sprite_arena_used - (uint16_t)(rm_off + rm_sz);
    if (tail > 0) {
        memmove(sprite_arena + rm_off, sprite_arena + rm_off + rm_sz, tail);
    }
    sprite_arena_used -= rm_sz;
    for (uint8_t i = 0; i < sprite_cache_count; i++) {
        if (sprite_cache[i].offset > rm_off) {
            sprite_cache[i].offset -= rm_sz;
        }
    }
    if (victim + 1 < sprite_cache_count) {
        memmove(&sprite_cache[victim], &sprite_cache[victim + 1],
                (size_t)(sprite_cache_count - victim - 1) * sizeof(sprite_cache[0]));
    }
    sprite_cache_count--;
    return true;
}

static bool sprite_ensure_arena(uint16_t need)
{
    if (need > SPRITE_CACHE_ARENA_CAP) {
        assets_sprite_cache_clear();
        sprite_arena = malloc(need);
        if (!sprite_arena) {
            return false;
        }
        sprite_arena_cap = need;
        sprite_cache_oversize = true;
        return true;
    }
    if (sprite_cache_oversize || (sprite_arena && sprite_arena_cap < need)) {
        assets_sprite_cache_clear();
    }
    if (!sprite_arena) {
#ifdef __TICE__
        static const uint16_t caps[] = { SPRITE_CACHE_ARENA_CAP, 34816u, 32768u, 30720u, 28672u, 24576u, 20480u, 16384u, 12288u, 8192u };
#else
        static const uint16_t caps[] = { SPRITE_CACHE_ARENA_CAP, 12288u, 8192u };
#endif
        for (size_t i = 0; i < sizeof(caps) / sizeof(caps[0]); i++) {
            if (caps[i] < need) {
                continue;
            }
            sprite_arena = malloc(caps[i]);
            if (sprite_arena) {
                sprite_arena_cap = caps[i];
                break;
            }
        }
        if (!sprite_arena) {
            sprite_arena = malloc(need);
            if (!sprite_arena) {
                return false;
            }
            sprite_arena_cap = need;
        }
    }
    while (sprite_cache_count >= SPRITE_CACHE_SLOTS ||
           (uint16_t)(sprite_arena_cap - sprite_arena_used) < need) {
        if (!sprite_cache_evict_lru()) {
            return false;
        }
    }
    return true;
}

static bool sprite_open_rlet(uint16_t id, const gfx_rletsprite_t **rle_out,
                             int16_t *dx_out, int16_t *dy_out)
{
    for (uint8_t i = 0; i < sprite_cache_count; i++) {
        if (sprite_cache[i].id == id) {
            sprite_cache[i].last_use = ++sprite_lru_tick;
            *dx_out = sprite_cache[i].dx;
            *dy_out = sprite_cache[i].dy;
            *rle_out = (const gfx_rletsprite_t *)(sprite_arena + sprite_cache[i].offset);
            return true;
        }
    }

    uint8_t appvar_idx;
    uint16_t offset;
    uint16_t entry_len = 0;
    if (!sprite_lookup(id, &appvar_idx, &offset, &entry_len, dx_out, dy_out)) {
        return false;
    }
    if (entry_len < 3) {
        return false;
    }

    if (sprite_handle && sprite_handle_idx != appvar_idx) {
        assets_sprite_end();
    }
    if (!sprite_handle) {
        sprite_handle = pak_open_vol("DSPR", appvar_idx);
        if (!sprite_handle) {
            return false;
        }
        sprite_handle_idx = appvar_idx;
    }

    uint32_t vol_sz = ti_GetSize(sprite_handle);
    if ((uint32_t)offset + entry_len <= vol_sz) {
        const uint8_t *compressed = (const uint8_t *)ti_GetDataPtr(sprite_handle) + offset;
        uint16_t raw_size = read_u16le(compressed);
        if (raw_size < 2) {
            return false;
        }
        if (!sprite_ensure_arena(raw_size)) {
            return false;
        }
        uint16_t dst_off = sprite_arena_used;
        zx0_Decompress(sprite_arena + dst_off, compressed + 2);
        sprite_arena_used += raw_size;

        sprite_cache_slot_t *slot = &sprite_cache[sprite_cache_count++];
        slot->id = id;
        slot->offset = dst_off;
        slot->size = raw_size;
        slot->dx = *dx_out;
        slot->dy = *dy_out;
        slot->last_use = ++sprite_lru_tick;

        *rle_out = (const gfx_rletsprite_t *)(sprite_arena + dst_off);
        return true;
    }

    /* Spanning fallback when a packed sprite crosses a DPK volume boundary. */
    assets_sprite_end();
    uint8_t *tmp = malloc(entry_len);
    if (!tmp) {
        return false;
    }
    if (!pak_read_span(appvar_idx, offset, 0, tmp, entry_len)) {
        free(tmp);
        return false;
    }
    uint16_t raw_size = read_u16le(tmp);
    if (raw_size < 2 || !sprite_ensure_arena(raw_size)) {
        free(tmp);
        return false;
    }
    uint16_t dst_off = sprite_arena_used;
    zx0_Decompress(sprite_arena + dst_off, tmp + 2);
    free(tmp);
    sprite_arena_used += raw_size;

    sprite_cache_slot_t *slot = &sprite_cache[sprite_cache_count++];
    slot->id = id;
    slot->offset = dst_off;
    slot->size = raw_size;
    slot->dx = *dx_out;
    slot->dy = *dy_out;
    slot->last_use = ++sprite_lru_tick;

    *rle_out = (const gfx_rletsprite_t *)(sprite_arena + dst_off);
    return true;
}

bool assets_draw_sprite(uint16_t id, int center_x, int feet_y)
{
    const gfx_rletsprite_t *sprite;
    int16_t dx, dy;
    if (!sprite_open_rlet(id, &sprite, &dx, &dy)) {
        return false;
    }

    gfx_RLETSprite(sprite, center_x - sprite->width / 2 + dx, feet_y - sprite->height + dy);
    return true;
}

bool assets_sprite_bounds(uint16_t id, int *w, int *h, int *dx, int *dy)
{
    const gfx_rletsprite_t *sprite;
    int16_t sdx, sdy;
    if (!sprite_open_rlet(id, &sprite, &sdx, &sdy)) {
        return false;
    }

    *w = sprite->width;
    *h = sprite->height;
    *dx = sdx;
    *dy = sdy;
    return true;
}

/* --- the scaled-sprite cache --------------------------------------------
 *
 * Everything below the resample exists because the scale is *constant*.
 * DDLC's speaking zoom is one fixed 1.05x, and this engine animates a
 * character's position (hop, sink, the eased fallback rise) but never the
 * zoom itself -- so across every frame of a speaking line the scaled pixels
 * are identical and only the destination y moves. Recomputing them per frame
 * meant an AppVar open, a full RLE decode and a full resample for a bitmap
 * that never changed.
 *
 * So a scaled bitmap is built once and kept, and the per-frame cost drops to
 * a single graphx blit -- the same cost as drawing any ordinary sprite.
 *
 * Two slots is not a guess: replaying all of Act 1 through tools/host_sim
 * with --trace, no scene state ever has more than one actor carrying
 * VN_FLAG_ZOOM (72 of 281 have exactly one, the rest none), and a zoomed
 * actor needs at most two bitmaps -- its body atom and its expression atom.
 * Two slots therefore hold a whole speaking character with nothing to evict,
 * and a new speaker replaces both within one frame.
 *
 * Cached bitmaps are freed on any chunk load (see assets_load_chunk), which
 * is where this program's largest allocation happens: a body atom's scaled
 * copy is ~14KB against a script chunk of up to ~62KB, and holding one
 * across that boundary could turn a chunk load -- fatal -- into a failure to
 * save a redraw that is merely nice to have.
 */

void assets_zoom_cache_clear(void)
{
    assets_sprite_end();
}

bool assets_zoom_prepare(uint16_t id)
{
    uint8_t appvar_idx;
    uint16_t offset;
    int16_t dx, dy;
    return sprite_lookup(id, &appvar_idx, &offset, NULL, &dx, &dy);
}

bool assets_draw_sprite_scaled(uint16_t id, int center_x, int feet_y, unsigned int scale_permille)
{
    const gfx_rletsprite_t *rle;
    int16_t dx, dy;
    if (!sprite_open_rlet(id, &rle, &dx, &dy)) {
        return false;
    }

    int src_w = rle->width;
    int src_h = rle->height;
    if (src_w <= 0 || src_h <= 0) {
        return true;
    }

    int dst_w = (int)(((uint32_t)src_w * scale_permille + 500) / 1000);
    int dst_h = (int)(((uint32_t)src_h * scale_permille + 500) / 1000);

    int zdx = (dx >= 0) ? (int)(((uint32_t)dx * scale_permille + 500) / 1000)
                        : -(int)(((uint32_t)(-dx) * scale_permille + 500) / 1000);
    int zdy = (dy >= 0) ? (int)(((uint32_t)dy * scale_permille + 500) / 1000)
                        : -(int)(((uint32_t)(-dy) * scale_permille + 500) / 1000);

    int dst_x = center_x - dst_w / 2 + zdx;
    int dst_y = feet_y - dst_h + zdy;

    if (dst_w == src_w && dst_h == src_h) {
        gfx_RLETSprite(rle, dst_x, dst_y);
        return true;
    }

    int x0 = dst_x > 0 ? dst_x : 0;
    int x1 = (dst_x + dst_w) < SCREEN_W ? (dst_x + dst_w) : SCREEN_W;
    int y0 = dst_y > 0 ? dst_y : 0;
    int y1 = (dst_y + dst_h) < SCREEN_H ? (dst_y + dst_h) : SCREEN_H;

    if (x0 < x1 && y0 < y1) {
        uint8_t row_buf[256];
        int vis_w = x1 - x0;
        int vis_h = y1 - y0;
        int start_dx = x0 - dst_x;
        int start_dy = y0 - dst_y;

        uint32_t init_num_x = (uint32_t)start_dx * (uint32_t)src_w + (uint32_t)(dst_w / 2);
        int start_sx = (int)(init_num_x / (uint32_t)dst_w);
        uint32_t init_acc_x = init_num_x % (uint32_t)dst_w;

        uint32_t init_num_y = (uint32_t)start_dy * (uint32_t)src_h + (uint32_t)(dst_h / 2);
        int start_sy = (int)(init_num_y / (uint32_t)dst_h);
        uint32_t acc_y = init_num_y % (uint32_t)dst_h;
        if (start_sy >= src_h) {
            start_sy = src_h - 1;
        }

        const uint8_t *rlet_ptr = rle->data;
        if (start_sy > 0) {
            rlet_ptr = fast_rlet_skip_rows(rlet_ptr, (size_t)start_sy, (uint8_t)src_w);
        }
        int next_sy = start_sy;
        int cur_sy = start_sy;
        int decoded_sy = -1;
        uint8_t *dest_row = (uint8_t *)gfx_vbuffer + (size_t)y0 * SCREEN_W + x0;

        for (int y = 0; y < vis_h; y++) {
            int want_sy = cur_sy < src_h ? cur_sy : (src_h - 1);
            if (want_sy != decoded_sy) {
                if (want_sy > next_sy) {
                    rlet_ptr = fast_rlet_skip_rows(rlet_ptr, (size_t)(want_sy - next_sy), (uint8_t)src_w);
                }
                rlet_ptr = fast_rlet_decode_row(row_buf, rlet_ptr, (uint8_t)src_w);
                decoded_sy = want_sy;
                next_sy = want_sy + 1;
            }
            fast_scale_row_trans(dest_row, row_buf + start_sx, (size_t)vis_w, init_acc_x,
                                 (unsigned int)src_w, (unsigned int)dst_w);
            dest_row += SCREEN_W;
            acc_y += (uint32_t)src_h;
            while (acc_y >= (uint32_t)dst_h) {
                acc_y -= (uint32_t)dst_h;
                cur_sy++;
            }
        }
    }

    return true;
}

bool assets_draw_sprite_zoomed(uint16_t id, int center_x, int feet_y)
{
    return assets_draw_sprite_scaled(id, center_x, feet_y, 1050);
}

bool assets_title_layout(uint8_t id, int *x, int *y, int *dx, int *dy)
{
    if (id >= title_pos_count) {
        return false;
    }
    const uint8_t *e = title_pos_buf + (size_t)id * 8;
    *x  = (int16_t)read_u16le(e);
    *y  = (int16_t)read_u16le(e + 2);
    *dx = (int16_t)read_u16le(e + 4);
    *dy = (int16_t)read_u16le(e + 6);
    return true;
}

/* The title screen redraws all of its art every frame, so opening and closing
 * each piece's AppVar per draw (the pattern assets_draw_sprite uses, where a
 * scene shows at most a few sprites and then waits for input) would mean five
 * open/close pairs per frame and visibly costs smoothness. Instead the handle
 * for the AppVar currently being read is kept between draws and only swapped
 * when a draw needs a different one -- assets_title_end() closes it at the end
 * of the frame.
 *
 * This still never holds a ti_GetDataPtr pointer across an unrelated AppVar
 * open: each pointer is used and discarded inside a single draw, and the only
 * other AppVar the title touches (DTILBG) is opened and closed before any of
 * this runs. */
static uint8_t title_handle;
static uint8_t title_handle_idx;

static const uint8_t *title_appvar(uint8_t appvar_idx)
{
    if (title_handle && title_handle_idx != appvar_idx) {
        ti_Close(title_handle);
        title_handle = 0;
    }
    if (!title_handle) {
        title_handle = pak_open_vol("DTIL", appvar_idx);
        if (!title_handle) {
            return NULL;
        }
        title_handle_idx = appvar_idx;
    }
    return ti_GetDataPtr(title_handle);
}

void assets_title_end(void)
{
    if (title_handle) {
        ti_Close(title_handle);
        title_handle = 0;
    }
}

bool assets_draw_title(uint8_t id, int left_x, int top_y)
{
    uint8_t appvar_idx;
    uint16_t offset;
    if (!lut_lookup(title_lut, title_lut_count, id, &appvar_idx, &offset, NULL)) {
        return false;
    }

    const uint8_t *data = title_appvar(appvar_idx);
    if (!data) {
        return false;
    }

    /* Top-left anchored, unlike assets_draw_sprite's centre/feet anchoring:
     * the title art's placement is already baked in (tools/image_resolve.py
     * alpha-crops each sheet and computes the resulting screen position), so
     * re-deriving an anchor here would double-apply it. */
    gfx_RLETSprite((const gfx_rletsprite_t *)(data + offset), left_x, top_y);
    return true;
}

/* The scrolling background strip: TITLE_STRIP_W is one tile wider than the
 * screen precisely so any horizontal offset in 0..TITLE_TILE-1 still leaves a
 * full screen row readable in one go, and the vertical wrap is a modulo on
 * the source row. See tools/image_resolve.py's BG_TILE_PERIOD for why the
 * pattern may be repeated this way at all. */
#define TITLE_TILE     50
#define TITLE_STRIP_W  (320 + TITLE_TILE)

bool assets_title_bg(uint8_t px, uint8_t py, uint8_t *dest)
{
    pak_view_t v;
    if (!pak_view_open("DTILBG", &v)) {
        return false;
    }

    px %= TITLE_TILE;
    py %= TITLE_TILE;
    fast_title_bg(dest, v.ptr, px, py);

    pak_view_close(&v);
    return true;
}

/* The notebook is a single ZX0-compressed full-frame stream in DPOEMBG.
 * Decode into the caller's graphics buffer; keep no permanent 77KB mirror.
 * The split raw format remains readable for older bundles. */
#define POEM_BG_BYTES  (320u * 240u)
#define POEM_BG_SPLIT  65000u

bool assets_poem_bg(uint8_t *dest)
{
    pak_view_t v;
    if (pak_view_open("DPOEMBG", &v)) {
        fast_zx0_decompress(dest, v.ptr);
        pak_view_close(&v);
        return true;
    }
    /* Compatibility with old uncompressed bundles. */
    uint8_t handle0 = ti_Open("DPOEMBG0", "r");
    if (!handle0) {
        return false;
    }
    if (ti_GetSize(handle0) != POEM_BG_SPLIT) {
        ti_Close(handle0);
        return false;
    }
    memcpy(dest, ti_GetDataPtr(handle0), POEM_BG_SPLIT);
    ti_Close(handle0);

    uint8_t handle1 = ti_Open("DPOEMBG1", "r");
    if (!handle1) {
        return false;
    }
    if (ti_GetSize(handle1) != POEM_BG_BYTES - POEM_BG_SPLIT) {
        ti_Close(handle1);
        return false;
    }
    memcpy(dest + POEM_BG_SPLIT, ti_GetDataPtr(handle1), POEM_BG_BYTES - POEM_BG_SPLIT);
    ti_Close(handle1);

    return true;
}

/* The dialogue/name boxes are immutable archived palette indices.
 * Open/copy/close together: never keep an AppVar pointer across another
 * fileioc call. No 20KB permanent RAM mirror of immutable flash pixels. */
bool assets_draw_box(bool namebox, uint8_t *dest)
{
    size_t w = namebox ? NAMEBOX_W : TEXTBOX_W;
    size_t h = namebox ? NAMEBOX_H : TEXTBOX_H;
    const char *aname = namebox ? "DNAMEBOX" : "DTXTBOX";
    pak_view_t v;
    if (pak_view_open(aname, &v)) {
        if (v.size != w * h) {
            pak_view_close(&v);
            return false;
        }
        fast_rect_blit(dest, SCREEN_W, v.ptr, w, w, h);
        pak_view_close(&v);
        return true;
    }
    /* Spanning fallback: blit row-by-row with zero extra RAM. */
    uint8_t vol, flags;
    uint16_t off;
    size_t total;
    if (!pak_stat(aname, &vol, &off, &total, &flags) || total != w * h) {
        return false;
    }
    for (size_t row = 0; row < h; row++) {
        if (!pak_read_span(vol, off, row * w, dest + row * SCREEN_W, w)) {
            return false;
        }
    }
    return true;
}

bool assets_draw_sticker_centered(uint8_t id, int center_x, int bottom_y)
{
    pak_view_t v;
    if (!pak_view_open("DSTKRS", &v)) {
        return false;
    }
    const uint8_t *data = v.ptr;
    uint8_t count = data[0];
    if (id >= count) {
        pak_view_close(&v);
        return false;
    }
    uint16_t offset = (uint16_t)(data[1 + (size_t)id * 2] | ((uint16_t)data[2 + (size_t)id * 2] << 8));
    const gfx_rletsprite_t *spr = (const gfx_rletsprite_t *)(data + offset);
    gfx_RLETSprite(spr, center_x - spr->width / 2, bottom_y - spr->height);
    pak_view_close(&v);
    return true;
}

/* Fixed size of every background (image_resolve.py's BG_SIZE) -- baked to
 * exactly this many raw palette-index bytes, so no length needs reading out
 * of the LUT entry. CGs are baked smaller (CG_SIZE below) -- see
 * assets_scene()'s own comment on why. */
#define SCENE_BYTES (320u * 180u)

/* CGs bake at half the real scene resolution (image_resolve.py's CG_SIZE,
 * 160x90 not SCREEN_W x SCENE_H) -- 4x fewer raw pixels to store/compress, a
 * deliberate quality tradeoff to close a real archive-budget overage without
 * touching backgrounds (which aren't what blows the budget). An external
 * full-res pack (src/cgpack.h) is tried first for any CG id and, when one is
 * plugged in and matches this build, skips this downscaled path entirely --
 * see assets_scene() below. */
#define SCENE_CG_SRC_W 160u
#define SCENE_CG_SRC_H 90u
#define SCENE_CG_BYTES (SCENE_CG_SRC_W * SCENE_CG_SRC_H)

/* A same-sized (57600-byte) decompressed-background cache was tried here to
 * avoid re-decompressing every frame render_scene() runs (every typewriter
 * tick, every idle-bob redraw -- not just an actual scene change), which is
 * why backgrounds shipped uncompressed in the first place. That cache
 * overflowed real RAM on-device even as a lazy malloc() -- graphx's own
 * draw buffer is already ~77KB, a resident script chunk can be up to 65KB
 * (see docs/FORMAT.md's "Chunking"), and this project's own ~150KB usable
 * figure doesn't leave room for another 57.6KB on top of both. Decompressing
 * straight into @p dest every call, like this, is the correctness-first
 * fallback: no extra RAM, at the cost of the per-frame decode this module
 * used to avoid. A CG's own scratch buffer below is a much smaller 14.4KB,
 * so it doesn't reopen that problem. */
static bool is_cg(uint8_t id)
{
    return cg_index_buf && id < cg_index_count && cg_index_buf[id] != 0xFF;
}

bool assets_debug_is_cg(uint8_t id)
{
    return is_cg(id);
}

bool assets_scene_fullscreen(uint8_t id)
{
    return scene_full_buf && id < scene_full_count && scene_full_buf[id] != 0;
}

bool assets_scene(uint8_t id, uint8_t *dest)
{
    assets_sprite_end();
    if (is_cg(id) && !assets_scene_fullscreen(id) && cgpack_read_pixels(id, dest)) {
        return true;
    }

    uint8_t appvar_idx;
    uint16_t offset;
    if (!lut_lookup(scene_lut, scene_lut_count, id, &appvar_idx, &offset, NULL)) {
        return false;
    }

    uint8_t handle = pak_open_vol("DSCN", appvar_idx);
    if (!handle) {
        return false;
    }
    const uint8_t *data = ti_GetDataPtr(handle);

    if (!is_cg(id)) {
        if (assets_scene_fullscreen(id)) {
            fast_zx0_decompress(dest, data + offset);
        } else {
            zx0_Decompress(dest, data + offset);
        }
        ti_Close(handle);
        return true;
    }

    /* Fullscreen (160x120 -> 320x240) and standard (160x90 -> 320x180) CGs
     * both stage in dest + SCENE_BYTES: for 120 rows, rows 0..118 write
     * at most dest[76159] before reading dest[76480], and row 119 copies
     * cleanly through a 160-byte stack buffer so input never overlaps output. */
    uint8_t *cg_scratch = dest + SCENE_BYTES;
    zx0_Decompress(cg_scratch, data + offset);
    ti_Close(handle);

    if (assets_scene_fullscreen(id)) {
        uint8_t last_row[SCENE_CG_SRC_W];
        memcpy(last_row, cg_scratch + (size_t)119u * SCENE_CG_SRC_W, SCENE_CG_SRC_W);
        fast_cg_upscale_2x_rows(dest, cg_scratch, 119u);
        fast_cg_upscale_2x_rows(dest + (size_t)238u * SCREEN_W, last_row, 1u);
    } else {
        fast_cg_upscale_2x(dest, cg_scratch);
    }
    return true;
}

const uint16_t *assets_scene_palette(uint8_t id)
{
    /* Deliberately not consulting cgpack here, unlike assets_scene() above:
     * tools/export_cgpack.py quantizes the full-res pack onto this exact
     * same DCGPAL palette (see its own comment), so the bytes are always
     * identical -- reading them a second time over USB on every scene
     * change would just be a slower, redundant path with no upside. */
    if (is_cg(id)) {
        uint8_t appvar_idx;
        uint16_t offset;
        uint16_t len = 0;
        if (lut_lookup(cgpal_lut, cgpal_lut_count, cg_index_buf[id], &appvar_idx, &offset, &len)) {
            if (len > sizeof(cg_palette_scratch)) {
                len = sizeof(cg_palette_scratch);
            }
            if (pak_active()) {
                memcpy(cg_palette_scratch, game_palette, sizeof(cg_palette_scratch));
                if (pak_read_span(appvar_idx, offset, 0, cg_palette_scratch, len)) {
                    return cg_palette_scratch;
                }
            } else {
                char name[9];
                sprintf(name, "DCGPAL%u", appvar_idx);
                uint8_t handle = ti_Open(name, "r");
                if (handle) {
                    const uint8_t *data = ti_GetDataPtr(handle);
                    memcpy(cg_palette_scratch, game_palette, sizeof(cg_palette_scratch));
                    memcpy(cg_palette_scratch, data + offset, len);
                    ti_Close(handle);
                    return cg_palette_scratch;
                }
            }
        }
    }
    return game_palette;
}
