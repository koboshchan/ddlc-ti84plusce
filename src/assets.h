/**
 * @file assets.h
 * @brief Loads the AppVars tools/import_game.py packages (see docs/FORMAT.md).
 *
 * DENTRY/DSCRn/DSPRLUT/DSCNLUT/DPALGAME are read into private RAM copies;
 * script text and the sprite/scene tables are zero-copy *within that private
 * buffer*, not the original AppVar -- see assets.c's file comment for why a
 * pointer straight into the AppVar isn't safe to hold across the many
 * DSPRn/DSCNn opens a play session does. Per-image sprite data stays a
 * direct pointer into its AppVar for the brief window it's actually being
 * drawn. Backgrounds are copied into a caller-supplied destination (the
 * graphx draw buffer) -- uncompressed on disk, not decompressed here, since
 * this runs every frame a scene redraws and a per-frame decode was the
 * actual bottleneck behind sluggish rendering (see assets.c's
 * assets_scene()).
 *
 * Only one script chunk is ever resident (assets_load_chunk()) -- see
 * docs/FORMAT.md's "Chunking".
 */

#ifndef ASSETS_H
#define ASSETS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <graphx.h>

/** Which step assets_init() got to before failing, for on-device diagnosis
 * (the AppVars can be present per the OS's own variable list yet loading
 * still fail, e.g. a corrupt/truncated transfer or a format mismatch --
 * a plain bool can't tell those apart from "not sent at all"). */
typedef enum {
    ASSETS_OK = 0,
    ASSETS_ERR_ENTRY,           /* DENTRY missing/unreadable */
    ASSETS_ERR_SCRIPT_OPEN,     /* the entry chunk's DSCRn missing/unreadable/corrupt */
    ASSETS_ERR_SPRITE_LUT,      /* DSPRLUT missing/unreadable */
    ASSETS_ERR_SCENE_LUT,       /* DSCNLUT missing/unreadable */
    ASSETS_ERR_PALETTE,         /* DPALGAME missing/unreadable */
} assets_status_t;

/**
 * Reads DENTRY (the packed entry pc -- see vn.h's VN_PACK_ADDR) and loads
 * its chunk, plus DSPRn, DSCNn, and DPALGAME into gfx_palette. Call once
 * before vn_init().
 */
assets_status_t assets_init(void);

/** Human-readable form of @p status, for an on-device error screen. */
const char *assets_status_str(assets_status_t status);

/**
 * Loads chunk @p chunk_id (its DSCRn AppVar), replacing whichever chunk was
 * previously resident -- both at startup (assets_init(), for the entry
 * chunk) and at runtime (main.c's vn_host_t.load_chunk, whenever a Jump/Call
 * crosses a chunk boundary). Returns false if the AppVar is missing,
 * unreadable, or its string pool is too large (see assets.c's MAX_STRINGS).
 */
bool assets_load_chunk(uint8_t chunk_id);

/** DENTRY's packed entry pc (vn.h's VN_PACK_ADDR/VN_CHUNK_ID/
 * VN_CHUNK_OFFSET) -- where main.c should point vm.pc after vn_init(). */
uint32_t assets_entry_pc(void);

/** Zero-copy pointer to the currently resident chunk's bytecode, plus its
 * size (see assets_load_chunk()). */
const uint8_t *assets_script(size_t *size_out);

/** Zero-copy pointer to string @p index's NUL-terminated UTF-8 bytes. */
const char *assets_string(uint16_t index);

/**
 * Draws sprite @p id with its center-bottom anchored at
 * (@p center_x, @p feet_y). Returns false if @p id is out of range or its
 * AppVar is missing.
 *
 * Draws internally (rather than handing back a gfx_rletsprite_t*) so the
 * sprite's AppVar handle can be opened, used, and closed within this one
 * call -- see assets.c's file comment for why that matters.
 */
bool assets_draw_sprite(uint16_t id, int center_x, int feet_y);

/**
 * Copies background/CG @p id's raw 320x180 palette-index pixels directly
 * into @p dest (typically the graphx draw buffer). Returns false
 * if @p id is out of range or its AppVar is missing.
 */
bool assets_scene(uint8_t id, uint8_t *dest);

/**
 * The 256-entry RGB565 palette scene/background @p id should render under --
 * the shared game palette for an ordinary background, or a CG's own private
 * one if @p id was baked with one (tools/convert_images.py's per-CG palette
 * group; see docs/FORMAT.md). Never fails: falls back to the game palette if
 * @p id has no CG palette, or if one is expected but its AppVar is missing.
 *
 * Returns a pointer into a private buffer this module owns (or into the
 * loaded game palette, when there's no CG involved) -- valid only until the
 * next assets_scene_palette() call. Callers apply it to @c gfx_palette
 * themselves (render_apply_palette() / render_fade_retarget()) rather than
 * this function writing it directly, since *when* that write should become
 * visible depends on whether a fade is in progress -- see main.c's
 * host_update().
 */
const uint16_t *assets_scene_palette(uint8_t id);

/* ---------------------------------------------------------------------------
 * Title screen
 *
 * DDLC's title screen has its own art set (`gui/menu_art_*.png`, the logo),
 * its own palette, and its own scrolling background -- it shares nothing with
 * the dialogue sprites. All of it is optional: a bundle built without it still
 * boots into a playable game, and these calls just fail cleanly.
 *
 * Ids match tools/import_game.py's TITLE_ART order, with the logo appended.
 * ------------------------------------------------------------------------ */

enum {
    TITLE_YURI = 0,
    TITLE_NATSUKI,
    TITLE_SAYORI,
    TITLE_MONIKA,
    TITLE_LOGO,
    TITLE_ART_COUNT,
};

/**
 * Swaps @c gfx_palette between the title screen's palette and the game's.
 * No-op when @p on is true but no title palette was shipped.
 *
 * Safe to leave loaded across the help/save-slot screens: both palettes pin
 * the same reserved entries 0..7 (tools/convert_images.py's FIXED_ENTRIES),
 * so every COL_* in render.h means the same color either way.
 */
void assets_use_title_palette(bool on);

/**
 * Title art @p id's resting top-left position and its entrance offset (the
 * delta it animates *from*, per DDLC's menu_art_move / menu_logo_move).
 * False if @p id has no entry. Computed at bake time rather than in render.c
 * because alpha-cropping shifts it -- see tools/image_resolve.py.
 */
bool assets_title_layout(uint8_t id, int *x, int *y, int *dx, int *dy);

/** Draws title art @p id with its **top-left** at (@p left_x, @p top_y).
 * Coordinates are signed and clipped, so partly-offscreen is fine.
 *
 * Keeps the underlying AppVar open between calls so a whole frame's worth of
 * art costs one open instead of one per piece; call assets_title_end() once
 * the frame is done. */
bool assets_draw_title(uint8_t id, int left_x, int top_y);

/** Releases the AppVar assets_draw_title() left open. Call once per frame,
 * after the last title draw. */
void assets_title_end(void);

/**
 * Fills @p dest (a full 320x240 screen) with the title's tiling background,
 * scrolled by (@p px, @p py). Both offsets wrap, so a caller can just count
 * up forever. False if the background AppVar is missing.
 */
bool assets_title_bg(uint8_t px, uint8_t py, uint8_t *dest);

#endif /* ASSETS_H */
