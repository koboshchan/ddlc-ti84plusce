/**
 * @file render.c
 * @brief Scene composition via graphx. See render.h.
 */

#include "assets.h"
#include "render.h"
#include "text.h"

#include <graphx.h>
#include <sys/timers.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* Seeded by render_init(), used by the tear effect section further down --
 * forward-declared here since render_init() runs before that section's own
 * declaration would otherwise be visible. */
static uint32_t tear_rng_state;

void render_init(void)
{
    /* No gfx_SetDefaultPalette() here: assets_init() already loaded the
     * real 256-entry palette (including the fixed UI indices 0..7) into
     * gfx_palette before this runs, and SetDefaultPalette would overwrite
     * the whole thing with a generic gradient. */
    gfx_Begin();
    gfx_SetDrawBuffer();
    gfx_SetTextTransparentColor(COL_TRANSPARENT);
    gfx_SetTextBGColor(COL_TRANSPARENT);
    /* Independent of the text transparency above -- a separate graphx
     * global. Not actually read by assets_draw_sprite_zoomed() (it checks
     * the transparent index directly rather than going through a
     * gfx_TransparentSprite()-style call), but set here once anyway to keep
     * this convention fixed and documented in one place. */
    gfx_SetTransparentColor(COL_TRANSPARENT);

    /* Seeds the tear effect's own PRNG -- see its section below for why
     * this doesn't share vn.c's OP_RANDOM state. 0 remapped to a fixed
     * nonzero fallback for the same reason vn_init() does: xorshift32
     * can't recover from an all-zero state. */
    uint32_t seed = (uint32_t)clock();
    tear_rng_state = seed ? seed : 0x9E3779B9u;
}

void render_end(void)
{
    gfx_End();
}

/* ---------------------------------------------------------------------------
 * Text measurement
 * ------------------------------------------------------------------------ */

/* gfx_GetStringWidth needs a NUL-terminated string, but the wrapper measures
 * slices, so widths are summed per character instead. */
static unsigned measure(void *ctx, const char *str, size_t len)
{
    (void)ctx;

    unsigned w = 0;
    for (size_t i = 0; i < len; i++) {
        w += gfx_GetCharWidth(str[i]);
    }
    return w;
}

static void print_slice(const char *str, size_t len, int x, int y)
{
    gfx_SetTextXY(x, y);
    for (size_t i = 0; i < len; i++) {
        gfx_PrintChar(str[i]);
    }
}

/* Real DDLC's dialogue font is a solid fill over a distinct outline, not a
 * flat single color -- this font has no such glyph, so the outline is
 * faked the same way most sprite-font-less engines do it: the same slice
 * drawn at a 1px offset in @p outline along each of the 4 cardinal
 * directions, then once more dead center in @p fill on top.
 * gfx_SetTextTransparentColor() (render_init()) keeps every pass from
 * clobbering non-glyph pixels, so the offset passes only ever widen each
 * glyph's own silhouette by a ring -- never touch what's already there
 * outside it -- and the final centered pass repaints every pixel the true
 * glyph covers, leaving just that ring showing through as the outline.
 * Cardinal-only (not the full 8-direction ring, which also hits the
 * diagonals) -- the diagonal passes double up at every corner of a glyph's
 * strokes, reading as a noticeably heavier/blockier outline than real
 * DDLC's thin one. */
static void print_slice_outlined(const char *str, size_t len, int x, int y,
                                 uint8_t fill, uint8_t outline)
{
    static const int8_t off[4][2] = {
                 {0, -1},
        {-1, 0},         {1, 0},
                 {0,  1},
    };
    gfx_SetTextFGColor(outline);
    for (int i = 0; i < 4; i++) {
        print_slice(str, len, x + off[i][0], y + off[i][1]);
    }
    gfx_SetTextFGColor(fill);
    print_slice(str, len, x, y);
}

/* ---------------------------------------------------------------------------
 * Easing
 *
 * Shared by the speaking pop (below) and the title screen entrance (further
 * down): both need a smooth eased offset driven by real elapsed time, not a
 * continuous idle animation, and this LUT/ease() pair is generic in either
 * case -- what differs per caller is just the curve, amplitude, and timing.
 * ------------------------------------------------------------------------ */

/* Remaining-offset curves, 255 (fully displaced) -> 0 (arrived), sampled 32
 * ways. Integer tables rather than float easing: this can run per element
 * per frame, and the eZ80 has no FPU. Max amplitude is 300, so 300*255 stays
 * well inside a 24-bit int -- no overflow, no division. */
#define EASE_STEPS 32

static const uint8_t ease_remain[EASE_STEPS] = {
    255, 254, 252, 249, 245, 239, 232, 224,
    215, 206, 195, 184, 172, 159, 147, 134,
    121, 108,  96,  83,  71,  60,  49,  40,
     31,  23,  16,  10,   6,   3,   1,   0,
};
static const uint8_t quint_remain[EASE_STEPS] = {
    255, 224, 195, 170, 147, 126, 108,  92,
     77,  65,  54,  44,  36,  29,  23,  18,
     14,  11,   8,   6,   4,   3,   2,   1,
      1,   0,   0,   0,   0,   0,   0,   0,
};
static const uint8_t bounce_remain[EASE_STEPS] = {
    255, 253, 247, 237, 223, 205, 183, 157,
    127,  92,  54,  12,  15,  33,  47,  56,
     62,  64,  61,  55,  45,  30,  12,   5,
     12,  16,  15,  11,   2,   3,   4,   0,
};

/** How much of @p amp is still left to travel at time @p t (ms), per @p lut.
 *
 * Interpolates between table entries rather than snapping to the nearest:
 * with only EASE_STEPS samples spread over the intro, snapping quantises the
 * motion into visible steps, and the bounce curve in particular has enough
 * high-frequency detail near its end to read as jitter instead of a bounce. */
static int ease(const uint8_t *lut, int amp, unsigned t, unsigned at, unsigned dur)
{
    if (t <= at) {
        return amp;
    }
    unsigned elapsed = t - at;
    if (elapsed >= dur) {
        return 0;
    }

    unsigned pos  = elapsed * ((EASE_STEPS - 1) << 8) / dur;  /* 8 frac bits */
    unsigned i    = pos >> 8;
    unsigned frac = pos & 0xFF;
    int a = lut[i];
    int b = (i + 1 < EASE_STEPS) ? lut[i + 1] : 0;

    return (amp * (a + (((b - a) * (int)frac) >> 8))) / 255;
}

/* ---------------------------------------------------------------------------
 * Scene art
 * ------------------------------------------------------------------------ */

