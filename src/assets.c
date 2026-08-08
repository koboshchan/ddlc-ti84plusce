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
#include "render.h"

#include <compression.h>
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

    /* Same reasoning, one step further: a cached scaled sprite is worth a
     * few KB of redraw savings, and the chunk about to be read is worth the
     * game continuing to run. Drop the cache here rather than risk the
     * allocation below failing because of it -- the next zoomed draw simply
     * rebuilds what it needs. */
    assets_zoom_cache_clear();

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

/* Shared by assets_draw_sprite() and assets_draw_sprite_zoomed(): resolves
 * @p id to its LUT entry plus its DSPROFF draw offset (0,0 if DSPROFF is
 * missing or doesn't cover this id -- see the DSPROFF load in
 * assets_init()). */
static bool sprite_lookup(uint16_t id, uint8_t *appvar_out, uint16_t *offset_out,
                          int16_t *dx_out, int16_t *dy_out)
{
    if (!lut_lookup(sprite_lut, sprite_lut_count, id, appvar_out, offset_out)) {
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

bool assets_draw_sprite(uint16_t id, int center_x, int feet_y)
{
    uint8_t appvar_idx;
    uint16_t offset;
    int16_t dx, dy;
    if (!sprite_lookup(id, &appvar_idx, &offset, &dx, &dy)) {
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
    gfx_RLETSprite(sprite, center_x - sprite->width / 2 + dx, feet_y - sprite->height + dy);

    ti_Close(handle);
    return true;
}

/* Zoom is always exactly 1.05x -- DDLC's real `focus`/`hopfocus` speaking
 * zoom, the only scale this engine ever needs -- so this is a fixed 21:20
 * nearest-neighbor resample rather than a general scaler: no per-pixel
 * division beyond that one fixed ratio, and no dependency on graphx's
 * gfx_RotateScaleSprite (which requires a *square* input sprite -- see its
 * own doc comment in graphx.h -- and character atoms never are). */
#define ZOOM_NUM 21
#define ZOOM_DEN 20

static int zoom_scale_dim(int v)
{
    return (v * ZOOM_NUM + ZOOM_DEN / 2) / ZOOM_DEN;
}

/* Round-half-away-from-zero, unlike zoom_scale_dim() above (a dimension is
 * never negative) -- dx/dy frequently are (an atom cropped up/left of the
 * naive centered position), and scaling a negative offset the same way as a
 * positive one keeps a body atom and its overlay from drifting a stray
 * pixel apart from inconsistent rounding between the two. */
static int zoom_scale_off(int v)
{
    return (v >= 0) ? (v * ZOOM_NUM + ZOOM_DEN / 2) / ZOOM_DEN
                    : -((-v * ZOOM_NUM + ZOOM_DEN / 2) / ZOOM_DEN);
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

#define ZOOM_CACHE_SLOTS 2

static struct {
    uint16_t      id;
    gfx_sprite_t *spr;   /* NULL == this slot is empty */
} zoom_cache[ZOOM_CACHE_SLOTS];

static uint8_t zoom_cache_next;

void assets_zoom_cache_clear(void)
{
    for (uint8_t i = 0; i < ZOOM_CACHE_SLOTS; i++) {
        free(zoom_cache[i].spr);
        zoom_cache[i].spr = NULL;
    }
    zoom_cache_next = 0;
}

static gfx_sprite_t *zoom_cache_find(uint16_t id)
{
    for (uint8_t i = 0; i < ZOOM_CACHE_SLOTS; i++) {
        if (zoom_cache[i].spr != NULL && zoom_cache[i].id == id) {
            return zoom_cache[i].spr;
        }
    }
    return NULL;
}

/* Builds sprite @p id's scaled bitmap into a free (or round-robin evicted)
 * slot and returns it, or NULL if anything along the way fails -- a missing
 * id, an unopenable AppVar, or either allocation. Callers treat NULL as
 * "draw this one unscaled instead", never as fatal.
 *
 * Both buffers are alive at once here, briefly: the plain decode
 * (gfx_ConvertFromRLETSprite has no incremental form, so the source must be
 * materialized whole) and the scaled destination. That peak is why this is
 * worth doing exactly once per sprite rather than once per frame. */
static gfx_sprite_t *zoom_cache_fill(uint16_t id)
{
    uint8_t appvar_idx;
    uint16_t offset;
    int16_t dx, dy;
    if (!sprite_lookup(id, &appvar_idx, &offset, &dx, &dy)) {
        return NULL;
    }

    char name[9];
    sprintf(name, "DSPR%u", appvar_idx);
    uint8_t handle = ti_Open(name, "r");
    if (!handle) {
        return NULL;
    }

    const gfx_rletsprite_t *rle =
        (const gfx_rletsprite_t *)(ti_GetDataPtr(handle) + offset);

    gfx_sprite_t *plain = malloc((size_t)2 + (size_t)rle->width * rle->height);
    if (plain == NULL) {
        ti_Close(handle);
        return NULL;
    }
    gfx_ConvertFromRLETSprite(rle, plain);
    ti_Close(handle);

    int zw_i = zoom_scale_dim(plain->width);
    int zh_i = zoom_scale_dim(plain->height);
    /* gfx_sprite_t's dimensions are uint8_t, so a source atom over 242px in
     * either axis would scale past 255 and wrap silently, drawing garbage.
     * The import pipeline's own limit is 255 (image_resolve.py's
     * SPRITE_MAX_DIM), which is looser than this one -- today's largest
     * baked atom is 196px, but nothing enforces that, so refuse rather than
     * corrupt. The caller just draws this sprite unscaled. */
    if (zw_i > 255 || zh_i > 255) {
        free(plain);
        return NULL;
    }
    uint8_t zw = (uint8_t)zw_i;
    uint8_t zh = (uint8_t)zh_i;
    gfx_sprite_t *scaled = malloc((size_t)2 + (size_t)zw * zh);
    if (scaled == NULL) {
        free(plain);
        return NULL;
    }
    scaled->width  = zw;
    scaled->height = zh;

    /* Output pixel (x, y) samples source (x*ZOOM_DEN/ZOOM_NUM, likewise for
     * y). Doing those two divisions per pixel is what made this path
     * visibly laggy back when it ran every frame: the eZ80 has no divide
     * instruction, so each one is a software routine costing far more than
     * the byte copy it guards. The ratio is fixed at 20:21, so walk it with
     * a Bresenham accumulator instead -- the source index advances one per
     * output pixel except on every 21st, where it repeats.
     *
     * No clamping of sx/sy against the source dimensions is needed:
     * zoom_scale_dim() rounds, so the largest output index maps to at most
     * (dim - 1) for every dimension -- (zw-1)*20 <= 21*w - 10 < 21*w. */
    const uint8_t *srow = plain->data;
    uint8_t       *dst  = scaled->data;
    int ay = 0;

    for (uint8_t y = 0; y < zh; y++) {
        int sx = 0;
        int ax = 0;

        for (uint8_t x = 0; x < zw; x++) {
            *dst++ = srow[sx];
            ax += ZOOM_DEN;
            if (ax >= ZOOM_NUM) {
                ax -= ZOOM_NUM;
                sx++;
            }
        }

        ay += ZOOM_DEN;
        if (ay >= ZOOM_NUM) {
            ay -= ZOOM_NUM;
            srow += plain->width;
        }
    }

    free(plain);

    free(zoom_cache[zoom_cache_next].spr);
    zoom_cache[zoom_cache_next].id  = id;
    zoom_cache[zoom_cache_next].spr = scaled;
    zoom_cache_next = (uint8_t)((zoom_cache_next + 1) % ZOOM_CACHE_SLOTS);
    return scaled;
}

bool assets_zoom_prepare(uint16_t id)
{
    return zoom_cache_find(id) != NULL || zoom_cache_fill(id) != NULL;
}

bool assets_draw_sprite_zoomed(uint16_t id, int center_x, int feet_y)
{
    gfx_sprite_t *spr = zoom_cache_find(id);
    if (spr == NULL && (spr = zoom_cache_fill(id)) == NULL) {
        return false;
    }

    /* dx/dy are re-derived per draw rather than cached alongside the bitmap:
     * sprite_lookup() is a LUT search plus an array read, no file I/O, and
     * keeping the cache entry to just the pixels means one less thing that
     * can fall out of step with DSPROFF. */
    uint8_t appvar_idx;
    uint16_t offset;
    int16_t dx, dy;
    if (!sprite_lookup(id, &appvar_idx, &offset, &dx, &dy)) {
        return false;
    }

    gfx_TransparentSprite(spr, center_x - spr->width / 2 + zoom_scale_off(dx),
                          feet_y - spr->height + zoom_scale_off(dy));
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

/* The poem minigame's notebook background: full-screen (320x240 = 76800
 * bytes) raw indices, unlike an ordinary scene (see image_resolve.py's
 * poem_background() for why it isn't one). 76800 bytes is over the
 * 65535-byte single-AppVar ceiling, so tools/import_game.py splits it in
 * two at a fixed MAXVARSIZE (65000) boundary rather than through a LUT --
 * there's only ever this one resource, a LUT would be pure overhead.
 * Optional, same as the title assets, so a bundle built before the poem
 * minigame existed still boots. */
#define POEM_BG_BYTES  (320u * 240u)
#define POEM_BG_SPLIT  65000u

bool assets_poem_bg(uint8_t *dest)
{
    uint8_t handle0 = ti_Open("DPOEMBG0", "r");
    if (!handle0) {
        return false;
    }
    memcpy(dest, ti_GetDataPtr(handle0), POEM_BG_SPLIT);
    ti_Close(handle0);

    uint8_t handle1 = ti_Open("DPOEMBG1", "r");
    if (!handle1) {
        return false;
    }
    memcpy(dest + POEM_BG_SPLIT, ti_GetDataPtr(handle1), POEM_BG_BYTES - POEM_BG_SPLIT);
    ti_Close(handle1);

    return true;
}

/* Fixed size of every background/CG (image_resolve.py's BG_SIZE/CG_SIZE) --
 * both are baked to exactly this many raw palette-index bytes, so no length
 * needs reading out of the LUT entry. */
#define SCENE_BYTES (320u * 180u)

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
 * used to avoid. */
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

    const uint8_t *data = ti_GetDataPtr(handle);
    zx0_Decompress(dest, data + offset);
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
