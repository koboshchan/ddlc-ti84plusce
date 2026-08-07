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

#include <fileioc.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* A single resident chunk's string pool, generously capped -- the largest
 * single compiled chunk seen so far (script-poemresponses) uses 1257. Table
 * of pointers, not copies: each points directly into script_buf (this
 * module's private copy of the current chunk's bytes). */
#define MAX_STRINGS 2048

static uint8_t         *script_buf; /* owns the current chunk's bytes */
static const uint8_t   *script_code;
static size_t            script_code_size;
static const char       *string_ptrs[MAX_STRINGS];
static uint16_t          string_count;
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
static uint8_t   *cgpal_lut_buf;
static const uint8_t *cgpal_lut;
static uint16_t   cgpal_lut_count;
static uint16_t   cg_palette_scratch[256]; /* assets_scene_palette()'s return buffer */

static uint16_t read_u16le(const uint8_t *p)
{
    return (uint16_t)(p[0] | (uint16_t)(p[1] << 8));
}

/** Reads @p name's whole contents into a freshly malloc'd buffer the caller
 * owns, closing the handle before returning. NULL if @p name can't be
 * opened or the allocation fails. */
static uint8_t *read_whole(const char *name, uint16_t *size_out)
{
    uint8_t handle = ti_Open(name, "r");
    if (!handle) {
        return NULL;
    }

    uint16_t size = ti_GetSize(handle);
    uint8_t *buf = malloc(size);
    if (!buf) {
        ti_Close(handle);
        return NULL;
    }

    ti_Read(buf, 1, size, handle);
    ti_Close(handle);

    if (size_out) {
        *size_out = size;
    }
    return buf;
}

bool assets_load_chunk(uint8_t chunk_id)
{
    /* Only one chunk is ever resident (see docs/FORMAT.md's "Chunking") --
     * the previous one's buffer has to go before loading the next, or a long
     * session that crosses many chunk boundaries would leak one chunk's
     * worth of RAM per crossing. First call has nothing to free yet. */
    free(script_buf);
    script_buf = NULL;

    char name[9];
    sprintf(name, "DSCR%u", chunk_id);

    uint16_t total;
    script_buf = read_whole(name, &total);
    if (!script_buf) {
        return false;
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
    if (!load_lut("DSCNLUT", &scene_lut_buf, &scene_lut, &scene_lut_count)) {
        return ASSETS_ERR_SCENE_LUT;
    }

    uint16_t pal_size;
    uint8_t *pal = read_whole("DPALGAME", &pal_size);
    if (!pal) {
        return ASSETS_ERR_PALETTE;
    }
    memcpy(gfx_palette, pal, sizeof(game_palette));
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
    load_lut("DCGPLUT", &cgpal_lut_buf, &cgpal_lut, &cgpal_lut_count);

    return ASSETS_OK;
}

void assets_use_title_palette(bool on)
{
    if (on && !title_palette_ok) {
        return; /* no title palette shipped -- leave the game one in place */
    }
    memcpy(gfx_palette, on ? title_palette : game_palette, sizeof(game_palette));
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
        default:                           return "unknown";
    }
}

uint32_t assets_entry_pc(void)
{
    return entry_pc;
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

/* LUT entry layout: u8 appvar_index, u16 offset, u16 length (5 bytes). @p id
 * is u16 (not every LUT this indexes has more than 255 entries, but the
 * sprite one does -- 406 for the full game -- so the shared helper takes the
 * wider type for all callers rather than having two near-identical copies). */
static bool lut_lookup(const uint8_t *lut, uint16_t count, uint16_t id,
                       uint8_t *appvar_out, uint16_t *offset_out)
{
    if (id >= count) {
        return false;
    }
    const uint8_t *e = lut + (size_t)id * 5;
    *appvar_out = e[0];
    *offset_out = read_u16le(e + 1);
    return true;
}

bool assets_draw_sprite(uint16_t id, int center_x, int feet_y)
{
    uint8_t appvar_idx;
    uint16_t offset;
    if (!lut_lookup(sprite_lut, sprite_lut_count, id, &appvar_idx, &offset)) {
        return false;
    }

    char name[9];
    sprintf(name, "DSPR%u", appvar_idx);
    uint8_t handle = ti_Open(name, "r");
    if (!handle) {
        return false;
    }

    const uint8_t *data = ti_GetDataPtr(handle);
    const gfx_rletsprite_t *sprite = (const gfx_rletsprite_t *)(data + offset);
    gfx_RLETSprite(sprite, center_x - sprite->width / 2, feet_y - sprite->height);

    ti_Close(handle);
    return true;
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
        char name[9];
        sprintf(name, "DTIL%u", appvar_idx);
        title_handle = ti_Open(name, "r");
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
    if (!lut_lookup(title_lut, title_lut_count, id, &appvar_idx, &offset)) {
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
    uint8_t handle = ti_Open("DTILBG", "r");
    if (!handle) {
        return false;
    }

    const uint8_t *strip = ti_GetDataPtr(handle);
    px %= TITLE_TILE;
    for (int y = 0; y < 240; y++) {
        unsigned src_row = (unsigned)(y + py) % TITLE_TILE;
        memcpy(dest + (size_t)y * 320,
               strip + src_row * TITLE_STRIP_W + px, 320);
    }

    ti_Close(handle);
    return true;
}

/* Fixed size of every background/CG (image_resolve.py's BG_SIZE/CG_SIZE) --
 * both are baked to exactly this many raw palette-index bytes, so no length
 * needs reading out of the LUT entry. */
#define SCENE_BYTES (320u * 180u)

bool assets_scene(uint8_t id, uint8_t *dest)
{
    uint8_t appvar_idx;
    uint16_t offset;
    if (!lut_lookup(scene_lut, scene_lut_count, id, &appvar_idx, &offset)) {
        return false;
    }

    char name[9];
    sprintf(name, "DSCN%u", appvar_idx);
    uint8_t handle = ti_Open(name, "r");
    if (!handle) {
        return false;
    }

    /* Was a per-frame zx0_Decompress() -- background art is redrawn every
     * typewriter tick and every idle-bob frame (see render.c), so that
     * decode was the actual bottleneck behind sluggish text and a
     * crawling-slow breathing animation. Backgrounds now ship uncompressed
     * (tools/convert_images.py), so this is a flat copy instead. */
    const uint8_t *data = ti_GetDataPtr(handle);
    memcpy(dest, data + offset, SCENE_BYTES);

    ti_Close(handle);
    return true;
}

const uint16_t *assets_scene_palette(uint8_t id)
{
    if (cg_index_buf && id < cg_index_count && cg_index_buf[id] != 0xFF) {
        uint8_t appvar_idx;
        uint16_t offset;
        if (lut_lookup(cgpal_lut, cgpal_lut_count, cg_index_buf[id], &appvar_idx, &offset)) {
            char name[9];
            sprintf(name, "DCGPAL%u", appvar_idx);
            uint8_t handle = ti_Open(name, "r");
            if (handle) {
                const uint8_t *data = ti_GetDataPtr(handle);
                memcpy(cg_palette_scratch, data + offset, sizeof(cg_palette_scratch));
                ti_Close(handle);
                return cg_palette_scratch;
            }
        }
    }
    return game_palette;
}