/* The speaking pop: DDLC's own ATL (transforms.rpy's `focus`/`hopfocus` vs.
 * `tcommon`/`hop`, plus `sink` for the persistent downward drift below)
 * zooms whichever character is currently speaking to 1.05x, separately
 * bounces briefly for "hop"-flagged lines, and separately still drifts a
 * character down and holds for "sink"-flagged lines -- all authored
 * directly per-line in the real script (which named transform, e.g. "f32"
 * vs "t32" vs "s32", compiles a Show), not inferred at runtime from
 * OP_SAY's speaker field the way an earlier version of this engine did (see
 * tools/compile_script.py's _resolve_anim). assets_draw_sprite_zoomed()
 * does the real 1.05x scale now.
 *
 * The scaled bitmap is cached, so a speaking line pays for it once rather
 * than per frame -- but building it still needs memory that may not be
 * there alongside a large resident script chunk (see assets.c's
 * scaled-sprite cache). So zoom_fallback_offset() below is kept, not
 * deleted: it's the small vertical-rise approximation this engine used
 * exclusively before real scaling existed, now repurposed as what
 * draw_actor() falls back to when the scale can't be built. */
#define SPEAK_POP_PX   4    /* screen px risen, as a zoom stand-in */
#define SPEAK_POP_MS 250    /* ms to ease in or out of the pop */

/* Set by any of the three offset helpers below whose eased transition is
 * still in flight as of the frame being drawn, and cleared by render_scene()
 * before it walks the actors. What's left afterwards answers "could the next
 * frame of this same scene come out different from this one?", which is
 * exactly what render_scene_lazy() needs to know -- see it for why that
 * matters. Each helper owns the test for its own duration so the answer
 * can't drift from the easing it describes. */
static bool anim_moving;

static int zoom_fallback_offset(uint8_t character, bool zoomed, unsigned t)
{
    static bool     was_zoomed[VN_MAX_CHARS];
    static unsigned changed_at[VN_MAX_CHARS];

    int old_rest = was_zoomed[character] ? -SPEAK_POP_PX : 0;
    if (zoomed != was_zoomed[character]) {
        was_zoomed[character] = zoomed;
        changed_at[character] = t;
    }
    int new_rest = zoomed ? -SPEAK_POP_PX : 0;
    if (t - changed_at[character] < SPEAK_POP_MS) {
        anim_moving = true;
    }

    /* ease() decays `amp` toward 0 as `t` runs from the change to
     * change+dur, so amp = old_rest - new_rest lands exactly on old_rest at
     * t=changed_at and new_rest once the ease finishes, whichever direction
     * this particular transition runs. */
    return new_rest + ease(ease_remain, old_rest - new_rest, t,
                           changed_at[character], SPEAK_POP_MS);
}

/* The real one-shot hop: DDLC's `hop`/`hopfocus` ATL eases yoffset to -20
 * over .1s then back to 0 over .1s -- a bounce that plays once per Show,
 * not a sustained state (unlike the zoom above, which holds for as long as
 * the character keeps being shown under a focus-flagged transform). Two
 * back-to-back applications of the same ease() primitive above cover the
 * down-then-up shape; no new curve is needed. HOP_PX is DDLC's real 20
 * Ren'Py px scaled by this engine's existing 0.25 canvas-to-screen ratio,
 * matching SPEAK_POP_PX's own convention. */
#define HOP_PX  5
#define HOP_MS 200

static int hop_offset(uint8_t character, uint8_t show_seq, unsigned t)
{
    static uint8_t  last_seq[VN_MAX_CHARS];
    static unsigned hop_started_at[VN_MAX_CHARS];

    /* actor->show_seq bumps on every real OP_SHOW targeting this character,
     * whether or not the sprite/pos actually changed -- e.g. `hop` used
     * purely for emphasis on an otherwise-unchanged pose -- so diffing it
     * here (rather than diffing the drawn sprite id) catches that case too.
     * Only called by draw_actor() while the current Show is hop-flagged, so
     * an unflagged Show correctly abandons any bounce still in flight
     * instead of finishing it -- matching the source ATL, where the bounce
     * belongs to that specific transform being the active one. */
    if (show_seq != last_seq[character]) {
        last_seq[character] = show_seq;
        hop_started_at[character] = t;
    }

    unsigned elapsed = t - hop_started_at[character];
    if (elapsed >= HOP_MS) {
        return 0;
    }
    anim_moving = true;
    if (elapsed < HOP_MS / 2) {
        return -HOP_PX + ease(ease_remain, HOP_PX, t, hop_started_at[character], HOP_MS / 2);
    }
    return ease(ease_remain, -HOP_PX, t, hop_started_at[character] + HOP_MS / 2, HOP_MS / 2);
}

/* DDLC's real sink: `at s..` eases ypos from 1.03 to 1.06 (a 0.03 fraction
 * of the 720-tall canvas, ~21.6 canvas px) over .5s and *holds* there --
 * unlike hop, this isn't a bounce-back, it's a persistent drift that only
 * reverses when a later Show lands the character back on t/f. That recovery
 * is authored on the *landing* transform, not on sink itself: both
 * tcommon's and focus's own "replace" handler eases ypos back to 1.03 over
 * .15s in parallel with whatever zoom change is also happening, confirmed
 * by decompiling transforms.rpy. SINK_PX is that 21.6 canvas px scaled by
 * this engine's existing 0.25 canvas-to-screen ratio, same convention as
 * HOP_PX/SPEAK_POP_PX.
 *
 * Structurally this is zoom_fallback_offset()'s pattern (a persistent
 * on/off state with an eased transition, not hop's one-shot bounce) --
 * except the two directions run at different real durations, so the target
 * value and duration are both picked from `sinking` rather than being
 * fixed. Called unconditionally every frame, same reasoning as
 * zoom_fallback_offset(): the eased transition has to keep tracking
 * correctly even across frames where the value goes unused.
 *
 * SINK_DOWN_MS is 300, not DDLC's real 500 -- a deliberate departure from
 * 1:1 timing fidelity, playtester-reported and diagnosed (not guessed):
 * hop's own 200ms covers the identical 5px range and reads smooth, but
 * sink's original 500ms (2.5x longer for the *same* pixel range) visibly
 * juddered on real hardware. This engine's achievable redraw rate is fixed
 * regardless of an animation's own duration, so stretching the same few
 * discrete pixel steps over more real time just means each step lingers
 * on screen longer before advancing -- slow motion needs a *higher*
 * sample rate to look smooth than fast motion does, which this hardware
 * doesn't have headroom for. 300ms keeps sink noticeably slower/more
 * weighty than hop (preserving some of the real "droop" feel over an
 * identical bounce) while cutting the per-step linger enough to read as
 * smooth rather than stepped. */
#define SINK_PX      5
#define SINK_DOWN_MS 300
#define SINK_UP_MS   150

