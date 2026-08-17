/**
 * @file render.h
 * @brief Scene composition for the TI-84 Plus CE screen (graphx, 320x240 8bpp).
 *
 * Layout, per docs/FORMAT.md:
 *
 *   y   0..179   scene area  (1280x720 source scales exactly 4:1 to 320x180)
 *   y 180..239   dialogue box (60px, opaque -- no alpha blending needed)
 *
 * Sprites hang from a baseline just *below* the scene area (render.c's
 * ACTOR_BASELINE, DDLC's own `ypos 1.03`), so a character's lower body runs
 * off the bottom of the scene exactly as it does in the real game. The
 * overshoot lands under the dialogue box, which is drawn afterwards.
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

/* The real dialogue box/namebox art (tools/image_resolve.py's
 * ui_box_art()/TEXTBOX_SIZE/NAMEBOX_SIZE -- keep these in sync with those).
 * TEXTBOX_W spans the full box width/height (BOX_H) exactly; a namebox
 * this size fits the box's own top-left corner without covering more than
 * a sliver of the scene area above it. */
#define TEXTBOX_W    320
#define TEXTBOX_H    BOX_H
#define NAMEBOX_W    72
#define NAMEBOX_H    17

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

/* Title-palette-only entries (tools/convert_images.py's TITLE_FIXED_ENTRIES).
 * These are DDLC's nav panel colors, which render_title_screen() draws as two
 * rectangles instead of shipping the panel as art. They mean nothing while the
 * game palette is loaded -- only use them behind assets_use_title_palette(). */
#define COL_NAV_FILL    8
#define COL_NAV_EDGE    9

/**
 * Starts graphx. Call once at startup, *before* assets_init() -- palette
 * writes before gfx_Begin() don't reliably stick (its docs say it must run
 * before any other graphx routine). This function itself never touches the
 * palette, so it's safe to run first without clobbering assets_init()'s
 * load right after.
 */
void render_init(void);

/** Tear down graphx. */
void render_end(void);

/** Draw background + actors into the back buffer, without the dialogue box.
 * Always does the real work -- use this for a Show/Scene/Hide event (i.e.
 * from host_update()) or anywhere else the scene may have actually changed.
 * For a loop that redraws the *same* scene repeatedly (the typewriter
 * reveal, idle waits for input) with nothing but the dialogue text moving,
 * use render_scene_lazy() instead -- it's much cheaper once any zoom/hop/
 * sink transition has settled. */
void render_scene(const vn_scene_t *scene);

/**
 * Like render_scene(), but avoids the real redraw (a background zx0 decode
 * plus a full pass over every actor) in the two cases where it isn't needed:
 *
 *  - Nothing is animating. The draw buffer already holds the correct pixels,
 *    so this does nothing at all.
 *  - Exactly one actor is animating. Its rectangle is restored from a saved
 *    plate and only it (and anything drawn in front of it) is redrawn, which
 *    is what lets hop/sink/rise run at a frame rate that looks smooth rather
 *    than stepped. See the plate section in render.c.
 *
 * Only safe for a caller redrawing the *same* logical scene it last passed
 * to render_scene() -- see below. Drawing any overlay (render_menu(),
 * render_pause_box(), render_backdrop(), render_title_screen()) cancels both
 * shortcuts automatically, so the next call here is a full redraw.
 *
 * The buffer stays correct because render_present()
 * keeps it in sync with
 * whatever's actually on screen every frame. Only safe for a caller that
 * redraws the *same* logical scene every call (no Show/Scene/Hide can have
 * happened since the last render_scene()) -- the typewriter reveal and the
 * "waiting for the player to advance" idle loop, both in main.c.
 */
void render_scene_lazy(const vn_scene_t *scene);

/**
 * Forces the next render_scene_lazy() call to do a real redraw, even if the
 * scene has settled and nothing is animating. render_scene_lazy()'s whole
 * point is skipping redundant redraws when the draw buffer is already known
 * correct (see its own comment) -- that assumption breaks if the *source* a
 * settled scene's background comes from changes out from under it, which is
 * exactly what happens when src/cgpack.c's external CG pack becomes
 * available or unavailable mid-scene (a USB drive plugged in or pulled out
 * while a CG is already on screen). Cheap and rare enough to not need a
 * finer-grained "just this one CG" invalidation.
 */
void render_invalidate_scene(void);

/**
 * Draw the dialogue box with @p text revealed up to @p visible characters.
 * Pass SIZE_MAX to reveal the whole line.
 *
 * @p speaker is the name to show on the plate, or NULL for narration (no
 * plate at all). The caller resolves it rather than this function looking it
 * up: DDLC keeps the displayed names in story variables that the script
 * reassigns as the plot goes on -- "???" before an introduction, the real
 * name after, "???" again for Monika in Act 2 -- so the answer depends on VM
 * state this module has no business reaching into. See main.c's
 * speaker_display_name().
 */
void render_box(const vn_scene_t *scene, const char *speaker,
                const char *text, size_t visible);

