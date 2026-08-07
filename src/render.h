/**
 * @file render.h
 * @brief Scene composition for the TI-84 Plus CE screen (graphx, 320x240 8bpp).
 *
 * Layout, per docs/FORMAT.md:
 *
 *   y   0..179   scene area  (1280x720 source scales exactly 4:1 to 320x180)
 *   y 180..239   dialogue box (60px, opaque -- no alpha blending needed)
 *
 * Sprites anchor their feet to y=180 so they never intrude on the box.
 */

#ifndef RENDER_H
#define RENDER_H

#include "vn.h"

#include <stdbool.h>
#include <stdint.h>

#define SCREEN_W     320
#define SCREEN_H     240
#define SCENE_H      180
#define BOX_Y        SCENE_H
#define BOX_H        (SCREEN_H - SCENE_H)

/* Reserved palette entries. The generated game palette (tools/convert_images.py)
 * fills 8..255; these low indices are pinned via convimg 'fixed-entries' so
 * UI colors stay stable no matter which images the palette was built from.
 * assets_init() loads the whole 256-entry palette from the DPALGAME AppVar,
 * so these indices' actual colors are set there, not in this file. */
#define COL_TRANSPARENT 0
#define COL_BLACK       1
#define COL_WHITE       2
#define COL_BOX_FILL    3
#define COL_BOX_EDGE    4
#define COL_NAME        5
#define COL_HIGHLIGHT   6
#define COL_SHADOW      7

/**
 * Starts graphx. Call once at startup, after assets_init() has already
 * loaded gfx_palette -- this does not touch the palette itself, so it
 * won't clobber that load.
 */
void render_init(void);

/** Tear down graphx. */
void render_end(void);

/** Draw background + actors into the back buffer, without the dialogue box. */
void render_scene(const vn_scene_t *scene);

/**
 * Draw the dialogue box with @p text revealed up to @p visible characters.
 * Pass SIZE_MAX to reveal the whole line.
 */
void render_box(const vn_scene_t *scene, const char *text, size_t visible);

/** Draw the choice menu over the current scene, highlighting @p selected. */
void render_menu(const char *const *choices, uint8_t count, uint8_t selected);

/** Present the back buffer, optionally with a fade (enum vn_trans). */
void render_present(uint8_t trans);

#endif /* RENDER_H */