static int sink_offset(uint8_t character, bool sinking, unsigned t)
{
    static bool     was_sinking[VN_MAX_CHARS];
    static unsigned changed_at[VN_MAX_CHARS];

    int old_rest = was_sinking[character] ? SINK_PX : 0;
    if (sinking != was_sinking[character]) {
        was_sinking[character] = sinking;
        changed_at[character] = t;
    }
    int new_rest = sinking ? SINK_PX : 0;
    unsigned dur = sinking ? SINK_DOWN_MS : SINK_UP_MS;
    if (t - changed_at[character] < dur) {
        anim_moving = true;
    }

    return new_rest + ease(ease_remain, old_rest - new_rest, t,
                           changed_at[character], dur);
}

static void draw_background(uint8_t bg)
{
    /* assets_scene() copies exactly SCENE_H rows of SCREEN_W palette-index
     * bytes -- background art is always scaled to this fixed size at import
     * time (image_resolve.py's BG_SIZE/CG_SIZE) -- straight into the draw
     * buffer, so there's no separate scratch copy or blit step. */
    if (bg == VN_NO_SPRITE || !assets_scene(bg, (uint8_t *)gfx_vbuffer)) {
        gfx_SetColor(COL_BLACK);
        gfx_FillRectangle_NoClip(0, 0, SCREEN_W, SCENE_H);

        /* TEMP diagnostic for the still-open "New Game shows a black
         * screen" bug -- draw_background() has no other way to surface
         * *why* the real background didn't render (VN_NO_SPRITE, meaning
         * scene->background was never set to a real id, vs. a specific id
         * that assets_scene() itself couldn't resolve/open). Remove once
         * that bug is root-caused. */
        char diag[24];
        sprintf(diag, bg == VN_NO_SPRITE ? "bg=NONE (0xFF)" : "bg=%u (load failed)", bg);
        gfx_SetTextFGColor(COL_WHITE);
        gfx_PrintStringXY(diag, 4, 4);
    }
}

/* Where a sprite canvas's bottom edge rests. DDLC's character transforms all
 * carry `yanchor 1.0, ypos 1.03` (confirmed in transforms.rpy -- tcommon,
 * focus, hop and sink every one of them), i.e. the canvas bottom sits 3% of
 * the screen height *below* the bottom of the screen, so a character's lower
 * body is deliberately cut off. Here that overshoot lands under the dialogue
 * box, which render_box() paints over afterwards.
 *
 * SINK_PX/HOP_PX are the same 0.25 scaling of the same source numbers, so
 * they compose with this correctly: sink's ypos 1.03 -> 1.06 is exactly
 * +5px from here, and hop's yoffset -20 exactly -5px.
 *
 * Anchoring at SCENE_H instead (as this used to) squeezed every character
 * entirely above the box, which is not how the game frames them -- see
 * tools/image_resolve.py's SPRITE_SCALE for the other half of that fix. */
#define ACTOR_BASELINE ((SCENE_H * 103) / 100)

/** Decodes vn_actor_t.pos (half the screen-space center X) back to a real
 * screen X. Not a bucketed anchor -- see vn.h and compile_script.py's
 * _pos_from_x. */
static int pos_center(uint8_t pos)
{
    return (int)pos * 2;
}

static void draw_actor(const vn_actor_t *actor, unsigned t)
{
    int center_x = pos_center(actor->pos);
    bool zoom_wanted = (actor->flags & VN_FLAG_ZOOM) != 0;

    /* Called every frame regardless of whether the real zoom below actually
     * runs and succeeds this frame -- that's what lets it track the
     * zoomed/not-zoomed transition correctly and ease back out properly
     * even after a run of frames where the real zoom worked and this
     * return value went unused. */
    int fallback_off = zoom_fallback_offset(actor->character, zoom_wanted, t);

    /* The resting baseline, plus the one-shot hop if this Show authored one
     * and the persistent sink drift if it's currently sunk -- both the real
     * zoom and its fallback anchor from this same feet_y, so both apply
     * identically either way. */
    int feet_y = ACTOR_BASELINE;
    if (actor->flags & VN_FLAG_HOP) {
        feet_y += hop_offset(actor->character, actor->show_seq, t);
    }
    feet_y += sink_offset(actor->character, (actor->flags & VN_FLAG_SINK) != 0, t);

    /* Both of this character's layers are committed to the same fate here,
     * before either is drawn: preparing them separately let them disagree,
     * since the body atom is by far the bigger allocation and so the one
     * that failed under pressure, while the small expression atom right
     * after it succeeded -- drawing a 1.05x head on a 1.00x body. Deciding
     * up front makes that unreachable; the character zooms whole or not at
     * all.
     *
     * After the first frame of a line these are pure cache hits (see
     * assets.c's scaled-sprite cache), so this costs a lookup, not work. */
    bool zoom = zoom_wanted && assets_zoom_prepare(actor->sprite) &&
                (actor->overlay == VN_NO_OVERLAY ||
                 assets_zoom_prepare(actor->overlay));

    /* The real scale can still be unavailable on a real device (no room for
     * the bitmap alongside a large resident script chunk -- see assets.c),
     * which is why this doesn't just branch on zoom_wanted: fall back to the
     * plain draw, nudged by fallback_off, whenever the real scale didn't
     * actually happen, not only when it wasn't wanted.
     *
     * The plain (non-zoomed) draw can independently fail the exact same way
     * the zoom prepare above already accounts for -- the body atom is by
     * far the bigger allocation, so under memory pressure it's the one
     * that can lose the malloc() race while the small expression atom
     * right after it still succeeds. Unlike the zoom path (which commits
     * both layers to the same fate *before* drawing either, since it can
     * cheaply check affordability with assets_zoom_prepare()), there's no
     * equivalent cheap check for a plain draw -- so this reacts instead:
     * only attempt the overlay once the body has actually landed a pixel,
     * so a failed body always means a fully-skipped character for that
     * frame, never a floating head with no body under it. */
    bool body_drawn = zoom && assets_draw_sprite_zoomed(actor->sprite, center_x, feet_y);
    if (!body_drawn) {
        body_drawn = assets_draw_sprite(actor->sprite, center_x, feet_y + fallback_off);
    }

    /* Most actors are one flattened sprite (overlay == VN_NO_OVERLAY); a
     * layered one draws its expression atom second, at the same anchor --
     * its own (dx, dy) from DSPROFF is what places it correctly relative to
     * the body atom just drawn, at either scale. */
    if (body_drawn && actor->overlay != VN_NO_OVERLAY &&
        !(zoom && assets_draw_sprite_zoomed(actor->overlay, center_x, feet_y))) {
        assets_draw_sprite(actor->overlay, center_x, feet_y + fallback_off);
    }
}