/** Debug menu's dialogue text-render test -- see its own doc comment in
 * render.c. Draws only; caller handles input/present, same as render_text
 * and friends. */
void render_debug_text_test(void);

/** Draw the choice menu over the current scene, highlighting @p selected. */
void render_menu(const char *const *choices, uint8_t count, uint8_t selected);

/** Present the back buffer, optionally with a fade (enum vn_trans). */
void render_present(uint8_t trans);

/**
 * Fade the screen to black, hold, and (render_fade_in) back up.
 *
 * These ramp the palette rather than redrawing, so render_fade_out() darkens
 * whatever is already displayed -- call it *before* drawing the new scene,
 * then draw and present under the blacked-out palette, then render_fade_in().
 * That sequence reproduces DDLC's scene transitions, which are all
 * dissolve-to-black, pause, dissolve-back (see transforms.rpy).
 */
void render_fade_out(void);
void render_fade_in(void);

/** Writes @p palette (256 RGB565 entries, e.g. from assets_scene_palette())
 * straight to @c gfx_palette -- an instant pop, visible immediately (no
 * fade in progress). */
void render_apply_palette(const uint16_t *palette);

/**
 * Changes what render_fade_in() ramps up *to*, without touching what's
 * currently on screen. Call between render_fade_out() and render_fade_in()
 * when the scene being faded to also changes palette (a CG): the screen is
 * already held at black at that point, and this keeps it there while
 * swapping in the new target -- writing @p palette straight to gfx_palette
 * instead would pop it to full brightness while the *old* scene's pixels are
 * still what's on screen (render_scene() for the new one hasn't run yet).
 */
void render_fade_retarget(const uint16_t *palette);

/* ---------------------------------------------------------------------------
 * Title / pause / menu screens
 *
 * These are plain composable primitives (fill, text, list) rather than one
 * function per screen -- main.c composes the title screen, pause overlay,
 * help card, and save/load slot picker out of them, so adding or reordering
 * a screen doesn't mean adding a render.c function for it.
 * ------------------------------------------------------------------------ */

/** Flat-fills the whole screen with a reserved palette color (COL_*). */
void render_backdrop(uint8_t color);

/** One string at a fixed position in a reserved palette color. */
void render_text(const char *s, int x, int y, uint8_t color);

/** Like render_text(), but horizontally centered on the screen. */
void render_text_centered(const char *s, int y, uint8_t color);

/**
 * A vertical list of items, one per line starting at (@p x, @p y); the
 * @p selected item is prefixed with ">" and drawn in @p selected_color,
 * everything else in @p normal_color. Caller picks the colors so this reads
 * correctly on either a light backdrop (title) or a dark one (pause).
 */
void render_list_menu(const char *const *items, uint8_t count, uint8_t selected,
                      int x, int y, uint8_t normal_color, uint8_t selected_color);

/**
 * Like render_list_menu(), but marks @p selected with a filled bar spanning
 * @p w behind the text instead of a ">" prefix -- DDLC's own nav-panel style
 * (see render_title_screen()'s doc comment), for screens meant to read as
 * one of DDLC's real menu cards (pause overlay, save/load) rather than an
 * in-scene choice prompt.
 */
void render_list_menu_bar(const char *const *items, uint8_t count, uint8_t selected,
                          int x, int y, int w, uint8_t text_color,
                          uint8_t bar_color, uint8_t sel_text_color);

/** Flat-fills a rectangle in a reserved palette color -- highlight bars,
 * divider rules, drop shadows behind a panel. */
void render_fill_rect(int x, int y, int w, int h, uint8_t color);

/** Milliseconds the title intro runs for; past this everything is at rest. */
#define TITLE_INTRO_MS 3500

/**
 * Draws the whole title screen -- scrolling background, cast, nav panel, menu
 * items, and logo -- in DDLC's z-order, with @p selected highlighted.
 *
 * @p t is real elapsed milliseconds since the intro started (not a frame
 * count -- frames don't arrive at a fixed rate, and driving the entrance off
 * one would let a busy frame skip right over a whole animation window; see
 * the "Title screen" file comment above). It drives both the one-shot
 * entrance (pass >= TITLE_INTRO_MS to skip straight to the resting layout)
 * and the perpetual background scroll, so callers should keep it growing
 * rather than stopping at TITLE_INTRO_MS.
 *
 * Unlike the gameplay screens this draws its own menu rather than deferring
 * to render_list_menu(): DDLC's title highlights the selection with a filled
 * bar over the nav panel, not with the ">" prefix that suits the in-scene
 * choice menu. Requires the title palette (assets_use_title_palette()).
 */
void render_title_screen(uint8_t selected, unsigned t);

/** A bordered, filled box -- the pause overlay card, save/load slot cards,
 * drawn over an already-rendered game scene. Content (header text, list
 * menu) is the caller's job. */
void render_pause_box(int x, int y, int w, int h, uint8_t fill_color, uint8_t edge_color);

#endif /* RENDER_H */