/* ---------------------------------------------------------------------------
 * The moving-actor plate
 *
 * What made the hop/sink/rise animations stutter wasn't the easing -- it was
 * the frame rate underneath them. Every animating frame redrew the whole
 * scene, and the dominant cost there is draw_background(): a zx0 decode of
 * all 57,600 bytes of the scene area, dwarfing the sprite blits on top of
 * it. At that price only a handful of frames fit inside a 200ms hop, so a
 * motion with six distinct pixel positions got shown as two or three.
 *
 * Caching the decoded background outright was tried and reverted (commit
 * 5c5604a): at 57.6KB it does not fit beside the ~77KB draw buffer and a
 * resident script chunk. But a whole background is far more than an
 * animation needs. Only one actor moves, by a few pixels, so the only
 * region that can change is that actor's own rectangle.
 *
 * So: during a full redraw, once the background and every actor *behind*
 * the mover are down, copy that rectangle out. Subsequent frames paste it
 * back -- restoring background and rearward actors in one memcpy -- and
 * redraw the mover and whatever draws in front of it. No decode, no
 * full-scene pass, and z-order is preserved because the split is made at
 * the mover's own position in the draw order.
 *
 * The plate is best-effort like every other allocation here: if it can't be
 * had, animating frames just stay full redraws, which is what they were.
 * It's freed the moment the scene settles, so it holds nothing while the
 * player is simply reading.
 * ------------------------------------------------------------------------ */

/* Vertical slack around the resting sprite box, covering every offset the
 * three animations can add: hop reaches -HOP_PX, the fallback rise
 * -SPEAK_POP_PX, sink +SINK_PX, and they can stack. Rounded up so the
 * rectangle stays valid for the whole animation without being recaptured. */
#define PLATE_SLACK (HOP_PX + SPEAK_POP_PX + SINK_PX)

static uint8_t *plate;
static int      plate_x, plate_y, plate_w, plate_h;
static int      plate_slot = -1;   /* actor slot the plate was captured before */
static bool     plate_valid;

static void plate_free(void)
{
    free(plate);
    plate      = NULL;
    plate_valid = false;
    plate_slot  = -1;
}

/* Screen rectangle actor @p a can occupy over the course of an animation:
 * the union of its layers, at both scales (whether the zoom succeeds is
 * decided per frame, and the scaled draw is the larger but not a superset
 * -- it is centred differently), grown by PLATE_SLACK vertically.
 *
 * Measured from ACTOR_BASELINE rather than the actor's current animated
 * feet_y, so the answer doesn't depend on when during the animation this is
 * asked. */
static bool actor_rect(const vn_actor_t *a, int *rx, int *ry, int *rw, int *rh)
{
    int center_x = pos_center(a->pos);
    int x0 = SCREEN_W, y0 = SCREEN_H, x1 = 0, y1 = 0;
    uint16_t ids[2] = { a->sprite, a->overlay };

    for (int i = 0; i < 2; i++) {
        if (i == 1 && a->overlay == VN_NO_OVERLAY) {
            continue;
        }
        int w, h, dx, dy;
        if (!assets_sprite_bounds(ids[i], &w, &h, &dx, &dy)) {
            return false;
        }
        /* plain draw */
        int px = center_x - w / 2 + dx, py = ACTOR_BASELINE - h + dy;
        if (px < x0) x0 = px;
        if (py < y0) y0 = py;
        if (px + w > x1) x1 = px + w;
        if (py + h > y1) y1 = py + h;
        /* 1.05x draw -- assets.c's zoom_scale_dim/zoom_scale_off, inlined
         * here as the same 21:20 ratio rather than exported, since this only
         * needs a bound and not the exact pixel. */
        int zw = (w * 21 + 10) / 20, zh = (h * 21 + 10) / 20;
        int zdx = (dx >= 0) ? (dx * 21 + 10) / 20 : -((-dx * 21 + 10) / 20);
        int zdy = (dy >= 0) ? (dy * 21 + 10) / 20 : -((-dy * 21 + 10) / 20);
        px = center_x - zw / 2 + zdx;
        py = ACTOR_BASELINE - zh + zdy;
        if (px < x0) x0 = px;
        if (py < y0) y0 = py;
        if (px + zw > x1) x1 = px + zw;
        if (py + zh > y1) y1 = py + zh;
    }

    y0 -= PLATE_SLACK;
    y1 += PLATE_SLACK;

    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > SCREEN_W) x1 = SCREEN_W;
    /* Nothing below the scene area needs saving: render_box() repaints all
     * of y >= BOX_Y opaquely every frame regardless. */
    if (y1 > SCENE_H) y1 = SCENE_H;

    if (x1 <= x0 || y1 <= y0) {
        return false;
    }
    *rx = x0;
    *ry = y0;
    *rw = x1 - x0;
    *rh = y1 - y0;
    return true;
}

static void plate_blit(bool save)
{
    for (int r = 0; r < plate_h; r++) {
        uint8_t *fb = (uint8_t *)gfx_vbuffer + (size_t)(plate_y + r) * SCREEN_W + plate_x;
        uint8_t *st = plate + (size_t)r * plate_w;
        if (save) {
            memcpy(st, fb, (size_t)plate_w);
        } else {
            memcpy(fb, st, (size_t)plate_w);
        }
    }
}

/* Captures the plate for actor @p a, sizing (or resizing) the buffer to fit.
 * Call with the background and every rearward actor already drawn. */
static bool plate_capture(const vn_actor_t *a, int slot)
{
    int rx, ry, rw, rh;
    if (!actor_rect(a, &rx, &ry, &rw, &rh)) {
        return false;
    }

    if (plate == NULL || rw != plate_w || rh != plate_h) {
        free(plate);
        plate = malloc((size_t)rw * rh);
        if (plate == NULL) {
            plate_valid = false;
            plate_slot  = -1;
            return false;
        }
    }
    plate_x = rx;
    plate_y = ry;
    plate_w = rw;
    plate_h = rh;
    plate_slot = slot;
    plate_blit(true);
    return true;
}

/* ---------------------------------------------------------------------------
 * The tear glitch overlay (OP_TEAR_SHOW/OP_TEAR_HIDE)
 *
 * DDLC's real `tear` is a custom Python Displayable class (effects.rpy),
 * not preserved in any compiled .rpyc this engine reads -- there is no
 * exact pixel algorithm to recover, only the effect's name, its parameters
 * (a chunk count and a displacement range, timed by two multipliers), and
 * what "screen tearing" looks like as a genre convention. What follows is a
 * faithful reinterpretation on those terms, not a decompilation: the scene
 * area splits into horizontal bands, and each band independently redraws at
 * a fresh random horizontal offset every so often, wrapping at the screen
 * edges -- see tools/compile_script.py's _parse_tear_call() for how the
 * source call's arguments map onto this.
 * ------------------------------------------------------------------------ */

#define TEAR_MAX_CHUNKS 64  /* generous headroom over every real call site
                             * (8 or 20 chunks) -- a chunk count above this
                             * clamps rather than overflowing tear_offset[]. */

/* Independent of vn.c's OP_RANDOM state (vn_vm_t.rng_state) on purpose:
 * this drives a cosmetic screen effect with no bearing on story branching,
 * so it doesn't need to be part of the deterministic, host_sim-replayable
 * VM state the way a real gameplay draw does. Same xorshift32 construction
 * as vn.c's, seeded once at render_init() (tear_rng_state itself is
 * declared up near the includes -- render_init() runs before this section
 * would otherwise make it visible). */
static uint32_t tear_rand_next(void)
{
    uint32_t x = tear_rng_state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    tear_rng_state = x;
    return x;
}

static int16_t   tear_offset[TEAR_MAX_CHUNKS];
static unsigned  tear_reroll_at[TEAR_MAX_CHUNKS];
static bool      tear_was_on;

/* Applies the current tear state to the draw buffer's scene rows -- called
 * from render_scene() after the ordinary background+actor draw, so a
 * displaced band shows the just-drawn frame shifted, not stale content.
 * Every band re-rolls independently, so the bands drift out of sync with
 * each other over time, which reads as a genuine glitch rather than the
 * whole scene sliding as one piece. */
static void apply_tear(const vn_scene_t *scene, unsigned t)
{
    if (!scene->tear_on) {
        tear_was_on = false;
        return;
    }

    uint8_t chunks = scene->tear_chunks;
    if (chunks == 0) {
        return;
    }
    if (chunks > TEAR_MAX_CHUNKS) {
        chunks = TEAR_MAX_CHUNKS;
    }

    /* A band re-rolling the instant the effect turns on (rather than
     * waiting out whatever stale reroll time an earlier, unrelated glitch
     * left behind) is what makes it read as "just started" rather than
     * picking up mid-flicker from nothing. */
    if (!tear_was_on) {
        for (uint8_t i = 0; i < chunks; i++) {
            tear_reroll_at[i] = t;
        }
        tear_was_on = true;
    }

    int16_t span = scene->tear_offset_max - scene->tear_offset_min;
    int     rows_per_chunk = (SCENE_H + chunks - 1) / chunks;

    for (uint8_t i = 0; i < chunks; i++) {
        if (t >= tear_reroll_at[i]) {
            /* Magnitude uniform in [offset_min, offset_max], sign random --
             * DDLC's own call sites always pass offset_min=0, so without
             * randomizing the sign too every band would drift the same one
             * direction, reading as a slide rather than a tear. */
            int16_t mag = (span > 0)
                        ? (int16_t)(scene->tear_offset_min + (int16_t)(tear_rand_next() % (uint32_t)(span + 1)))
                        : scene->tear_offset_min;
            tear_offset[i] = (tear_rand_next() & 1) ? mag : (int16_t)-mag;
            tear_reroll_at[i] = t + scene->tear_period_ms;
        }

        int16_t off = tear_offset[i];
        if (off == 0) {
            continue;
        }
        /* off > 0 wraps a copy of the row from the left back in on the
         * right (and vice versa) rather than leaving a blank edge -- an
         * exposed solid-color strip would read as a rendering bug, not
         * part of the effect. */
        int y0 = i * rows_per_chunk;
        int y1 = y0 + rows_per_chunk;
        if (y1 > SCENE_H) {
            y1 = SCENE_H;
        }
        for (int y = y0; y < y1; y++) {
            uint8_t *row = (uint8_t *)gfx_vbuffer + (size_t)y * SCREEN_W;
            uint8_t  tmp[SCREEN_W];
            memcpy(tmp, row, SCREEN_W);
            for (int x = 0; x < SCREEN_W; x++) {
                int sx = x - off;
                sx %= SCREEN_W;
                if (sx < 0) {
                    sx += SCREEN_W;
                }
                row[x] = tmp[sx];
            }
        }
    }
}

/* ---------------------------------------------------------------------------
 * Public drawing
 * ------------------------------------------------------------------------ */

/* Whether the last render_scene() left every actor at rest, and whether one
 * has happened at all yet. Together these are what render_scene_lazy() reads
 * to decide it can skip -- see it below. */
static bool scene_settled;
static bool have_drawn_once;

/* Which actor slot moved during the last full redraw, or -1 if none did --
 * or if more than one did. The plate reconstructs exactly one moving actor,
 * so a second mover disables it and everything falls back to full redraws;
 * replaying Act 1 through host_sim finds no scene state where that happens
 * (at most one actor ever carries a zoom/hop/sink flag), but correctness
 * shouldn't rest on that holding for Acts 2 and 3 as well. */
static int mover_slot = -1;

/* Anything that paints over the composed scene -- a choice menu, the pause
 * overlay, a full-screen backdrop -- leaves the draw buffer no longer
 * showing the scene, so neither of render_scene_lazy()'s shortcuts is valid
 * afterwards: skipping would strand the overlay on screen, and the plate
 * would repair only one actor's rectangle of it.
 *
 * The overlay routines call this themselves rather than their callers doing
 * it, so a new one can't forget. It matters because the lazy path's other
 * gate is "has the scene settled?", and a player pauses or is offered a
 * choice precisely when it has -- an earlier version gated on wall-clock
 * time instead and papered over this by redrawing for 500ms regardless. */
static void scene_obscured(void);

void render_scene(const vn_scene_t *scene)
{
    draw_background(scene->background);
    anim_moving = false;

    /* Real elapsed time, not a frame count -- render_scene() is called from
     * many different loops (the typewriter reveal, idle waits, the pause
     * menu overlay) rather than one central per-frame driver like the title
     * screen has, so zoom_fallback_offset()/hop_offset() sample the clock
     * themselves here rather than threading a @p t parameter through every
     * one of those call sites. */
    unsigned t = (unsigned)(clock() * 1000UL / CLOCKS_PER_SEC);

    /* Capturing the plate needs the actor's rear neighbours already drawn
     * and the actor itself not yet -- so it happens mid-loop, at the slot
     * that moved last time. That the target comes from the *previous* full
     * redraw is why an animation's first frame or two are still full ones:
     * nothing knows who is moving until someone has moved. */
    int  capture_at = mover_slot;
    int  movers = 0;
    int  first_mover = -1;
    bool captured = false;

    for (int i = 0; i < VN_MAX_CHARS; i++) {
        const vn_actor_t *actor = &scene->actors[i];
        if (actor->character == VN_NO_SPRITE) {
            continue;
        }
        if (i == capture_at) {
            captured = plate_capture(actor, i);
        }

        bool was_moving = anim_moving;
        draw_actor(actor, t);
        if (!was_moving && anim_moving) {
            movers++;
            if (first_mover < 0) {
                first_mover = i;
            }
        }
    }

    mover_slot  = (movers == 1) ? first_mover : -1;
    plate_valid = captured && movers == 1 && first_mover == capture_at;

    apply_tear(scene, t);
    if (scene->tear_on) {
        /* Never settles while shown -- every band keeps re-rolling, so a
         * frame is never "the same as last time" the way idle actors are.
         * Also disqualifies the plate: apply_tear() touches rows across
         * the whole scene area, not one actor's rectangle, so the plate's
         * single-rectangle repair can't reconstruct it. */
        anim_moving = true;
        plate_valid = false;
    }

    scene_settled   = !anim_moving;
    have_drawn_once = true;
    if (scene_settled) {
        /* Reading, not animating -- give the memory back. The next
         * animation re-captures, and a chunk load in between (the largest
         * allocation this program makes) gets the room. */
        plate_free();
    }
}

/* The fast path: paste the plate back and redraw only from the moving actor
 * forward. Everything outside that rectangle is already correct in the draw
 * buffer, which render_present() keeps in step with the screen. */
static void render_scene_moving(const vn_scene_t *scene)
{
    if (plate == NULL || plate_slot < 0) {   /* can't happen; cheap insurance */
        render_scene(scene);
        return;
    }
    plate_blit(false);

    unsigned t = (unsigned)(clock() * 1000UL / CLOCKS_PER_SEC);
    anim_moving = false;

    for (int i = plate_slot; i < VN_MAX_CHARS; i++) {
        const vn_actor_t *actor = &scene->actors[i];
        if (actor->character != VN_NO_SPRITE) {
            draw_actor(actor, t);
        }
    }

    scene_settled = !anim_moving;
    if (scene_settled) {
        plate_free();
    }
}

static void scene_obscured(void)
{
    have_drawn_once = false;
    plate_free();
}

void render_invalidate_scene(void)
{
    /* Same mechanism as scene_obscured() above, minus plate_free(): nothing
     * about actor animation changed, so there's no plate to release -- just
     * force the next render_scene_lazy() call past its "already correct,
     * skip" shortcut. See its own doc comment in render.h for why this
     * exists (src/cgpack.c's availability changing mid-scene). */
    have_drawn_once = false;
}

void render_scene_lazy(const vn_scene_t *scene)
{
    if (!have_drawn_once || !scene_settled) {
        if (plate_valid) {
            render_scene_moving(scene);
        } else {
            render_scene(scene);
        }
        return;
    }

    /* Nothing left to animate, and nothing else could have changed the
     * scene without going through render_scene() first (a real Show/Scene/
     * Hide always redraws via host_update() in main.c) -- so the draw
     * buffer, as of the last render_present(), already holds exactly this
     * frame's correct pixels: render_present()'s gfx_SwapDraw() followed
     * immediately by gfx_BlitScreen() re-syncs the (new) draw buffer from
     * the (new) visible screen every single call, so the two never drift
     * apart. Skipping straight to nothing here avoids re-decompressing the
     * background (a real zx0 decode, not free) and redrawing every actor
     * (AppVar opens plus, for a zoomed one, a real decode/resample) for a
     * frame that would come out pixel-identical anyway -- the difference
     * between "laggy" and not in a scene with several actors on screen at
     * once, where this used to happen on every single typewriter tick and
     * every idle frame spent just waiting for the player to read.
     *
     * The gate is scene_settled rather than "has the longest possible
     * transition's worth of wall-clock time passed since the last real
     * draw?", which is how this first shipped: that spent a fixed 500ms of
     * full redraws after *every* Show, including the overwhelming majority
     * where nothing was easing at all and the very first frame was already
     * final. Asking the offset helpers directly costs nothing and settles
     * on the next frame in that case. */
}

/* Copies a @p w x @p h raw palette-index image (row-major, no stride gaps)
 * into the draw buffer with its top-left at (@p x, @p y). Used for both the
 * textbox and namebox art below -- neither needs graphx's own sprite/rlet
 * machinery, since both are always drawn axis-aligned with no transparency
 * to skip. */
static void blit_raw(int x, int y, const uint8_t *src, int w, int h)
{
    for (int r = 0; r < h; r++) {
        uint8_t *fb = (uint8_t *)gfx_vbuffer + (size_t)(y + r) * SCREEN_W + x;
        memcpy(fb, src + (size_t)r * w, w);
    }
}

void render_box(const vn_scene_t *scene, const char *speaker,
                const char *text, size_t visible)
{
    /* `window hide` -- DDLC uses this for a beat with no text over a clean
     * shot of the scene. Filled black rather than left untouched: nothing
     * else in this program ever draws y >= SCENE_H, so a stale frame's
     * pixels (or worse, whatever happened to be in the draw buffer before
     * gfx_Begin() cleared it) would otherwise show through the very first
     * time this runs. Not the same effect as real Ren'Py's window hide --
     * see vn_scene_t.window_hidden's own comment for why this engine can't
     * expand the scene into that space instead. */
    if (scene->window_hidden) {
        gfx_SetColor(COL_BLACK);
        gfx_FillRectangle_NoClip(0, BOX_Y, SCREEN_W, BOX_H);
        return;
    }

    const int pad = 6;

    /* The real textbox/namebox art (tools/image_resolve.py's ui_box_art()),
     * loaded once and cached here rather than re-read from the AppVar every
     * call -- render_box() runs on every typewriter tick, so this avoids
     * paying that (cheap, but nonzero) ti_Open/memcpy cost dozens of times
     * per line. Falls back to the old flat-rectangle box when the AppVar is
     * missing (an older raw DDLC copy this port's own asset pipeline
     * couldn't find gui/textbox.png in -- see ui_box_art()'s own
     * "not found" degrade) rather than drawing nothing. */
    static uint8_t textbox_px[TEXTBOX_W * TEXTBOX_H];
    static uint8_t namebox_px[NAMEBOX_W * NAMEBOX_H];
    static bool    box_art_loaded, have_textbox, have_namebox;
    if (!box_art_loaded) {
        have_textbox   = assets_textbox(textbox_px);
        have_namebox   = assets_namebox(namebox_px);
        box_art_loaded = true;
    }

    if (have_textbox) {
        blit_raw(0, BOX_Y, textbox_px, TEXTBOX_W, TEXTBOX_H);
    } else {
        gfx_SetColor(COL_BOX_FILL);
        gfx_FillRectangle_NoClip(0, BOX_Y, SCREEN_W, BOX_H);
        gfx_SetColor(COL_BOX_EDGE);
        gfx_HorizLine_NoClip(0, BOX_Y, SCREEN_W);
    }

    int y = BOX_Y + 4;

    if (speaker != NULL) {
        /* Pokes up above BOX_Y into the scene area, matching the real
         * game's namebox -- safe despite render_scene_lazy()'s "plate"
         * redraw-avoidance machinery assuming only actors touch y <
         * SCENE_H: render_box() (this function) still runs unconditionally
         * every single frame, right after render_scene()/render_scene_lazy()
         * in every call site, and repaints this exact rectangle every time
         * regardless of which path the scene took -- so whatever stale
         * pixels the plate mechanism captured or restored underneath it
         * are always immediately painted over again before the frame is
         * presented. */
        if (have_namebox) {
            const int nb_x = pad, nb_y = BOX_Y - NAMEBOX_H + 4;
            blit_raw(nb_x, nb_y, namebox_px, NAMEBOX_W, NAMEBOX_H);
            print_slice_outlined(speaker, strlen(speaker), nb_x + 8, nb_y + 4,
                                 COL_WHITE, COL_NAME);
            y = nb_y + NAMEBOX_H + 2;
        } else {
            print_slice_outlined(speaker, strlen(speaker), pad, y,
                                 COL_WHITE, COL_NAME);
            y += 11;
        }
    }

    /* text_wrap() re-measures every character-width prefix of every line it
     * builds -- real work, not free, and this used to run in full on every
     * single typewriter tick even though only @p visible had changed since
     * the last call. Caching the wrap by @p text's own pointer identity
     * turns that into a one-time cost per line: @p text is always
     * scene->text, a stable zero-copy pointer for the whole typewriter
     * reveal of one line (see assets_string()/host_say()) -- it only
     * changes when a genuinely new line starts, which is exactly when the
     * cache should invalidate. */
    static const char   *cached_text;
    static text_layout_t cached_full;
    if (text != cached_text) {
        text_wrap(&cached_full, text, SCREEN_W - 2 * pad, measure, NULL);
        cached_text = text;
    }

    text_layout_t shown;
    if (visible >= cached_full.total) {
        shown = cached_full;
    } else {
        text_clamp(&shown, &cached_full, visible);
    }

    for (uint8_t i = 0; i < shown.count; i++) {
        print_slice_outlined(shown.lines[i].start, shown.lines[i].len, pad, y,
                             COL_WHITE, COL_BLACK);
        y += 10;
    }
}

void render_menu(const char *const *choices, uint8_t count, uint8_t selected)
{
    scene_obscured();
    const int h    = 14;
    const int w    = SCREEN_W - 60;
    const int x    = 30;
    const int top  = (SCENE_H - count * h) / 2;

    for (uint8_t i = 0; i < count; i++) {
        const int y = top + i * h;

        gfx_SetColor(i == selected ? COL_HIGHLIGHT : COL_BOX_FILL);
        gfx_FillRectangle(x, y, w, h - 2);
        gfx_SetColor(COL_BOX_EDGE);
        gfx_Rectangle(x, y, w, h - 2);

        gfx_SetTextFGColor(i == selected ? COL_BLACK : COL_WHITE);
        gfx_PrintStringXY(choices[i], x + 6, y + 3);
    }
}

void render_present(uint8_t trans)
{
    /* TRANS_FADE is accepted but still presents as a cut; a real fade needs
     * the palette-ramp pass that lands with the asset pipeline. */
    (void)trans;

    gfx_SwapDraw();
    gfx_BlitScreen();
}

/* ---------------------------------------------------------------------------
 * Title / pause / menu screens
 * ------------------------------------------------------------------------ */

void render_backdrop(uint8_t color)
{
    scene_obscured();
    gfx_SetColor(color);
    gfx_FillRectangle_NoClip(0, 0, SCREEN_W, SCREEN_H);
}

void render_text(const char *s, int x, int y, uint8_t color)
{
    gfx_SetTextFGColor(color);
    gfx_PrintStringXY(s, x, y);
}

void render_text_centered(const char *s, int y, uint8_t color)
{
    render_text(s, (SCREEN_W - (int)gfx_GetStringWidth(s)) / 2, y, color);
}

void render_list_menu(const char *const *items, uint8_t count, uint8_t selected,
                      int x, int y, uint8_t normal_color, uint8_t selected_color)
{
    const int line_h = 16;

    for (uint8_t i = 0; i < count; i++) {
        bool is_selected = i == selected;
        uint8_t color = is_selected ? selected_color : normal_color;
        int line_y = y + i * line_h;

        gfx_SetTextFGColor(color);
        gfx_PrintStringXY(is_selected ? ">" : " ", x, line_y);
        gfx_PrintStringXY(items[i], x + 10, line_y);
    }
}

void render_list_menu_bar(const char *const *items, uint8_t count, uint8_t selected,
                          int x, int y, int w, uint8_t text_color,
                          uint8_t bar_color, uint8_t sel_text_color)
{
    const int line_h = 18;
    const int pad    = 6;

    for (uint8_t i = 0; i < count; i++) {
        bool is_selected = i == selected;
        int line_y = y + i * line_h;

        if (is_selected) {
            render_fill_rect(x - pad, line_y - 3, w, line_h - 3, bar_color);
        }
        gfx_SetTextFGColor(is_selected ? sel_text_color : text_color);
        gfx_PrintStringXY(items[i], x, line_y);
    }
}

void render_fill_rect(int x, int y, int w, int h, uint8_t color)
{
    scene_obscured();
    gfx_SetColor(color);
    gfx_FillRectangle_NoClip(x, y, w, h);
}

/* ---------------------------------------------------------------------------
 * Title screen
 *
 * Reproduces DDLC's main menu: the tiling background scrolls forever, and a
 * one-shot entrance slides the cast up, the nav panel in from the left, and
 * bounces the logo down from above. DDLC's own entrance runs ~3.45s; the
 * timings below keep its relative shape and land close to that same length --
 * compressing it further read as elements "popping" into place rather than
 * animating, since eased motion needs enough real time on screen to actually
 * show the curve instead of jumping most of the way there in a couple of
 * frames.
 *
 * @p t (render_title_screen's parameter) is real elapsed milliseconds, not a
 * frame count. An earlier version counted rendered frames instead, on the
 * assumption that frames arrive at a roughly fixed rate -- they don't: the
 * title screen's per-frame cost (a full background copy plus five sprite
 * draws) is heavy enough that actual frame time varies, and a fixed-duration
 * animation window (e.g. the nav panel's slide) could span fewer real frames
 * than it had steps, jumping straight from "not started" to "done" between
 * two consecutive draws. Driving every curve off wall-clock time instead
 * means each frame shows the position correct for the moment it was drawn,
 * however many or few frames that turns out to be.
 *
 * DDLC also zooms the cast during the entrance; that's deliberately dropped.
 * graphx has no cheap runtime scaler for rlet sprites, and baking a second
 * set at 0.75 would cost ~35KB to produce what would read as a jump cut. The
 * concurrent horizontal slide already carries the "settling into place" feel.
 * ------------------------------------------------------------------------ */

/* Keyframes, in milliseconds since the intro started -- roughly 5x the
 * original compressed values, landing the whole entrance (cast slide, the
 * last element to settle) around 3.5s, matching DDLC's own pacing. */
#define F_CAST_RISE_AT    250
#define F_CAST_RISE_DUR  2250
#define F_NAV_AT          500
#define F_NAV_DUR        1750
#define F_CAST_SLIDE_AT  1500
#define F_CAST_SLIDE_DUR 2000
#define F_LOGO_AT        1000
#define F_LOGO_DUR       2500

/* Nav panel, in the calculator's own units rather than scaled from DDLC's
 * 310px: it has to hold "Load Game", and DDLC's proportional width (77px)
 * doesn't. The 90/10 fill-to-edge split matches the source overlay's. */
#define NAV_W       84
#define NAV_EDGE_W   8
#define NAV_SLIDE   125   /* offscreen start, DDLC slides it in from -500 */
#define MENU_X        5
#define MENU_Y      120
#define MENU_LINE_H  16

/** Draws title art @p id at its resting spot plus the fraction of its
 * entrance offset still left to travel. @p dx_left / @p dy_left are in
 * 1/256ths: 256 = fully displaced, 0 = arrived. */
static void draw_title_art(uint8_t id, int dx_left, int dy_left)
{
    int x, y, dx, dy;
    if (assets_title_layout(id, &x, &y, &dx, &dy)) {
        assets_draw_title(id, x + dx * dx_left / 256, y + dy * dy_left / 256);
    }
}

void render_title_screen(uint8_t selected, unsigned t)
{
    scene_obscured();
    static const char *const items[] = { "New Game", "Load Game", "Help", "Quit" };
    const uint8_t count = sizeof(items) / sizeof(items[0]);

    /* Perpetual diagonal scroll, independent of the one-shot entrance above
     * -- callers keep @p t growing past the intro's end for exactly this.
     * DDLC runs 100 canvas px over 3000ms, which is 25 calculator px, i.e.
     * one px every 120ms. Both axes share a phase because the pattern is
     * symmetric under an equal x/y shift. */
    uint8_t phase = (uint8_t)((t / 120) % 50);
    if (!assets_title_bg(phase, phase, (uint8_t *)gfx_vbuffer)) {
        render_backdrop(COL_WHITE);
    }

    /* Cast rises while sliding horizontally into place; the per-element
     * amplitudes are baked by the importer (assets_title_layout). */
    int rise  = ease(ease_remain, 256, t, F_CAST_RISE_AT, F_CAST_RISE_DUR);
    int slide = ease(ease_remain, 256, t, F_CAST_SLIDE_AT, F_CAST_SLIDE_DUR);

    /* Back of DDLC's z-order: these two sit behind the nav panel. */
    draw_title_art(TITLE_YURI, slide, rise);
    draw_title_art(TITLE_NATSUKI, slide, rise);

    /* Nav panel + menu, sliding in from the left. */
    int nav_x = -ease(quint_remain, NAV_SLIDE, t, F_NAV_AT, F_NAV_DUR);

    gfx_SetColor(COL_NAV_FILL);
    gfx_FillRectangle(nav_x, 0, NAV_W - NAV_EDGE_W, SCREEN_H);
    gfx_SetColor(COL_NAV_EDGE);
    gfx_FillRectangle(nav_x + NAV_W - NAV_EDGE_W, 0, NAV_EDGE_W, SCREEN_H);

    for (uint8_t i = 0; i < count; i++) {
        int y = MENU_Y + i * MENU_LINE_H;
        if (i == selected) {
            gfx_SetColor(COL_NAV_EDGE);
            gfx_FillRectangle(nav_x, y - 2, NAV_W, MENU_LINE_H - 2);
        }
        gfx_SetTextFGColor(COL_BOX_FILL);
        gfx_PrintStringXY(items[i], nav_x + MENU_X, y);
    }

    /* Logo bounce-drops from above -- over the nav panel but under the front
     * pair, matching DDLC's own layering. */
    draw_title_art(TITLE_LOGO, 0,
                   ease(bounce_remain, 256, t, F_LOGO_AT, F_LOGO_DUR));

    /* Frontmost pair. */
    draw_title_art(TITLE_SAYORI, slide, rise);
    draw_title_art(TITLE_MONIKA, slide, rise);

    assets_title_end();
}

/* ---------------------------------------------------------------------------
 * Fade transitions
 *
 * DDLC's scene-level transitions (transforms.rpy's `dissolve_scene_full` and
 * `wipeleft_scene`) are all the same shape: dissolve out to a Solid("#000"),
 * hold, then dissolve back in. On an 8bpp display that costs nothing to
 * reproduce -- ramping the palette toward black darkens the image already on
 * screen without touching a single pixel of the framebuffer, so a fade is
 * 256 palette writes per step rather than a full redraw.
 *
 * The displayed buffer is deliberately left alone during a fade-out: it still
 * holds the *previous* scene, which is exactly what should be fading away.
 * ------------------------------------------------------------------------ */

#define FADE_STEP_MS 25   /* ~16 steps/sec, close to DDLC's 1.0s dissolve */
#define FADE_STEPS   16
#define FADE_HOLD_MS 250  /* DDLC's Pause() between the two dissolves */

static uint16_t fade_saved[256];

static void fade_apply(uint8_t amount)
{
    /* gfx_Darken: 0 is full black, 255 is the original color. */
    for (unsigned i = 0; i < 256; i++) {
        gfx_palette[i] = gfx_Darken(fade_saved[i], amount);
    }
}

/* Paced with msleep(), not gfx_Wait(): gfx_Wait() only blocks on a pending
 * gfx_SwapDraw(), and a fade changes no pixels -- it only re-tints the
 * palette of whatever is already on screen -- so nothing here ever swaps.
 * Without a real delay the whole fade (and the black hold) would collapse to
 * a handful of back-to-back palette writes with no visible time passing. */
void render_fade_out(void)
{
    memcpy(fade_saved, gfx_palette, sizeof(fade_saved));

    for (unsigned s = 1; s <= FADE_STEPS; s++) {
        fade_apply((uint8_t)(255 - s * 255 / FADE_STEPS));
        msleep(FADE_STEP_MS);
    }
    msleep(FADE_HOLD_MS);
}

void render_fade_in(void)
{
    for (unsigned s = 1; s <= FADE_STEPS; s++) {
        fade_apply((uint8_t)(s * 255 / FADE_STEPS));
        msleep(FADE_STEP_MS);
    }
    /* Restore exactly rather than trusting gfx_Darken(c, 255) to round-trip. */
    memcpy(gfx_palette, fade_saved, sizeof(fade_saved));
}

void render_apply_palette(const uint16_t *palette)
{
    memcpy(gfx_palette, palette, 256 * sizeof(uint16_t));
}

void render_fade_retarget(const uint16_t *palette)
{
    memcpy(fade_saved, palette, sizeof(fade_saved));
    fade_apply(0); /* re-hold at full black under the new palette's values */
}

void render_pause_box(int x, int y, int w, int h, uint8_t fill_color, uint8_t edge_color)
{
    scene_obscured();
    gfx_SetColor(fill_color);
    gfx_FillRectangle_NoClip(x, y, w, h);
    gfx_SetColor(edge_color);
    gfx_Rectangle_NoClip(x, y, w, h);
}
