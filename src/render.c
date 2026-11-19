#include "noise.h"
#include "ch5_bands.h"
#include "cgpack.h"
/**
 * @file render.c
 * @brief Scene composition via graphx. See render.h.
 */

#include "assets.h"
#include "chars.h"
#include "render.h"
#include "save.h"
#include "text.h"
#include "fast_ops.h"

#include <fontlibc.h>
#include <graphx.h>
#include <sys/timers.h>
#include "perf_overlay.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include "perf_memory.h"
#include <string.h>
#include <time.h>

/* Seeded by render_init(), used by the tear effect section further down --
 * forward-declared here since render_init() runs before that section's own
 * declaration would otherwise be visible. */
/* Forward-declared above render_box(), which inspects the active palette
 * before the fade/palette section below. */
static bool palette_entry_is_near_white(uint8_t index);

static uint32_t tear_rng_state;

/* Forward-declared for the same reason as tear_rng_state above -- defined
 * in the "Text measurement" section further down, but render_init() needs
 * to call it before that section's own declaration would otherwise be
 * visible. */
static void set_font(uint8_t index);

void render_init(void)
{
    /* No gfx_SetDefaultPalette() here: assets_init() already loaded the
     * real 256-entry palette (including the fixed UI indices 0..7) into
     * gfx_palette before this runs, and SetDefaultPalette would overwrite
     * the whole thing with a generic gradient. */
    gfx_Begin();
    gfx_SetDrawBuffer();
    /* Independent of the text setup below -- a separate graphx global. Not
     * actually read by assets_draw_sprite_zoomed() (it checks the
     * transparent index directly rather than going through a
     * gfx_TransparentSprite()-style call), but set here once anyway to keep
     * this convention fixed and documented in one place. */
    gfx_SetTransparentColor(COL_TRANSPARENT);

    /* fontlibc, not graphx's own built-in font -- see set_font()'s own
     * comment for why DFONTS ships two real DDLC fonts (Aller_Rg, index 0;
     * RifficFree-Bold, index 1) instead. Transparency here is the fontlibc
     * equivalent of the old gfx_SetTextTransparentColor()/SetTextBGColor()
     * pair: skip drawing a glyph's "off" pixels rather than filling them
     * with a background color, so text draws over whatever's already
     * there. Index 0 (Aller_Rg) is the default/body-text font; the namebox
     * is the one place that switches to index 1, and switches back
     * immediately after (see render_box()). */
    fontlib_SetTransparency(true);
    set_font(0);

    /* Seeds the tear effect's own PRNG -- see its section below for why
     * this doesn't share vn.c's OP_RANDOM state. 0 remapped to a fixed
     * nonzero fallback for the same reason vn_init() does: xorshift32
     * can't recover from an all-zero state. */
    uint32_t seed = (uint32_t)clock();
    tear_rng_state = seed ? seed : 0x9E3779B9u;
}

void render_end(void)
{
    perf_overlay_shutdown();
    gfx_End();
}

/* ---------------------------------------------------------------------------
 * Text measurement
 * ------------------------------------------------------------------------ */

/* DFONTS ships two of DDLC's own real bundled fonts, at the two indices
 * below -- not graphx's built-in font. Both are extracted fresh from the
 * user's own legally obtained copy of the game every build, never
 * committed to this repo (see tools/extract.py's own docstring):
 *   0 = Aller_Rg.ttf -- DDLC's actual gui.default_font: dialogue,
 *       narration, menus, everywhere except the namebox below.
 *   1 = RifficFree-Bold.ttf -- DDLC's actual gui.name_font, i.e. its real
 *       namebox font.
 * tools/convert_fonts.py builds the pack; index order there is index order
 * here, not looked up by name on-calc. */
#define FONT_DEFAULT 0
#define FONT_NAME    1

static fontlib_font_t *fonts[4];
static bool fonts_probed;
static fontlib_font_t *active_font = NULL;
static uint8_t active_font_index;

static void ensure_fonts(void)
{
    if (!fonts_probed) {
        fonts_probed = true;
        for (uint8_t i = 0; i < 4; i++) {
            fonts[i] = fontlib_GetFontByIndex("DFONTS", i);
        }
    }
}

static void set_font(uint8_t index)
{
    ensure_fonts();
    fontlib_font_t *f = (index < 4) ? fonts[index] : NULL;
    if (f) {
        fontlib_SetFont(f, 0);
        active_font = f;
        active_font_index = index;
    }
}

/* fontlib_GetStringWidthL exists and would save the loop, but its stop-code
 * handling isn't verified against a non-NUL-terminated slice (this measures
 * substrings of a larger buffer, not always a real C string) -- summing
 * per-glyph widths costs nothing extra at this scale and sidesteps needing
 * to find out the hard way. */
static unsigned measure(void *ctx, const char *str, size_t len)
{
    (void)ctx;
    if (!active_font) {
        return (unsigned)len * 8;
    }

    unsigned w = 0;
    for (size_t i = 0; i < len; i++) {
        uint8_t c = (uint8_t)str[i];
        if (c >= 32 && c <= 126) {
            w += fontlib_GetGlyphWidth(c);
        }
    }
    return w;
}

static unsigned string_width(const char *s)
{
    if (!active_font || !s) {
        return s ? (unsigned)strlen(s) * 8 : 0;
    }

    unsigned w = 0;
    for (const char *p = s; *p; p++) {
        uint8_t c = (uint8_t)*p;
        if (c >= 32 && c <= 126) {
            w += fontlib_GetGlyphWidth(c);
        }
    }
    return w;
}

/* Off-screen coordinate guards prevent unsigned 24-bit integer wrap into
 * arbitrary memory in fontlib. Zero-copy slice draw via fontlib_DrawStringL. */
static void print_slice(const char *str, size_t len, int x, int y)
{
    if (!active_font || !str || len == 0 || x < 0 || x >= SCREEN_W || y < 0 || y >= SCREEN_H) {
        return;
    }
    if (len > 255) {
        len = 255;
    }
    fontlib_SetCursorPosition((unsigned int)x, (uint8_t)y);
    fontlib_DrawStringL(str, len);
}

/* Outline masks are cardinal-dilated at asset build time (font indexes 2/3).
 * The +2 bitmap width is canceled by italic adjustment, so the cursor,
 * wrapping and typewriter prefixes match the original fill font exactly.
 * Old two-font bundles and edge coordinates retain the clipped fallback. */
static void print_slice_outlined(const char *str, size_t len, int x, int y,
                                 uint8_t fill, uint8_t outline)
{
    fontlib_font_t *expanded = (active_font_index < 2) ? fonts[active_font_index + 2] : NULL;
    if (expanded && x > 0 && y > 0 &&
        y + fontlib_GetCurrentFontHeight() + 1 < SCREEN_H &&
        ((x <= 12 && len <= 28) || x + (int)measure(NULL, str, len) + 1 < SCREEN_W)) {
        fontlib_font_t *fill_font = active_font;
        fontlib_SetFont(expanded, 0);
        fontlib_SetItalicSpacingAdjustment(2);
        fontlib_SetForegroundColor(outline);
        print_slice(str, len, x - 1, y - 1);
        fontlib_SetFont(fill_font, 0);
        fontlib_SetItalicSpacingAdjustment(0);
        fontlib_SetForegroundColor(fill);
        print_slice(str, len, x, y);
        return;
    }
    static const int8_t off[4][2] = {
                 {0, -1},
        {-1, 0},         {1, 0},
                 {0,  1},
    };
    fontlib_SetForegroundColor(outline);
    for (int i = 0; i < 4; i++) {
        print_slice(str, len, x + off[i][0], y + off[i][1]);
    }
    fontlib_SetForegroundColor(fill);
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
    static int      from_off[VN_MAX_CHARS];
    static int      to_off[VN_MAX_CHARS];

    if (zoomed != was_zoomed[character]) {
        from_off[character]   = was_zoomed[character] ? -SPEAK_POP_PX : 0;
        to_off[character]     = zoomed ? -SPEAK_POP_PX : 0;
        was_zoomed[character] = zoomed;
        changed_at[character] = t;
    }
    if (t - changed_at[character] < SPEAK_POP_MS) {
        anim_moving = true;
    }

    return to_off[character] + ease(ease_remain, from_off[character] - to_off[character],
                                    t, changed_at[character], SPEAK_POP_MS);
}

static unsigned zoom_scale_permille(uint8_t character, bool zoomed, unsigned t)
{
    static bool     was_zoomed[VN_MAX_CHARS];
    static unsigned changed_at[VN_MAX_CHARS];
    static int      from_scale[VN_MAX_CHARS];
    static int      to_scale[VN_MAX_CHARS];
    static bool     inited;

    if (!inited) {
        for (int i = 0; i < VN_MAX_CHARS; i++) {
            from_scale[i] = 1000;
            to_scale[i]   = 1000;
        }
        inited = true;
    }

    if (character >= VN_MAX_CHARS) {
        return zoomed ? 1050 : 1000;
    }

    if (zoomed != was_zoomed[character]) {
        int cur = to_scale[character] + ease(ease_remain, from_scale[character] - to_scale[character],
                                             t, changed_at[character], SPEAK_POP_MS);
        from_scale[character] = cur;
        to_scale[character]   = zoomed ? 1050 : 1000;
        was_zoomed[character] = zoomed;
        changed_at[character] = t;
    }
    if (t - changed_at[character] < SPEAK_POP_MS) {
        anim_moving = true;
    }

    return (unsigned)(to_scale[character] + ease(ease_remain, from_scale[character] - to_scale[character],
                                                 t, changed_at[character], SPEAK_POP_MS));
}

/* The real one-shot pulses (hop / dip) share one easing curve helper. */
#define HOP_PX     6
#define HOP_MS   200
#define DIP_PX     6
#define DIP_MS   500
#define LEFTIN_PX -75
#define LEFTIN_MS 250
#define POS_LERP_MS 150

static int pulse_y_offset(uint8_t character, uint8_t show_seq, uint8_t flags, unsigned t)
{
    static uint8_t  last_seq[VN_MAX_CHARS];
    static unsigned started_at[VN_MAX_CHARS];

    if (show_seq != last_seq[character]) {
        last_seq[character] = show_seq;
        started_at[character] = t;
    }

    unsigned dur = (flags & VN_FLAG_DIP) ? DIP_MS : HOP_MS;
    unsigned half = dur / 2;
    unsigned elapsed = t - started_at[character];
    if (elapsed >= dur) {
        return 0;
    }
    int peak = (flags & VN_FLAG_DIP) ? DIP_PX : -HOP_PX;
    anim_moving = true;
    if (elapsed < half) {
        return peak - ease(ease_remain, peak, t, started_at[character], half);
    }
    return ease(ease_remain, peak, t, started_at[character] + half, half);
}

/* Persistent sink drift: 500ms down (transforms.rpyc sink), 150ms up (tcommon/focus). */
#define SINK_PX      6
#define SINK_DOWN_MS 500
#define SINK_UP_MS   150

static int sink_offset(uint8_t character, bool sinking, unsigned t)
{
    static bool     was_sinking[VN_MAX_CHARS];
    static unsigned changed_at[VN_MAX_CHARS];
    static int      from_off[VN_MAX_CHARS];
    static int      to_off[VN_MAX_CHARS];
    static unsigned dur_ms[VN_MAX_CHARS];

    if (sinking != was_sinking[character]) {
        from_off[character]    = was_sinking[character] ? SINK_PX : 0;
        to_off[character]      = sinking ? SINK_PX : 0;
        dur_ms[character]      = sinking ? SINK_DOWN_MS : SINK_UP_MS;
        was_sinking[character] = sinking;
        changed_at[character]  = t;
    }
    if (t - changed_at[character] < dur_ms[character]) {
        anim_moving = true;
    }

    return to_off[character] + ease(ease_remain, from_off[character] - to_off[character],
                                    t, changed_at[character], dur_ms[character]);
}

/* Horizontal ease tracking for LEFTIN (-75 -> target over 250ms), pos changes
 * (150ms lerp unless VN_FLAG_INSTANT), and LHIDE exit snapshot in render_scene(). */
static bool     x_was_visible[VN_MAX_CHARS];
static int16_t  x_last_target[VN_MAX_CHARS];
static int16_t  x_delta[VN_MAX_CHARS];
static unsigned x_started_at[VN_MAX_CHARS];
static unsigned x_dur_ms[VN_MAX_CHARS];
static uint8_t  x_last_seq[VN_MAX_CHARS];

static int slide_x_pos(uint8_t character, uint8_t show_seq, int target_x,
                       uint8_t flags, unsigned t)
{
    int cur_x = target_x;
    if (x_dur_ms[character] > 0) {
        unsigned elapsed = t - x_started_at[character];
        if (elapsed < x_dur_ms[character]) {
            cur_x = (int)x_last_target[character] +
                    ease(ease_remain, x_delta[character], t,
                         x_started_at[character], x_dur_ms[character]);
        } else {
            x_dur_ms[character] = 0;
        }
    }

    if (!x_was_visible[character]) {
        x_was_visible[character] = true;
        x_last_target[character] = (int16_t)target_x;
        x_last_seq[character] = show_seq;
        if (flags & VN_FLAG_LEFTIN) {
            x_delta[character] = (int16_t)(LEFTIN_PX - target_x);
            x_started_at[character] = t;
            x_dur_ms[character] = LEFTIN_MS;
        } else {
            x_dur_ms[character] = 0;
        }
    } else if ((flags & VN_FLAG_LEFTIN) && x_last_seq[character] != show_seq) {
        x_last_target[character] = (int16_t)target_x;
        x_last_seq[character] = show_seq;
        x_delta[character] = (int16_t)(LEFTIN_PX - target_x);
        x_started_at[character] = t;
        x_dur_ms[character] = LEFTIN_MS;
    } else if (x_last_target[character] != target_x) {
        x_last_target[character] = (int16_t)target_x;
        x_last_seq[character] = show_seq;
        if (flags & VN_FLAG_INSTANT) {
            x_dur_ms[character] = 0;
        } else {
            x_delta[character] = (int16_t)(cur_x - target_x);
            x_started_at[character] = t;
            x_dur_ms[character] = POS_LERP_MS;
        }
    } else {
        x_last_seq[character] = show_seq;
    }

    if (x_dur_ms[character] > 0) {
        unsigned elapsed = t - x_started_at[character];
        if (elapsed < x_dur_ms[character]) {
            anim_moving = true;
            return target_x + ease(ease_remain, x_delta[character], t,
                                   x_started_at[character], x_dur_ms[character]);
        }
        x_dur_ms[character] = 0;
    }
    return target_x;
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

#ifdef DDLC_PERF_OVERLAY
static __attribute__((noinline)) void record_actor_sprite(uint8_t slot, bool is_face, uint16_t id)
{
    uint8_t v = 0; uint16_t o = 0, l = 0; uintptr_t a = 0;
    if (slot < VN_MAX_CHARS && assets_sprite_debug_info(id, &v, &o, &l, &a)) {
        perf_overlay_record_sprite(slot, is_face, id, v, o, l, a);
    }
}
#endif

typedef struct {
    int x0;
    int y0;
    int x1;
    int y1;
} rect_t;

typedef struct {
    bool     active;
    uint8_t  character;
    uint16_t sprite;
    uint16_t overlay;
    int      center_x;
    int      feet_y;
    unsigned scale_permille;
    rect_t   face_rect;
    rect_t   bounds;
} actor_pose_t;

static bool rect_empty(const rect_t *r)
{
    return r->x0 >= r->x1 || r->y0 >= r->y1;
}

static __attribute__((noinline)) void rect_union(rect_t *dst, const rect_t *src)
{
    if (rect_empty(src)) {
        return;
    }
    if (rect_empty(dst)) {
        *dst = *src;
        return;
    }
    if (src->x0 < dst->x0) {
        dst->x0 = src->x0;
    }
    if (src->y0 < dst->y0) {
        dst->y0 = src->y0;
    }
    if (src->x1 > dst->x1) {
        dst->x1 = src->x1;
    }
    if (src->y1 > dst->y1) {
        dst->y1 = src->y1;
    }
}

static __attribute__((noinline)) bool rect_overlaps(const rect_t *a, const rect_t *b)
{
    if (rect_empty(a) || rect_empty(b)) {
        return false;
    }
    return a->x0 < b->x1 && b->x0 < a->x1 &&
           a->y0 < b->y1 && b->y0 < a->y1;
}

static __attribute__((noinline)) int scale_val(int v, unsigned scale_permille)
{
    if (scale_permille <= 1000u || v == 0) {
        return v;
    }
    bool neg = v < 0;
    unsigned av = (unsigned)(neg ? -v : v);
    unsigned sv = (av * scale_permille + 500u) / 1000u;
    return neg ? -(int)sv : (int)sv;
}

static __attribute__((noinline)) bool sprite_screen_rect(uint16_t id, int center_x,
                                                         int feet_y,
                                                         unsigned scale_permille,
                                                         rect_t *out)
{
    int w;
    int h;
    int dx;
    int dy;
    if (!assets_sprite_bounds(id, &w, &h, &dx, &dy)) {
        return false;
    }

    int dst_w = scale_val(w, scale_permille);
    int dst_h = scale_val(h, scale_permille);
    int zdx = scale_val(dx, scale_permille);
    int zdy = scale_val(dy, scale_permille);

    int x0 = center_x - dst_w / 2 + zdx;
    int y0 = feet_y - dst_h + zdy;
    int x1 = x0 + dst_w;
    int y1 = y0 + dst_h;
    if (x0 < 0) {
        x0 = 0;
    }
    if (y0 < 0) {
        y0 = 0;
    }
    if (x1 > SCREEN_W) {
        x1 = SCREEN_W;
    }
    if (y1 > SCENE_H) {
        y1 = SCENE_H;
    }

    if (x0 >= x1 || y0 >= y1) {
        out->x0 = 0;
        out->y0 = 0;
        out->x1 = 0;
        out->y1 = 0;
    } else {
        out->x0 = x0;
        out->y0 = y0;
        out->x1 = x1;
        out->y1 = y1;
    }
    return true;
}

static __attribute__((noinline)) bool resolve_actor_pose_at(const vn_actor_t *actor,
                                                            int center_x, unsigned t,
                                                            bool with_bounds,
                                                            actor_pose_t *out)
{
    memset(out, 0, sizeof(*out));
    if (actor->character == VN_NO_SPRITE) {
        return true;
    }

    bool zoom_wanted = (actor->flags & VN_FLAG_ZOOM) != 0;
    unsigned scale_permille = zoom_scale_permille(actor->character, zoom_wanted, t);
    int fallback_off = zoom_fallback_offset(actor->character, zoom_wanted, t);

    int feet_y = ACTOR_BASELINE;
    if (actor->flags & (VN_FLAG_HOP | VN_FLAG_DIP)) {
        feet_y += pulse_y_offset(actor->character, actor->show_seq, actor->flags, t);
    }
    feet_y += sink_offset(actor->character, (actor->flags & VN_FLAG_SINK) != 0, t);

    bool can_zoom = assets_zoom_prepare(actor->sprite) &&
                    (actor->overlay == VN_NO_OVERLAY ||
                     assets_zoom_prepare(actor->overlay));

    out->active = true;
    out->character = actor->character;
    out->sprite = actor->sprite;
    out->overlay = actor->overlay;
    out->center_x = center_x;
    out->scale_permille = (can_zoom && scale_permille > 1000u)
                        ? scale_permille
                        : 1000u;
    out->feet_y = feet_y + ((!can_zoom) ? fallback_off : 0);

    if (!with_bounds) {
        return true;
    }
    if (!sprite_screen_rect(out->sprite, out->center_x, out->feet_y,
                            out->scale_permille, &out->bounds)) {
        return false;
    }
    if (out->overlay != VN_NO_OVERLAY) {
        if (!sprite_screen_rect(out->overlay, out->center_x, out->feet_y,
                                out->scale_permille, &out->face_rect)) {
            return false;
        }
        rect_union(&out->bounds, &out->face_rect);
    }
    return true;
}

static __attribute__((noinline)) bool resolve_actor_pose(const vn_actor_t *actor,
                                                         unsigned t,
                                                         actor_pose_t *out)
{
    if (actor->character == VN_NO_SPRITE) {
        memset(out, 0, sizeof(*out));
        return true;
    }
    int center_x = slide_x_pos(actor->character, actor->show_seq,
                               pos_center(actor->pos), actor->flags, t);
    return resolve_actor_pose_at(actor, center_x, t, true, out);
}

static __attribute__((noinline)) bool pose_body_equals(const actor_pose_t *a,
                                                       const actor_pose_t *b)
{
    if (!a->active || !b->active) {
        return false;
    }
    return a->character == b->character &&
           a->sprite == b->sprite &&
           a->center_x == b->center_x &&
           a->feet_y == b->feet_y &&
           a->scale_permille == b->scale_permille;
}

static bool pose_equals(const actor_pose_t *a, const actor_pose_t *b)
{
    if (!a->active && !b->active) {
        return true;
    }
    return pose_body_equals(a, b) && a->overlay == b->overlay;
}

static __attribute__((noinline)) bool draw_sprite_pose(uint16_t id, int center_x,
                                                       int feet_y,
                                                       unsigned scale_permille,
                                                       uint8_t slot, bool is_face)
{
    bool drawn = false;
    if (scale_permille > 1000u) {
        drawn = assets_draw_sprite_scaled(id, center_x, feet_y, scale_permille);
    }
    if (!drawn) {
        drawn = assets_draw_sprite(id, center_x, feet_y);
    }
#ifdef DDLC_PERF_OVERLAY
    if (drawn) {
        record_actor_sprite(slot, is_face, id);
    }
#else
    (void)slot;
    (void)is_face;
#endif
    return drawn;
}

static __attribute__((noinline)) void draw_actor_pose(const actor_pose_t *p,
                                                      uint8_t slot)
{
    if (!p->active) {
        return;
    }
    if (draw_sprite_pose(p->sprite, p->center_x, p->feet_y,
                         p->scale_permille, slot, false) &&
        p->overlay != VN_NO_OVERLAY) {
        draw_sprite_pose(p->overlay, p->center_x, p->feet_y,
                         p->scale_permille, slot, true);
    }
}

static __attribute__((noinline)) void draw_actor_at(const vn_actor_t *actor,
                                                    uint8_t slot, int center_x,
                                                    unsigned t)
{
    static actor_pose_t tmp_pose;
    resolve_actor_pose_at(actor, center_x, t, false, &tmp_pose);
    draw_actor_pose(&tmp_pose, slot);
}

static void draw_actor(const vn_actor_t *actor, uint8_t slot, unsigned t)
{
    int center_x = slide_x_pos(actor->character, actor->show_seq,
                               pos_center(actor->pos), actor->flags, t);
    draw_actor_at(actor, slot, center_x, t);
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
 * The moving-actor plate is that rectangle: a small malloc'd buffer captured
 * on the first frame an animation begins, after the background and all
 * rearward actors are drawn but *before* the moving actor is blitted. On
 * every subsequent frame of the animation the renderer doesn't touch the
 * background or rearward actors at all: it blits the plate back into the draw
 * buffer and repaints from the moving actor forward. When the motion ends
 * (the actor settles, or a new dialogue line arrives) the plate is freed.
 *
 * Bounds are computed conservatively: the bounding box of the actor's body
 * and overlay at both 1.00x and 1.05x, expanded by PLATE_SLACK on each edge
 * to cover the full range of the hop bounce and sink drift. That way one
 * capture covers the whole duration of the motion without having to re-plate
 * on every pixel of movement.
 * ------------------------------------------------------------------------- */

/* Legacy single-actor plate API retained as a no-op for render_timed_cg();
 * superseded by the general dirty-rect compositor in render_scene(). */
static void plate_free(void)
{
}

static bool     exit_active;
static vn_actor_t exit_actor;
static unsigned exit_started_at;
static int16_t  exit_from_x;

#define VN_OVL_PIXEL_MASK (VN_OVL_RECTS_N | VN_OVL_RECTS_M | \
                           VN_OVL_BLOOD | VN_OVL_BLOOD_EYE | VN_OVL_LIVE_TILE | \
                           VN_OVL_DUST)
static bool ovl_pixel_active;

static __attribute__((noinline)) void render_live_tile_bg(void);
static __attribute__((noinline)) void render_overlay_fx(const vn_scene_t *scene);
static bool layer_fx_active_pending(void);
static bool layer_fx_scene_pending(const vn_scene_t *scene, unsigned now_ms);
static __attribute__((noinline)) void apply_layer_fx(const vn_scene_t *scene, unsigned t);

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

#define TEAR_MAX_CHUNKS 32  /* headroom over real call sites (8 or 20 chunks)
                             * -- a chunk count above this clamps cleanly. */

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
static __attribute__((noinline)) void apply_tear(const vn_scene_t *scene, unsigned t)
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
    int     active_h = (scene->window_hidden && assets_scene_fullscreen(scene->background))
                     ? SCREEN_H : SCENE_H;
    int     rows_per_chunk = (active_h + chunks - 1) / chunks;
    extern uint8_t render_row_scratch[SCREEN_W];
    uint8_t *tear_scratch = render_row_scratch;

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
        /* Normalized to [0, SCREEN_W) once per chunk, not per pixel -- the
         * eZ80 has no hardware divider, so the % this used to do inside
         * the x loop below (up to SCREEN_W * rows_per_chunk times per
         * chunk per frame) was the actual cause of real, reported lag.
         * One % here instead is negligible. */
        int off_wrapped = (int)off % SCREEN_W;
        if (off_wrapped < 0) {
            off_wrapped += SCREEN_W;
        }
        /* off > 0 wraps a copy of the row from the left back in on the
         * right (and vice versa) rather than leaving a blank edge -- an
         * exposed solid-color strip would read as a rendering bug, not
         * part of the effect. */
        int y0 = i * rows_per_chunk;
        int y1 = y0 + rows_per_chunk;
        if (y1 > active_h) {
            y1 = active_h;
        }
        for (int y = y0; y < y1; y++) {
            uint8_t *row = (uint8_t *)gfx_vbuffer + (size_t)y * SCREEN_W;
            if (off_wrapped == 0) {
                continue;
            }
            fast_row_shift(row, tear_scratch, (size_t)off_wrapped);
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
static bool buffers_synced;
static bool dirty_sync_ok;
static int  dirty_y0 = SCREEN_H;
static int  dirty_y1 = 0;
static bool console_active;

/* Dirty-rect scene composition tracker. */
static bool         composed_valid;
static uint8_t      composed_bg = VN_NO_SPRITE;
static bool         prev_exit_active;
static actor_pose_t composed_actors[VN_MAX_CHARS];
static actor_pose_t cur_poses[VN_MAX_CHARS];

/* Textbox skip cache: tracks the last painted dialogue box so actor-only
 * updates and animations skip redrawing rows 180..239 when unchanged. */
static bool    box_valid;
static bool    box_namebox_dirty;
static bool    box_used_namebox_art;
static bool    box_last_hidden;
static uint8_t box_last_fill;
static bool    box_last_edited;
static bool    box_last_had_speaker;
static char    box_last_speaker[24];
static size_t  box_last_visible;

static __attribute__((noinline)) void mark_dirty_y(int y0, int y1);
static __attribute__((noinline)) bool draw_namebox_art(const char *speaker);

static __attribute__((noinline)) bool try_skip_visible_box(const vn_scene_t *scene,
                                                           const char *speaker,
                                                           bool text_same,
                                                           size_t eff_visible)
{
    bool fake_skip = (scene->overlay_fx_mask & VN_OVL_FAKE_SKIP) != 0;
    if (!box_valid || box_last_hidden || fake_skip ||
        !text_same || eff_visible != box_last_visible ||
        scene->text_edited != box_last_edited ||
        ((speaker != NULL) != box_last_had_speaker) ||
        (speaker != NULL && strcmp(speaker, box_last_speaker) != 0)) {
        return false;
    }
    if (!box_namebox_dirty) {
        return true;
    }
    if (speaker == NULL) {
        box_namebox_dirty = false;
        return true;
    }
    if (box_used_namebox_art && draw_namebox_art(speaker)) {
        box_namebox_dirty = false;
        mark_dirty_y(BOX_Y - NAMEBOX_H + 4, BOX_Y + 4);
        return true;
    }
    return false;
}

static __attribute__((noinline)) void mark_dirty_y(int y0, int y1)
{
    if (y0 < 0) {
        y0 = 0;
    }
    if (y1 > SCREEN_H) {
        y1 = SCREEN_H;
    }
    if (y0 >= y1) {
        return;
    }
    if (y0 < dirty_y0) {
        dirty_y0 = y0;
    }
    if (y1 > dirty_y1) {
        dirty_y1 = y1;
    }
}

static __attribute__((noinline)) void composite_dirty_rect(uint8_t bg,
                                                           const actor_pose_t *poses,
                                                           const rect_t *clip)
{
    if (rect_empty(clip)) {
        return;
    }
    perf_overlay_stage_begin(PERF_STAGE_BG);
    assets_scene_bg_rect(bg, clip->x0, clip->y0,
                         clip->x1 - clip->x0, clip->y1 - clip->y0,
                         (uint8_t *)gfx_vbuffer);
    perf_overlay_stage_end(PERF_STAGE_BG);

    assets_set_clip_rect(clip->x0, clip->y0, clip->x1, clip->y1);
    for (int i = 0; i < VN_MAX_CHARS; i++) {
        if (poses[i].active && rect_overlaps(&poses[i].bounds, clip)) {
            perf_overlay_stage_begin((uint8_t)(PERF_STAGE_ACT0 + i));
            draw_actor_pose(&poses[i], (uint8_t)i);
            perf_overlay_stage_end((uint8_t)(PERF_STAGE_ACT0 + i));
        }
    }
    assets_reset_clip_rect();
    assets_sprite_end();
    mark_dirty_y(clip->y0, clip->y1);

    /* If this dirty rect overlapped the namebox sliver above BOX_Y, flag
     * the namebox for repaint without invalidating the textbox below. */
    static const rect_t nb_sliver = { 6, BOX_Y - NAMEBOX_H + 4, 6 + NAMEBOX_W, SCENE_H };
    if (rect_overlaps(clip, &nb_sliver)) {
        box_namebox_dirty = true;
    }
}

static void scene_obscured(void);

static __attribute__((noinline)) unsigned ticks_to_ms(clock_t ticks)
{
    uint32_t dt = (uint32_t)ticks;
#ifdef __TICE__
    _Static_assert(CLOCKS_PER_SEC == 32768, "32-bit shift requires 32768Hz clock");
    return (unsigned)(((dt >> 15) * 1000UL) + (((dt & 0x7FFFUL) * 125UL) >> 12));
#else
    return (unsigned)(((uint64_t)dt * 1000ULL) / CLOCKS_PER_SEC);
#endif
}

static bool last_scene_used_motion;

static __attribute__((noinline)) bool try_dirty_rect_scene(const vn_scene_t *scene)
{
    rect_t dirty = { 0, 0, 0, 0 };
    uint8_t changed_count = 0;
    bool face_only = true;

    for (int i = 0; i < VN_MAX_CHARS; i++) {
        const actor_pose_t *old_p = &composed_actors[i];
        const actor_pose_t *new_p = &cur_poses[i];
        if (!pose_equals(old_p, new_p)) {
            changed_count++;
            if (pose_body_equals(old_p, new_p)) {
                if (old_p->overlay != VN_NO_OVERLAY) {
                    rect_union(&dirty, &old_p->face_rect);
                }
                if (new_p->overlay != VN_NO_OVERLAY) {
                    rect_union(&dirty, &new_p->face_rect);
                }
            } else {
                face_only = false;
                if (old_p->active) {
                    rect_union(&dirty, &old_p->bounds);
                }
                if (new_p->active) {
                    rect_union(&dirty, &new_p->bounds);
                }
            }
        }
    }

    (void)face_only;

    if (changed_count == 0) {
        perf_overlay_set_mode("SKIP");
        assets_sprite_end();
        scene_settled = !anim_moving;
        have_drawn_once = true;
        dirty_sync_ok = buffers_synced;
        return true;
    }

    unsigned dirty_area = rect_empty(&dirty)
                        ? 0u
                        : (unsigned)(dirty.x1 - dirty.x0) * (unsigned)(dirty.y1 - dirty.y0);
    if (changed_count != 1 && dirty_area >= 49000u) {
        return false;
    }

    perf_overlay_set_mode(face_only ? "FACE" : "DRTY");
    last_scene_used_motion = false;
    perf_overlay_clear_sprites();
    composite_dirty_rect(scene->background, cur_poses, &dirty);

    memcpy(composed_actors, cur_poses, sizeof(composed_actors));
    composed_bg = scene->background;
    composed_valid = true;
    prev_exit_active = false;
    dirty_sync_ok = buffers_synced;
    scene_settled = !anim_moving;
    have_drawn_once = true;
    return true;
}

void render_scene(const vn_scene_t *scene)
{
    perf_overlay_frame_start();
    assets_box_end();
    bool is_motion = ch5_motion_active(scene->background);
    if (is_motion) {
        noise_release();
        assets_sprite_cache_clear();
    } else {
        noise_restore();
    }

    ovl_pixel_active = (scene->overlay_fx_mask & VN_OVL_PIXEL_MASK) != 0;
    anim_moving = false;
    unsigned t = ticks_to_ms(clock());

    static uint8_t prev_bg = VN_NO_SPRITE;
    static uint8_t slot_char[VN_MAX_CHARS] = {
        VN_NO_SPRITE, VN_NO_SPRITE, VN_NO_SPRITE, VN_NO_SPRITE
    };
    bool bg_changed = (scene->background != prev_bg);
    prev_bg = scene->background;
    if (bg_changed) {
        exit_active = false;
        memset(x_was_visible, 0, sizeof(x_was_visible));
        memset(x_dur_ms, 0, sizeof(x_dur_ms));
    }

    for (int i = 0; i < VN_MAX_CHARS; i++) {
        uint8_t cur = scene->actors[i].character;
        if (slot_char[i] != VN_NO_SPRITE && cur == VN_NO_SPRITE) {
            uint8_t old_ch = slot_char[i];
            if (!bg_changed && (scene->actors[i].flags & VN_FLAG_LHIDE)) {
                exit_actor = scene->actors[i];
                exit_actor.character = old_ch;
                exit_from_x = x_last_target[old_ch];
                exit_started_at = t;
                exit_active = true;
            }
            x_was_visible[old_ch] = false;
            x_dur_ms[old_ch] = 0;
        }
        slot_char[i] = cur;
    }

    /* Resolve every actor's exact frame pose and bounding boxes first so all
     * animation timers advance identically whether we take the dirty-rect
     * fast path or fall back to a full redraw. */
    bool bounds_ok = true;
    for (int i = 0; i < VN_MAX_CHARS; i++) {
        if (!resolve_actor_pose(&scene->actors[i], t, &cur_poses[i])) {
            bounds_ok = false;
        }
    }

    bool fullscreen = assets_scene_fullscreen(scene->background);
    bool can_dirty = composed_valid &&
                     !bg_changed &&
                     scene->background == composed_bg &&
                     !is_motion &&
                     !ovl_pixel_active &&
                     !scene->tear_on &&
                     scene->layer_fx == VN_LAYER_NONE &&
                     !exit_active &&
                     !prev_exit_active &&
                     !fullscreen &&
                     bounds_ok &&
                     assets_scene_has_bg_rect(scene->background);

    if (can_dirty && try_dirty_rect_scene(scene)) {
        return;
    }

    /* Full redraw path (BG change, transition, CG, Ch5 motion, tear/FX). */
    buffers_synced = false;
    dirty_sync_ok = false;
    box_valid = false;
    box_namebox_dirty = false;

    perf_overlay_stage_begin(PERF_STAGE_BG);
    last_scene_used_motion = ch5_motion_draw(scene->background);
    if (last_scene_used_motion) {
        perf_overlay_mark_rendered();
        perf_overlay_set_mode("MOT");
    } else {
        draw_background(scene->background);
        perf_overlay_set_mode("FULL");
    }
    if (scene->overlay_fx_mask & VN_OVL_LIVE_TILE) {
        render_live_tile_bg();
    }
    perf_overlay_stage_end(PERF_STAGE_BG);

    if (!fullscreen) {
        assets_set_clip_rect(0, 0, SCREEN_W, SCENE_H);
    }
    perf_overlay_clear_sprites();
    for (int i = 0; i < VN_MAX_CHARS; i++) {
        const vn_actor_t *actor = &scene->actors[i];
        if (actor->character == VN_NO_SPRITE) {
            continue;
        }

        perf_overlay_stage_begin((uint8_t)(PERF_STAGE_ACT0 + i));
        if (cur_poses[i].active) {
            draw_actor_pose(&cur_poses[i], (uint8_t)i);
        } else {
            draw_actor(actor, (uint8_t)i, t);
        }
        perf_overlay_stage_end((uint8_t)(PERF_STAGE_ACT0 + i));
    }
    if (exit_active) {
        unsigned elapsed = t - exit_started_at;
        if (elapsed >= LEFTIN_MS) {
            exit_active = false;
        } else {
            int cur_x = LEFTIN_PX + ease(ease_remain, exit_from_x - LEFTIN_PX,
                                         t, exit_started_at, LEFTIN_MS);
            perf_overlay_stage_begin(PERF_STAGE_ACT3);
            draw_actor_at(&exit_actor, 0xFF, cur_x, t);
            perf_overlay_stage_end(PERF_STAGE_ACT3);
            anim_moving = true;
        }
    }
    if (!fullscreen) {
        assets_reset_clip_rect();
    }
    assets_sprite_end();

    if (ovl_pixel_active) {
        render_overlay_fx(scene);
    }
    apply_tear(scene, t);
    apply_layer_fx(scene, t);
    if (scene->tear_on) {
        anim_moving = true;
    }

    prev_exit_active = exit_active;
    if (!last_scene_used_motion && !ovl_pixel_active &&
        !scene->tear_on && scene->layer_fx == VN_LAYER_NONE &&
        !exit_active && !fullscreen && bounds_ok) {
        memcpy(composed_actors, cur_poses, sizeof(composed_actors));
        composed_bg = scene->background;
        composed_valid = true;
    } else {
        composed_valid = false;
    }

    scene_settled   = !anim_moving;
    have_drawn_once = true;
}

/* Transactional composition entrypoint. Scheduling caller supplies validated
 * source frame identity/time. No swap here; normal textbox/window composition
 * and noise_tick must run before presentation. Existing inactive graphx buffer
 * is the only pixel staging. Stack palette costs 512 bytes, pending measured
 * stack-domain proof. The scheduler calls this only from noise_tick. */
bool render_timed_cg(const vn_scene_t *scene, uint16_t index,
                     uint32_t timestamp_ms) {
    uint16_t palette[256];
    noise_restore(); /* BEFORE any destination bytes or palette are changed. */
    render_invalidate_scene();
    plate_free();
    memset(gfx_vbuffer,0,320u*240u);
    bool frame_ok=cgpack_read_timed_frame(scene->background,index,timestamp_ms,
                                         (uint8_t *)gfx_vbuffer,palette);
    /* UI artwork uses fixed indices. Reject an incompatible external palette
     * rather than overwrite its colors or silently recolor the reference. */
    if (frame_ok) {
        const uint16_t *ui=assets_scene_palette(scene->background);
        for (unsigned i=0;i<50;i++) {
            if (i==8 || i==9) continue;
            if (palette[i]!=ui[i]) { frame_ok=false; break; }
        }
    }
    if (frame_ok) {
        /* Frame/palette completely read before hardware palette mutation.
         * Only top 180 rows are frame data; remove sector padding below it. */
        memset((uint8_t *)gfx_vbuffer+57600u,0,19200u);
        render_apply_palette(palette);
        perf_overlay_mark_rendered();
        have_drawn_once=false; /* never let a lazy cached underlay survive */
        return true;
    }
    /* Short reads may have overwritten ANY prefix. Motion draw covers only
     * top 180: clear ALL 240 rows before reconstructing the authored underlay.
     * Subsequent normal textbox/window draw replaces lower UI before swap. */
    memset(gfx_vbuffer,0,320u*240u);
    /* Caller owns the sole fallback redraw after this transaction fails. */
    return false;
}

/* Scene-local schedule is tied to the motion clock, not noise startup.
 * Failure invalidates/clears staging; caller reconstructs once before presenting. */
bool render_timed_scene(const vn_scene_t *scene) {
    uint16_t index;
    uint32_t timestamp;
    if (!ch5_motion_active(scene->background) || cgpack_status()!=CGPACK_READY)
        return false;
    if (!ch5_motion_sync_clock(scene->background)) return false;
    noise_restore();
    if (cgpack_select_timed_frame(scene->background,
                                 ch5_motion_elapsed_ms(scene->background),
                                 (uint8_t *)gfx_vbuffer,&index,&timestamp))
        return render_timed_cg(scene,index,timestamp);
    render_invalidate_scene();
    memset(gfx_vbuffer,0,320u*240u);
    return false;
}

/* The fast path: dirty-rect composition lives in render_scene(); this marker
 * separates render_timed_scene() from scene lifecycle helpers. */
static void scene_obscured(void)
{
    render_invalidate_scene();
}

void render_invalidate_scene(void)
{
    have_drawn_once = false;
    buffers_synced = false;
    dirty_sync_ok = false;
    composed_valid = false;
    box_valid = false;
    box_namebox_dirty = false;
}

bool render_scene_used_motion(void)
{
    return last_scene_used_motion;
}

bool render_scene_is_settled(uint8_t bg_id)
{
    return have_drawn_once && scene_settled && !ch5_motion_active(bg_id) &&
           !ovl_pixel_active && !layer_fx_active_pending();
}

void render_scene_lazy(const vn_scene_t *scene)
{
    static clock_t motion_next;
    static clock_t ovl_next;
    if (ch5_motion_active(scene->background) && clock() >= motion_next) {
        render_scene(scene);
        motion_next = clock() + CLOCKS_PER_SEC/4;
        return;
    }
    if ((scene->overlay_fx_mask & VN_OVL_PIXEL_MASK) && (!have_drawn_once || clock() >= ovl_next)) {
        render_scene(scene);
        ovl_next = clock() + CLOCKS_PER_SEC / 8;
        return;
    }
    if (scene->layer_fx != VN_LAYER_NONE &&
        (!have_drawn_once || layer_fx_scene_pending(scene, ticks_to_ms(clock())))) {
        render_scene(scene);
        return;
    }
    if (!have_drawn_once || !scene_settled) {
        render_scene(scene);
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

static __attribute__((noinline)) bool draw_namebox_art(const char *speaker)
{
    const int nb_x = 6;
    const int nb_y = BOX_Y - NAMEBOX_H + 4;
    if (!assets_draw_box(true, (uint8_t *)gfx_vbuffer + nb_y * SCREEN_W + nb_x)) {
        return false;
    }
    set_font(FONT_NAME);
    int name_w = (int)string_width(speaker);
    int name_x = nb_x + (NAMEBOX_W - name_w) / 2;
    if (name_x < nb_x + 2) {
        name_x = nb_x + 2;
    }
    print_slice_outlined(speaker, strlen(speaker), name_x, nb_y + 2,
                         COL_WHITE, COL_BOX_FILL);
    set_font(FONT_DEFAULT);
    return true;
}

void render_box(const vn_scene_t *scene, const char *speaker,
                const char *text, size_t visible)
{
    perf_overlay_frame_start();
    static bool had_namebox;
    bool cur_hidden = scene->window_hidden || text == NULL || text[0] == '\0';

    /* Restore scene before filling the textbox: motion uses its lower rows
     * as temporary scratch, so a late redraw would erase the new box. */
    if (had_namebox && (speaker == NULL || cur_hidden)) {
        if (composed_valid &&
            !ch5_motion_active(scene->background) &&
            assets_scene_has_bg_rect(scene->background)) {
            rect_t nb = { 6, BOX_Y - NAMEBOX_H + 4, 6 + NAMEBOX_W, SCENE_H };
            composite_dirty_rect(scene->background, composed_actors, &nb);
        } else {
            render_invalidate_scene();
            render_scene(scene);
        }
        had_namebox = false;
        box_namebox_dirty = false;
    }

    perf_overlay_stage_begin(PERF_STAGE_BOX);

    /* `window hide` or empty text -- DDLC uses this for beats with no text
     * (e.g. splash screens, CGs, end screens, pauses). Full-height 240px
     * scenes and full-screen noise already fill rows 180..239; 180px scenes
     * letterbox the bottom bar to black (or near-white for light voids). */
    if (cur_hidden) {
        if (assets_scene_fullscreen(scene->background) || noise_is_fullscreen()) {
            box_valid = false;
            perf_overlay_stage_end(PERF_STAGE_BOX);
            return;
        }
        uint8_t bottom_color = ((uint8_t *)gfx_vbuffer)[(SCENE_H - 1) * SCREEN_W];
        uint8_t fill = palette_entry_is_near_white(bottom_color) ? bottom_color : COL_BLACK;
        if (box_valid && box_last_hidden && box_last_fill == fill) {
            perf_overlay_stage_end(PERF_STAGE_BOX);
            return;
        }
        gfx_SetColor(fill);
        gfx_FillRectangle_NoClip(0, BOX_Y, SCREEN_W, BOX_H);
        mark_dirty_y(BOX_Y, SCREEN_H);
        box_valid = true;
        box_namebox_dirty = false;
        box_last_hidden = true;
        box_last_fill = fill;
        perf_overlay_stage_end(PERF_STAGE_BOX);
        return;
    }

    /* Update wrapped-line cache before comparing visible counts. */
    const int pad = 6;
    static char          cached_copy[256];
    static text_layout_t cached_full;
    bool text_same = (strcmp(text, cached_copy) == 0);
    if (!text_same) {
        strncpy(cached_copy, text, sizeof(cached_copy) - 1);
        cached_copy[sizeof(cached_copy) - 1] = '\0';
        text_wrap(&cached_full, cached_copy, SCREEN_W - 2 * pad, measure, NULL);
    }

    size_t eff_visible = (visible >= cached_full.total) ? cached_full.total : visible;
    bool fake_skip = (scene->overlay_fx_mask & VN_OVL_FAKE_SKIP) != 0;

    if (try_skip_visible_box(scene, speaker, text_same, eff_visible)) {
        perf_overlay_stage_end(PERF_STAGE_BOX);
        return;
    }

    if (assets_draw_box(false, (uint8_t *)gfx_vbuffer + BOX_Y * SCREEN_W)) {
        /* Already copied directly from archive. */
    } else {
        gfx_SetColor(COL_BOX_FILL);
        gfx_FillRectangle_NoClip(0, BOX_Y, SCREEN_W, BOX_H);
        gfx_SetColor(COL_BOX_EDGE);
        gfx_HorizLine_NoClip(0, BOX_Y, SCREEN_W);
    }

    int y = BOX_Y + 4;
    box_used_namebox_art = false;

    if (speaker != NULL) {
        if (draw_namebox_art(speaker)) {
            box_used_namebox_art = true;
            y = (BOX_Y - NAMEBOX_H + 4) + NAMEBOX_H + 2;
        } else {
            set_font(FONT_NAME);
            print_slice_outlined(speaker, strlen(speaker), pad, y,
                                 COL_WHITE, COL_BOX_FILL);
            set_font(FONT_DEFAULT);
            y += 11;
        }
        had_namebox = true;
    }

    text_layout_t shown;
    if (visible >= cached_full.total) {
        shown = cached_full;
    } else {
        text_clamp(&shown, &cached_full, visible);
    }

    for (uint8_t i = 0; i < shown.count; i++) {
        if (scene->text_edited) {
            /* Thick black double-stroke matching DDLC's heavy VerilySerifMono edited style */
            print_slice_outlined(shown.lines[i].start, shown.lines[i].len, pad - 1, y,
                                 COL_WHITE, COL_BLACK);
            print_slice_outlined(shown.lines[i].start, shown.lines[i].len, pad + 1, y,
                                 COL_WHITE, COL_BLACK);
            fontlib_SetForegroundColor(COL_WHITE);
            print_slice(shown.lines[i].start, shown.lines[i].len, pad, y);
        } else {
            print_slice_outlined(shown.lines[i].start, shown.lines[i].len, pad, y,
                                 COL_WHITE, COL_BLACK);
        }
        y += 10;
    }

    if (fake_skip) {
        gfx_SetColor(COL_BOX_FILL);
        gfx_FillRectangle_NoClip(4, 4, 82, 14);
        gfx_SetColor(COL_BOX_EDGE);
        gfx_Rectangle(4, 4, 82, 14);
        render_text("Skipping >>", 9, 6, COL_WHITE);
    }

    box_valid = !fake_skip;
    box_namebox_dirty = false;
    box_last_hidden = false;
    box_last_edited = scene->text_edited;
    box_last_visible = eff_visible;
    box_last_had_speaker = (speaker != NULL);
    if (speaker != NULL) {
        strncpy(box_last_speaker, speaker, sizeof(box_last_speaker) - 1);
        box_last_speaker[sizeof(box_last_speaker) - 1] = '\0';
    } else {
        box_last_speaker[0] = '\0';
    }

    mark_dirty_y(speaker != NULL ? (BOX_Y - NAMEBOX_H + 4) : BOX_Y, SCREEN_H);
    perf_overlay_stage_end(PERF_STAGE_BOX);
}

#ifdef DDLC_DEBUG_MENU
/* Debug menu's dialogue text-render test -- runs the real text_wrap() +
 * print_slice_outlined() pipeline render_box() itself uses (rather than a
 * plain gfx_PrintStringXY() call) against a couple of known-tricky
 * sentences, so a future rendering regression in that pipeline (glyph
 * spacing, wrapping, outline offsets) shows up here directly instead of
 * needing a live dialogue box reached mid-story to notice. */
void render_debug_text_test(void)
{
    render_backdrop(COL_BOX_FILL);

    static const char *const samples[2] = {
        "Then that makes it official!",
        "I think with that, we can officially end today's meeting on a good note."
    };
    const int pad = 6;
    int y = 12;

    text_layout_t layout;
    for (int s = 0; s < 2; s++) {
        text_wrap(&layout, samples[s], SCREEN_W - 2 * pad, measure, NULL);
        for (uint8_t i = 0; i < layout.count; i++) {
            print_slice_outlined(layout.lines[i].start, layout.lines[i].len, pad, y,
                                 COL_WHITE, COL_BLACK);
            y += 10;
        }
        y += 6;
    }
}

void render_debug_font_test(void)
{
    render_backdrop(COL_BOX_FILL);

    static const char *const sample = "The quick brown fox jumps 0123456789";
    static const char *const labels[2] = {
        "Aller_Rg (default, FONT_DEFAULT)",
        "RifficFree-Bold (namebox, FONT_NAME)"
    };
    int y = 20;

    for (uint8_t f = 0; f < 2; f++) {
        set_font(f);
        render_text(labels[f], 6, y, COL_NAME);
        y += 14;
        print_slice_outlined(sample, strlen(sample), 6, y, COL_WHITE, COL_BLACK);
        y += 26;
    }
    set_font(FONT_DEFAULT);
}

/* Debug menu's external-CG test -- draw_background() already transparently
 * upgrades to the full-res cgpack version when one's available (see
 * assets_scene()/cgpack_read_pixels()), so this is just that same real path
 * pointed at a caller-picked CG scene id, with no separate "try the pack"
 * step of its own. main.c overlays its own status/caption text and calls
 * render_present() afterward, same split as render_debug_text_test(). */
void render_debug_bg_preview(uint8_t bg_id)
{
    draw_background(bg_id);
    gfx_SetColor(COL_BLACK);
    gfx_FillRectangle_NoClip(0, SCENE_H, SCREEN_W, BOX_H);
}
#endif

void render_menu(const char *const *choices, uint8_t count, uint8_t selected)
{
    scene_obscured();
    set_font(FONT_DEFAULT);

    if (count == 0) {
        return;
    }

    const int btn_h = (count <= 4) ? 22 : (count == 5 ? 20 : 18);
    const int gap   = (count <= 4) ? 6 : 4;
    const int w     = SCREEN_W - 60;
    const int x     = (SCREEN_W - w) / 2;
    const int total_h = count * btn_h + (count - 1) * gap;
    const int top   = (SCENE_H - total_h) / 2;

    for (uint8_t i = 0; i < count; i++) {
        const int y = top + (int)i * (btn_h + gap);
        bool is_sel = (i == selected);
        size_t len  = strlen(choices[i]);
        int text_w  = (int)measure(NULL, choices[i], len);
        int text_x  = x + (w - text_w) / 2;
        if (text_x < x + 4) {
            text_x = x + 4;
        }
        /* Vertical center: font glyph cell is 15px tall (ascent 12, descent 3).
         * Centering in btn_h gives (btn_h - 15) / 2. */
        int text_y  = y + (btn_h - 15) / 2;

        /* Drop shadow */
        gfx_SetColor(COL_SHADOW);
        gfx_FillRectangle(x + 1, y + 1, w, btn_h);

        /* Button body */
        gfx_SetColor(is_sel ? COL_HIGHLIGHT : COL_BOX_FILL);
        gfx_FillRectangle(x, y, w, btn_h);

        /* Border */
        gfx_SetColor(is_sel ? COL_HIGHLIGHT : COL_BOX_EDGE);
        gfx_Rectangle(x, y, w, btn_h);

        fontlib_SetForegroundColor(is_sel ? COL_BLACK : COL_WHITE);
        print_slice(choices[i], len, text_x, text_y);
    }
}

void render_dialog_ok(const char *msg, const char *ok_label)
{
    const int bx = 46, by = 46, bw = 228, bh = 78;
    render_pause_box(bx, by, bw, bh, COL_BOX_FILL, COL_BOX_EDGE);
    if (msg && msg[0] != '\0') {
        text_layout_t layout;
        text_wrap(&layout, msg, (unsigned)(bw - 18), measure, NULL);
        int ty = by + 10;
        for (uint8_t i = 0; i < layout.count && i < 3; i++) {
            int lw = (int)measure(NULL, layout.lines[i].start, layout.lines[i].len);
            int lx = bx + (bw - lw) / 2;
            if (lx < bx + 6) lx = bx + 6;
            fontlib_SetForegroundColor(COL_BLACK);
            print_slice(layout.lines[i].start, layout.lines[i].len, lx + 1, ty + 1);
            fontlib_SetForegroundColor(COL_WHITE);
            print_slice(layout.lines[i].start, layout.lines[i].len, lx, ty);
            ty += 12;
        }
    }
    const char *ok = (ok_label && ok_label[0] != '\0') ? ok_label : "OK";
    const int btn_w = 88, btn_h = 20;
    const int btn_x = bx + (bw - btn_w) / 2;
    const int btn_y = by + bh - btn_h - 8;
    gfx_SetColor(COL_HIGHLIGHT);
    gfx_FillRectangle(btn_x, btn_y, btn_w, btn_h);
    gfx_SetColor(COL_BOX_EDGE);
    gfx_Rectangle(btn_x, btn_y, btn_w, btn_h);
    size_t olen = strlen(ok);
    int ow = (int)measure(NULL, ok, olen);
    fontlib_SetForegroundColor(COL_BLACK);
    print_slice(ok, olen, btn_x + (btn_w - ow) / 2, btn_y + 3);
}

static bool fade_active;
static bool palette_defer;
static bool palette_staged;

void render_present(uint8_t trans)
{
    perf_overlay_end();
    perf_overlay_present_begin();
    perf_overlay_vram_begin();
    gfx_SwapDraw();
    render_commit_palette();
    if (trans == TRANS_CUT && buffers_synced && dirty_sync_ok &&
        !console_active && !fade_active) {
        if (dirty_y0 < dirty_y1) {
            gfx_BlitLines(gfx_screen, (uint8_t)dirty_y0,
                          (uint8_t)(dirty_y1 - dirty_y0));
        }
    } else {
        gfx_BlitScreen();
    }
    perf_overlay_vram_end();
    perf_overlay_present_end();
    buffers_synced = true;
    dirty_sync_ok = false;
    dirty_y0 = SCREEN_H;
    dirty_y1 = 0;
}

void render_present_box(const vn_scene_t *scene)
{
    if (buffers_synced && scene_settled && have_drawn_once &&
        !console_active && !palette_staged && !fade_active &&
        scene && scene->overlay_mask == 0 &&
        !(scene->overlay_fx_mask & VN_OVL_FAKE_SKIP)) {
        dirty_sync_ok = true;
    }
    render_present(TRANS_CUT);
}

/* ---------------------------------------------------------------------------
 * Title / pause / menu screens
 * ------------------------------------------------------------------------ */

void render_backdrop(uint8_t color)
{
    perf_overlay_frame_start();
    scene_obscured();
    gfx_SetColor(color);
    gfx_FillRectangle_NoClip(0, 0, SCREEN_W, SCREEN_H);
}

void render_text(const char *s, int x, int y, uint8_t color)
{
    if (!active_font || !s || x < 0 || x >= SCREEN_W || y < 0 || y >= SCREEN_H) {
        return;
    }
    dirty_sync_ok = false;
    fontlib_SetForegroundColor(color);
    fontlib_SetCursorPosition((unsigned int)x, (uint8_t)y);
    fontlib_DrawString(s);
}


void render_text_centered(const char *s, int y, uint8_t color)
{
    int cx = (SCREEN_W - (int)string_width(s)) / 2;
    if (cx < 0) {
        cx = 0;
    }
    render_text(s, cx, y, color);
}

void render_list_menu(const char *const *items, uint8_t count, uint8_t selected,
                      int x, int y, uint8_t normal_color, uint8_t selected_color)
{
    if (!active_font) {
        return;
    }
    const int line_h = 16;

    for (uint8_t i = 0; i < count; i++) {
        bool is_selected = i == selected;
        uint8_t color = is_selected ? selected_color : normal_color;
        int line_y = y + i * line_h;

        fontlib_SetForegroundColor(color);
        fontlib_SetCursorPosition(x, line_y);
        fontlib_DrawString(is_selected ? ">" : " ");
        fontlib_SetCursorPosition(x + 10, line_y);
        fontlib_DrawString(items[i]);
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
        fontlib_SetForegroundColor(is_selected ? sel_text_color : text_color);
        fontlib_SetCursorPosition(x, line_y);
        fontlib_DrawString(items[i]);
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

void render_title_screen(uint8_t selected, unsigned t, int16_t playthrough)
{
    perf_overlay_frame_start();
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

    /* Outlined like dialogue text (print_slice_outlined) rather than the
     * flat-fill-plus-highlight-bar this used to be -- real DDLC's own nav
     * panel text is outlined, and marks the selected item by recoloring
     * the text itself (a lighter pink), not a background bar behind it. */
    for (uint8_t i = 0; i < count; i++) {
        int y = MENU_Y + i * MENU_LINE_H;
        uint8_t fill = i == selected ? COL_NAME : COL_WHITE;
        print_slice_outlined(items[i], strlen(items[i]), nav_x + MENU_X, y,
                             fill, COL_BOX_FILL);
    }

    /* Logo bounce-drops from above -- over the nav panel but under the front
     * pair, matching DDLC's own layering. */
    draw_title_art(TITLE_LOGO, 0,
                   ease(bounce_remain, 256, t, F_LOGO_AT, F_LOGO_DUR));

    /* Frontmost pair. */
    if (playthrough == 0 || playthrough == 4) {
        draw_title_art(TITLE_SAYORI, slide, rise);
    }
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

static uint16_t current_palette[256];
#define fade_saved current_palette
#ifndef __TICE__
static bool fade_active;
static bool palette_defer;
static bool palette_staged;
#endif
static bool timed_fx_inverted;
static bool room_mask_applied;

static void fade_apply(uint8_t amount)
{
    /* gfx_Darken: 0 is full black, 255 is the original color. */
    for (unsigned i = 0; i < 256; i++) {
        gfx_palette[i] = gfx_Darken(current_palette[i], amount);
    }
}

void render_fade_out(void)
{
    fade_active = true;

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
    memcpy(gfx_palette, current_palette, sizeof(current_palette));
    fade_active = false;
}

void render_defer_palette(bool defer)
{
    palette_defer = defer;
}

void render_commit_palette(void)
{
    palette_defer = false;
    if (palette_staged && !fade_active) {
#ifdef __TICE__
        gfx_Wait();
#endif
        memcpy(gfx_palette, current_palette, sizeof(current_palette));
        palette_staged = false;
    }
}

void render_apply_palette(const uint16_t *palette)
{
    timed_fx_inverted = false;
    room_mask_applied = false;
    memcpy(current_palette, palette, sizeof(current_palette));
    if (fade_active) {
        palette_staged = false;
        fade_apply(0);
    } else if (palette_defer) {
        palette_staged = true;
    } else {
        palette_staged = false;
        memcpy(gfx_palette, palette, sizeof(current_palette));
    }
}

const uint16_t *render_active_palette(void)
{
    return (fade_active || palette_staged) ? current_palette : gfx_palette;
}

void render_fade_retarget(const uint16_t *palette)
{
    memcpy(current_palette, palette, sizeof(current_palette));
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

static bool palette_entry_is_near_white(uint8_t index)
{
    uint16_t bc = current_palette[index];
    return ((bc >> 10) & 31U) >= 28U && ((bc >> 5) & 31U) >= 28U && (bc & 31U) >= 28U;
}

const uint16_t *render_filter_palette(const vn_scene_t *scene, const uint16_t *palette)
{
    static uint16_t fx_palette[256];
    if (!scene || !palette || (scene->pal_fx == VN_FX_NONE && scene->box_style == 0)) {
        return palette;
    }
    memcpy(fx_palette, palette, sizeof(fx_palette));
    if (scene->pal_fx == VN_FX_SILHOUETTE) {
        for (unsigned i = 8; i < 256; i++) {
            uint16_t c = fx_palette[i];
            unsigned r = (c >> 10) & 31U;
            unsigned g = (c >> 5) & 31U;
            unsigned b = c & 31U;
            unsigned luma = (r + (g << 1) + b) >> 2;
            unsigned gray = ((luma * 3U) >> 2) + 4U;
            if (gray > 31U) {
                gray = 31U;
            }
            fx_palette[i] = (uint16_t)((gray << 10) | (gray << 5) | gray);
        }
    }
    if (scene->box_style == 1) {
        /* Shift the dedicated textbox/namebox entries (COL_BOX_FILL,
         * COL_BOX_EDGE, COL_NAME, and UI_BOX_FIXED_ENTRIES 10..49) to Act 3
         * Monika deep violet without disturbing scene colors. */
        for (unsigned i = 3; i <= 49; i++) {
            if (i > 5 && i < 10) {
                continue;
            }
            uint16_t c = fx_palette[i];
            unsigned r = (c >> 10) & 31U;
            unsigned g = (c >> 5) & 31U;
            unsigned b = c & 31U;
            if (r + g + b == 0U) {
                continue;
            }
            r = (r * 9U) >> 4;
            g = (g * 7U) >> 4;
            b = ((b * 13U) >> 4) + 3U;
            if (b > 31U) {
                b = 31U;
            }
            fx_palette[i] = (uint16_t)((c & 0x8000U) | (r << 10) | (g << 5) | b);
        }
    }
    return fx_palette;
}

static uint8_t blood_red_index(void)
{
    static uint8_t cached_col = COL_BOX_EDGE;
    static uint16_t cached_key = 0;
    static bool has_cache = false;

    uint16_t key = (uint16_t)(current_palette[8] ^ current_palette[128] ^ current_palette[200]);
    if (!has_cache || cached_key != key) {
        has_cache = true;
        cached_key = key;
        int best_score = 8;
        uint8_t best = COL_BOX_EDGE;
        for (unsigned i = 1; i < 256; i++) {
            uint16_t c = current_palette[i];
            int r = (int)((c >> 10) & 31U);
            int g = (int)((c >> 5) & 31U);
            int b = (int)(c & 31U);
            int score = r * 2 - g - b;
            if (r >= 10 && score > best_score) {
                best_score = score;
                best = (uint8_t)i;
            }
        }
        cached_col = best;
    }
    return cached_col;
}

static void render_live_tile_bg(void)
{
    for (int i = 0; i < 12; i++) {
        uint32_t r = tear_rand_next();
        int sx = (int)(r % 20U) * 16;
        int sy = (int)((r >> 5) % 11U) * 16;   /* <= 160, 160 + 16 = 176 < SCENE_H */
        int dx = (int)((r >> 10) % 20U) * 16;
        int dy = (int)((r >> 15) % 11U) * 16;
        uint8_t *dst = (uint8_t *)gfx_vbuffer + (size_t)dy * SCREEN_W + dx;
        const uint8_t *src = (const uint8_t *)gfx_vbuffer + (size_t)sy * SCREEN_W + sx;
        fast_rect_blit(dst, SCREEN_W, src, SCREEN_W, 16, 16);
    }
}

#define BLOOD_MAX_DROPS 24
typedef struct {
    int16_t x, y;
    int8_t  vx, vy;
    uint8_t ttl;
} blood_drop_t;

static void spawn_blood_drop(blood_drop_t *d, bool eye_mode)
{
    uint32_t r = tear_rand_next();
    if (eye_mode) {
        d->x = (int16_t)(142 + (r % 36U));
        d->y = (int16_t)(56 + ((r >> 6) % 12U));
        d->vx = 0;
        d->vy = (int8_t)(1 + ((r >> 10) % 3U));
    } else {
        d->x = (int16_t)(148 + (r % 24U));
        d->y = (int16_t)(64 + ((r >> 6) % 22U));
        d->vx = (int8_t)(((int)(r >> 10) % 9) - 4);
        d->vy = (int8_t)(((int)(r >> 14) % 7) - 4);
    }
    d->ttl = (uint8_t)(10U + ((r >> 18) % 14U));
}

static __attribute__((noinline)) void render_overlay_fx(const vn_scene_t *scene)
{
    uint8_t mask = scene->overlay_fx_mask;

    if (mask & VN_OVL_RECTS_N) {
        gfx_SetColor(COL_BLACK);
        for (int i = 0; i < 6; i++) {
            uint32_t r = tear_rand_next();
            int rx = 138 + (int)(r % 56U);
            int ry = 60 + (int)((r >> 6) % 42U);
            int rw = 2 + (int)((r >> 12) % 4U);
            int rh = 2 + (int)((r >> 15) % 3U);
            if (rx + rw <= SCREEN_W && ry + rh <= SCENE_H) {
                gfx_FillRectangle_NoClip(rx, ry, rw, rh);
            }
        }
    }

    if (mask & VN_OVL_RECTS_M) {
        gfx_SetColor(COL_BLACK);
        for (int i = 0; i < 14; i++) {
            uint32_t r = tear_rand_next();
            int rx = (int)(r % (SCREEN_W - 8));
            int ry = (int)((r >> 9) % (SCENE_H - 8));
            gfx_FillRectangle_NoClip(rx, ry, 8, 8);
        }
    }

    if (mask & (VN_OVL_BLOOD | VN_OVL_BLOOD_EYE)) {
        static blood_drop_t drops[BLOOD_MAX_DROPS];
        static uint8_t prev_blood_mode;
        bool eye_mode = (mask & VN_OVL_BLOOD_EYE) != 0 && !(mask & VN_OVL_BLOOD);
        uint8_t cur_mode = eye_mode ? 2 : 1;
        if (cur_mode != prev_blood_mode) {
            prev_blood_mode = cur_mode;
            for (int i = 0; i < BLOOD_MAX_DROPS; i++) {
                spawn_blood_drop(&drops[i], eye_mode);
            }
        }
        gfx_SetColor(blood_red_index());
        for (int i = 0; i < BLOOD_MAX_DROPS; i++) {
            blood_drop_t *d = &drops[i];
            d->x += d->vx;
            d->y += d->vy;
            if (!eye_mode && d->vy < 6) {
                d->vy++;
            }
            if (d->ttl == 0 || d->y < 0 || d->y >= SCENE_H - 2 ||
                d->x < 0 || d->x >= SCREEN_W - 2) {
                spawn_blood_drop(d, eye_mode);
            } else {
                d->ttl--;
                gfx_FillRectangle_NoClip(d->x, d->y, 2, 2);
            }
        }
    }

    if (mask & VN_OVL_DUST) {
        static int16_t dust_x[10], dust_y[10];
        static bool dust_init;
        if (!dust_init) {
            dust_init = true;
            for (int i = 0; i < 10; i++) {
                uint32_t r = tear_rand_next();
                dust_x[i] = (int16_t)(r % (SCREEN_W - 2));
                dust_y[i] = (int16_t)((r >> 9) % (SCENE_H - 2));
            }
        }
        gfx_SetColor(2); /* COL_WHITE */
        for (int i = 0; i < 10; i++) {
            dust_x[i] = (int16_t)((dust_x[i] + ((i & 1) ? 1 : 0)) % (SCREEN_W - 1));
            dust_y[i] = (int16_t)((dust_y[i] + 1) % (SCENE_H - 1));
            gfx_FillRectangle_NoClip(dust_x[i], dust_y[i], 1, 1);
        }
    }
}

void render_timed_fx_tick(const vn_scene_t *scene)
{
    static uint8_t  last_seq;
    static uint8_t  last_mode;
    static unsigned started_ms;
    static unsigned mask_last_ms;
    static uint16_t last_step;
    static uint16_t guard_c16;
    static bool     fx_pal_active;

    if (!scene) {
        return;
    }

    unsigned now = ticks_to_ms(clock());
    uint8_t mode = scene->timed_fx;

    if (mode != last_mode || scene->timed_fx_seq != last_seq) {
        last_mode = mode;
        last_seq = scene->timed_fx_seq;
        started_ms = now;
        last_step = 0xFFFFU;
        if (timed_fx_inverted || fx_pal_active) {
            memcpy(gfx_palette, current_palette, sizeof(current_palette));
            timed_fx_inverted = false;
            fx_pal_active = false;
        }
    }

    if (mode == VN_EFFECT_INVERT) {
        unsigned elapsed = now - started_ms;
        bool want = (elapsed >= scene->timed_fx_delay_ms) &&
                    (elapsed < (unsigned)scene->timed_fx_delay_ms + scene->timed_fx_len_ms);
        if (want != timed_fx_inverted) {
            timed_fx_inverted = want;
            if (want) {
                for (unsigned i = 0; i < 256; i++) {
                    gfx_palette[i] = current_palette[i] ^ 0xFFFFU;
                }
            } else {
                memcpy(gfx_palette, current_palette, sizeof(current_palette));
            }
        }
        if (timed_fx_inverted) {
            return;
        }
    } else if (mode >= VN_EFFECT_FLASH_WHITE && mode <= VN_EFFECT_DIM_HALF) {
        unsigned elapsed = now - started_ms;
        unsigned dly = scene->timed_fx_delay_ms;
        unsigned len = scene->timed_fx_len_ms;
        unsigned strength = 0;

        if (mode == VN_EFFECT_DIM_HALF) {
            strength = 128U;
        } else if (mode == VN_EFFECT_FLASH_WHITE) {
            if (len > 0 && elapsed < len) {
                strength = ((len - elapsed) * 192U) / len;
            }
        } else { /* FADE_BLACK or TINT_RED */
            if (elapsed >= dly) {
                unsigned after = elapsed - dly;
                if (len == 0 || after >= len) {
                    strength = 255U;
                } else {
                    strength = (after * 255U) / len;
                }
            }
        }

        uint16_t step = (uint16_t)((strength >> 3) + 1U);
        if (mode == VN_EFFECT_FLASH_WHITE && strength == 0) {
            if (fx_pal_active) {
                memcpy(gfx_palette, current_palette, sizeof(current_palette));
                fx_pal_active = false;
            }
        } else if (step != last_step || !fx_pal_active || gfx_palette[16] != guard_c16) {
            last_step = step;
            fx_pal_active = true;
            for (unsigned i = 16; i < 256; i++) {
                uint16_t c = current_palette[i];
                unsigned r = (c >> 10) & 31U;
                unsigned g = (c >> 5) & 31U;
                unsigned b = c & 31U;
                if (mode == VN_EFFECT_FLASH_WHITE) {
                    r += ((31U - r) * strength) >> 8;
                    g += ((31U - g) * strength) >> 8;
                    b += ((31U - b) * strength) >> 8;
                } else if (mode == VN_EFFECT_FADE_BLACK || mode == VN_EFFECT_DIM_HALF) {
                    unsigned keep = (mode == VN_EFFECT_DIM_HALF) ? 128U : (256U - strength);
                    r = (r * keep) >> 8;
                    g = (g * keep) >> 8;
                    b = (b * keep) >> 8;
                } else if (mode == VN_EFFECT_TINT_RED) {
                    unsigned keep = 256U - ((strength * 200U) >> 8);
                    r += ((31U - r) * strength) >> 9;
                    if (r > 31U) r = 31U;
                    g = (g * keep) >> 8;
                    b = (b * keep) >> 8;
                }
                gfx_palette[i] = (uint16_t)((c & 0x8000U) | (r << 10) | (g << 5) | b);
            }
            guard_c16 = gfx_palette[16];
            return;
        }
        if (fx_pal_active) {
            return;
        }
    }

    if (scene->overlay_fx_mask & VN_OVL_ROOM_MASK) {
        if (!room_mask_applied || now - mask_last_ms >= 100U) {
            mask_last_ms = now;
            room_mask_applied = true;
            unsigned phase = (now / 120U) & 31U;
            unsigned amp = (phase < 16U) ? phase : (31U - phase);
            unsigned boost = amp >> 2;
            for (unsigned i = 8; i < 256; i++) {
                uint16_t c = current_palette[i];
                unsigned r = ((c >> 10) & 31U) + boost;
                unsigned g = ((c >> 5) & 31U) + (boost >> 1);
                unsigned b = c & 31U;
                if (r > 31U) r = 31U;
                if (g > 31U) g = 31U;
                gfx_palette[i] = (uint16_t)((c & 0x8000U) | (r << 10) | (g << 5) | b);
            }
        }
    } else if (room_mask_applied) {
        memcpy(gfx_palette, current_palette, sizeof(current_palette));
        room_mask_applied = false;
    }
}

static uint8_t  layer_s_fx;
static uint8_t  layer_s_seq;
static uint16_t layer_s_param;
static unsigned layer_s_started_ms;
static uint16_t layer_s_drawn_key;
uint8_t  render_row_scratch[SCREEN_W];
#define layer_row_scratch render_row_scratch

static void copy_row_shifted(uint8_t *dst, const uint8_t *src, int dx)
{
    if (dx == 0) {
        if (dst != src) {
            memcpy(dst, src, SCREEN_W);
        }
    } else if (dx > 0) {
        int copy_w = SCREEN_W - dx;
        if (copy_w > 0) {
            memmove(dst + dx, src, (size_t)copy_w);
            memset(dst, COL_BLACK, (size_t)dx);
        } else {
            memset(dst, COL_BLACK, SCREEN_W);
        }
    } else {
        int sh = -dx;
        int copy_w = SCREEN_W - sh;
        if (copy_w > 0) {
            memmove(dst, src + sh, (size_t)copy_w);
            memset(dst + copy_w, COL_BLACK, (size_t)sh);
        } else {
            memset(dst, COL_BLACK, SCREEN_W);
        }
    }
}

static void layer_viewport_shift(int dx, int dy)
{
    if (dx == 0 && dy == 0) {
        return;
    }
    if (dx <= -SCREEN_W || dx >= SCREEN_W || dy <= -SCENE_H || dy >= SCENE_H) {
        memset(gfx_vbuffer, COL_BLACK, (size_t)(SCREEN_W * SCENE_H));
        return;
    }
    uint8_t *fb = (uint8_t *)gfx_vbuffer;
    if (dx == 0) {
        if (dy > 0) {
            memmove(fb + (size_t)dy * SCREEN_W, fb, (size_t)(SCENE_H - dy) * SCREEN_W);
            memset(fb, COL_BLACK, (size_t)dy * SCREEN_W);
        } else {
            int sy_off = -dy;
            int lim = SCENE_H + dy;
            memmove(fb, fb + (size_t)sy_off * SCREEN_W, (size_t)lim * SCREEN_W);
            memset(fb + (size_t)lim * SCREEN_W, COL_BLACK, (size_t)sy_off * SCREEN_W);
        }
        return;
    }
    if (dy >= 0) {
        for (int y = SCENE_H - 1; y >= dy; y--) {
            copy_row_shifted(fb + (size_t)y * SCREEN_W,
                             fb + (size_t)(y - dy) * SCREEN_W, dx);
        }
        if (dy > 0) {
            memset(fb, COL_BLACK, (size_t)(dy * SCREEN_W));
        }
    } else {
        int sy_off = -dy;
        int lim = SCENE_H + dy;
        for (int y = 0; y < lim; y++) {
            copy_row_shifted(fb + (size_t)y * SCREEN_W,
                             fb + (size_t)(y + sy_off) * SCREEN_W, dx);
        }
        memset(fb + (size_t)lim * SCREEN_W, COL_BLACK, (size_t)(sy_off * SCREEN_W));
    }
}

static void layer_stretch_one(uint8_t *fb, int dy, int sy, int x0, int cw)
{
    uint8_t *dst = fb + (size_t)dy * SCREEN_W;
    const uint8_t *src = fb + (size_t)sy * SCREEN_W + x0;
    if (sy == dy) {
        memcpy(layer_row_scratch, src, (size_t)cw);
        src = layer_row_scratch;
    }
    if (cw == SCREEN_W) {
        if (dst != src) {
            memcpy(dst, src, SCREEN_W);
        }
    } else {
        fast_stretch_row(dst, src, SCREEN_W, (size_t)cw);
    }
}

/* In-place crop-and-stretch on gfx_vbuffer (320x180) using one 320-byte row
 * scratch buffer. Rows above the crossing point copy from strictly lower rows,
 * rows below copy from strictly upper rows, and fixed-point rows use scratch. */
static void layer_crop_stretch(int x0, int y0, int cw, int ch)
{
    if (cw >= SCREEN_W && ch >= SCENE_H) {
        return;
    }
    if (cw < 1) cw = 1;
    if (cw > SCREEN_W) cw = SCREEN_W;
    if (ch < 1) ch = 1;
    if (ch > SCENE_H) ch = SCENE_H;
    if (x0 < 0) x0 = 0;
    if (x0 > SCREEN_W - cw) x0 = SCREEN_W - cw;
    if (y0 < 0) y0 = 0;
    if (y0 > SCENE_H - ch) y0 = SCENE_H - ch;

    uint8_t *fb = (uint8_t *)gfx_vbuffer;
    int y = 0;
    int prev_sy = -1;
    int sy = y0;
    int acc = 0;
    while (y < SCENE_H) {
        if (sy <= y) {
            break;
        }
        if (sy == prev_sy && y > 0) {
            memcpy(fb + (size_t)y * SCREEN_W, fb + (size_t)(y - 1) * SCREEN_W, SCREEN_W);
        } else {
            layer_stretch_one(fb, y, sy, x0, cw);
            prev_sy = sy;
        }
        y++;
        acc += ch;
        while (acc >= SCENE_H) {
            acc -= SCENE_H;
            sy++;
        }
    }
    int y2 = SCENE_H - 1;
    int prev_sy2 = -1;
    int sy2 = y0 + (y2 * ch) / SCENE_H;
    int acc2 = (y2 * ch) % SCENE_H;
    while (y2 >= y) {
        if (sy2 >= y2) {
            break;
        }
        if (sy2 == prev_sy2 && y2 + 1 < SCENE_H) {
            memcpy(fb + (size_t)y2 * SCREEN_W, fb + (size_t)(y2 + 1) * SCREEN_W, SCREEN_W);
        } else {
            layer_stretch_one(fb, y2, sy2, x0, cw);
            prev_sy2 = sy2;
        }
        y2--;
        acc2 -= ch;
        while (acc2 < 0) {
            acc2 += SCENE_H;
            sy2--;
        }
    }
    for (int ym = y; ym <= y2; ym++) {
        int sym = y0 + (ym * ch) / SCENE_H;
        layer_stretch_one(fb, ym, sym, x0, cw);
    }
}

static uint16_t layer_fx_compute_state(uint8_t fx, uint8_t seq, uint16_t param,
                                       unsigned elapsed_ms,
                                       int *out_cw, int *out_ch,
                                       int *out_xa, int *out_ya,
                                       int *out_dx, int *out_dy,
                                       bool *out_shear)
{
    int cw = SCREEN_W, ch = SCENE_H, xa = 50, ya = 50, dx = 0, dy = 0;
    bool shear = false;
    uint16_t key = 0;

    switch (fx) {
    case VN_LAYER_DRIFT: {
        unsigned step = elapsed_ms / 8000U;
        if (step > 15U) step = 15U;
        unsigned pct = 100U + step * 2U; /* 1.00 .. 1.30 in 16 steps */
        cw = (int)((SCREEN_W * 100U) / pct);
        ch = (int)((SCENE_H * 100U) / pct);
        dx = (int)(step / 3U);
        dy = -(int)(step / 4U);
        key = (uint16_t)(1U + step);
        break;
    }
    case VN_LAYER_FLICKER: {
        unsigned phase = elapsed_ms % 1500U;
        if (phase < 1200U) {
            key = 1U;
        } else if (phase < 1300U) {
            cw = 304; ch = 171; dx = 4;
            key = 2U;
        } else if (phase < 1400U) {
            cw = 310; ch = 174; dx = -4;
            key = 3U;
        } else {
            cw = 304; ch = 171; dx = 2;
            key = 4U;
        }
        break;
    }
    case VN_LAYER_MENU_ZOOM: {
        unsigned n = param;
        if (n > 12U) n = 12U;
        unsigned pct = 100U + n * n * 6U;
        if (pct > 250U) pct = 250U;
        cw = (int)((SCREEN_W * 100U) / pct);
        ch = (int)((SCENE_H * 100U) / pct);
        ya = 25;
        key = (uint16_t)(1U + n);
        break;
    }
    case VN_LAYER_DIZZY: {
        static const int8_t dx_lut[12] = { 6,  5,  3,  0, -3, -5, -6, -5, -3,  0,  3,  5};
        static const int8_t dy_lut[12] = { 0,  2,  3,  4,  3,  2,  0, -2, -3, -4, -3, -2};
        unsigned step = (elapsed_ms / 80U) % 12U;
        dx = dx_lut[step];
        dy = dy_lut[step];
        key = (uint16_t)(1U + step);
        break;
    }
    case VN_LAYER_REWIND: {
        static const int8_t jx[8] = {-5, 4, -3, 6, -4, 3, -6, 2};
        static const int8_t jy[8] = { 2,-3,  1,-2,  3,-1,  2,-3};
        unsigned step = (elapsed_ms / 90U) & 7U;
        cw = 266; ch = 150;
        dx = jx[step];
        dy = jy[step];
        shear = (step == 2U || step == 6U);
        key = (uint16_t)(1U + step);
        break;
    }
    case VN_LAYER_HEARTBEAT: {
        unsigned phase = elapsed_ms % 2140U;
        if (phase < 144U || phase >= 720U) {
            key = 1U;
        } else if (phase < 394U) {
            cw = 298; ch = 168; dx = 3;
            key = 2U;
        } else if (phase < 544U) {
            cw = 308; ch = 173; dx = -2;
            key = 3U;
        } else {
            cw = 298; ch = 168; dx = 2;
            key = 4U;
        }
        break;
    }
    case VN_LAYER_CH30_ZOOM: {
        unsigned step = elapsed_ms / 500U;
        if (step > 16U) step = 16U;
        unsigned pct = 100U + (step * 100U) / 16U; /* 100..200% */
        cw = (int)((SCREEN_W * 100U) / pct);
        ch = (int)((SCENE_H * 100U) / pct);
        ya = 15;
        key = (uint16_t)(1U + step);
        break;
    }
    case VN_LAYER_CH30_SHAKE1: {
        if (elapsed_ms < 1000U) {
            key = 1U;
        } else if (elapsed_ms < 1650U) {
            static const uint16_t w_tab[8] = {180, 180, 180, 190, 220, 250, 280, 304};
            static const int8_t   x_tab[8] = {-10,  10,  -8,   8,  -5,   5,  -3,   2};
            unsigned step = (elapsed_ms - 1000U) / 82U;
            if (step > 7U) step = 7U;
            cw = (int)w_tab[step];
            ch = (cw * SCENE_H) / SCREEN_W;
            ya = 10;
            dx = x_tab[step];
            key = (uint16_t)(2U + step);
        } else {
            key = 20U;
        }
        break;
    }
    case VN_LAYER_CH30_SHAKE3:
    case VN_LAYER_CH30_SHAKE1S: {
        unsigned total_ms = (fx == VN_LAYER_CH30_SHAKE3) ? 1050U : 350U;
        if (elapsed_ms < total_ms) {
            static const uint16_t bw[4] = {214, 246, 284, 308};
            static const int8_t   bx[4] = {-20,  14,  -6,   0};
            unsigned beat = elapsed_ms / 350U;
            unsigned sub  = (elapsed_ms % 350U) / 88U;
            if (sub > 3U) sub = 3U;
            int sign = (beat & 1U) ? -1 : 1;
            cw = (int)bw[sub];
            ch = (cw * SCENE_H) / SCREEN_W;
            dx = bx[sub] * sign;
            key = (uint16_t)(1U + beat * 4U + sub);
        } else {
            key = 99U;
        }
        break;
    }
    case VN_LAYER_CG_WIGGLE: {
        if (elapsed_ms < 500U) {
            static const int8_t wx[5] = {-3, 3, -2, 2, -1};
            unsigned step = elapsed_ms / 100U;
            if (step > 4U) step = 4U;
            dx = wx[step];
            key = (uint16_t)(1U + step);
        } else {
            key = 10U;
        }
        break;
    }
    case VN_LAYER_CG_WIGGLE_LOOP: {
        static const int8_t ldx[4] = {-2, 0, 2, 0};
        static const int8_t ldy[4] = {0, -1, 0, 1};
        unsigned step = (elapsed_ms / 120U) & 3U;
        dx = ldx[step];
        dy = ldy[step];
        key = (uint16_t)(1U + step);
        break;
    }
    case VN_LAYER_CG_ZOOM: {
        unsigned step = elapsed_ms / 80U;
        if (step > 4U) step = 4U;
        cw = 320 - (int)step * 15;
        ch = (cw * SCENE_H) / SCREEN_W;
        dx = (step & 1U) ? -2 : 2;
        if (step == 4U) dx = 0;
        key = (uint16_t)(1U + step);
        break;
    }
    case VN_LAYER_YURI_SLUMP: {
        if (elapsed_ms < 2550U) {
            key = 1U;
        } else {
            unsigned dt = elapsed_ms - 2550U;
            unsigned step = dt / 60U;
            if (step > 8U) step = 8U;
            dy = (int)step * 5;
            key = (uint16_t)(2U + step);
        }
        break;
    }
    default:
        break;
    }

    if (out_cw) *out_cw = cw;
    if (out_ch) *out_ch = ch;
    if (out_xa) *out_xa = xa;
    if (out_ya) *out_ya = ya;
    if (out_dx) *out_dx = dx;
    if (out_dy) *out_dy = dy;
    if (out_shear) *out_shear = shear;
    return (uint16_t)(((uint16_t)seq << 8) ^ (uint16_t)(fx << 5) ^ key);
}

static bool layer_fx_active_pending(void)
{
    if (layer_s_fx == VN_LAYER_NONE) {
        return false;
    }
    unsigned now = ticks_to_ms(clock());
    uint16_t key = layer_fx_compute_state(layer_s_fx, layer_s_seq, layer_s_param,
                                          now - layer_s_started_ms,
                                          NULL, NULL, NULL, NULL, NULL, NULL, NULL);
    return key != layer_s_drawn_key;
}

static bool layer_fx_scene_pending(const vn_scene_t *scene, unsigned now_ms)
{
    if (!scene || scene->layer_fx == VN_LAYER_NONE) {
        return false;
    }
    if (scene->layer_fx != layer_s_fx ||
        scene->layer_fx_seq != layer_s_seq ||
        scene->layer_fx_param != layer_s_param) {
        return true;
    }
    uint16_t key = layer_fx_compute_state(layer_s_fx, layer_s_seq, layer_s_param,
                                          now_ms - layer_s_started_ms,
                                          NULL, NULL, NULL, NULL, NULL, NULL, NULL);
    return key != layer_s_drawn_key;
}

static __attribute__((noinline)) void apply_layer_fx(const vn_scene_t *scene, unsigned t)
{
    if (!scene || scene->layer_fx == VN_LAYER_NONE) {
        layer_s_fx = VN_LAYER_NONE;
        layer_s_drawn_key = 0;
        return;
    }
    if (scene->layer_fx != layer_s_fx ||
        scene->layer_fx_seq != layer_s_seq ||
        scene->layer_fx_param != layer_s_param) {
        layer_s_fx = scene->layer_fx;
        layer_s_seq = scene->layer_fx_seq;
        layer_s_param = scene->layer_fx_param;
        layer_s_started_ms = t;
    }
    int cw, ch, xa, ya, dx, dy;
    bool shear;
    uint16_t key = layer_fx_compute_state(layer_s_fx, layer_s_seq, layer_s_param,
                                          t - layer_s_started_ms,
                                          &cw, &ch, &xa, &ya, &dx, &dy, &shear);
    layer_s_drawn_key = key;

    if (cw < SCREEN_W || ch < SCENE_H) {
        int x0 = ((SCREEN_W - cw) * xa) / 100;
        int y0 = ((SCENE_H - ch) * ya) / 100;
        layer_crop_stretch(x0, y0, cw, ch);
    }
    if (dx != 0 || dy != 0) {
        layer_viewport_shift(dx, dy);
    }
    if (shear) {
        uint8_t *fb = (uint8_t *)gfx_vbuffer;
        for (int y = 40; y < 70; y++) {
            fast_row_shift(fb + (size_t)y * SCREEN_W, layer_row_scratch, 10);
        }
        for (int y = 115; y < 135; y++) {
            fast_row_shift(fb + (size_t)y * SCREEN_W, layer_row_scratch, SCREEN_W - 8);
        }
    }
}

void render_splash_warning(const char *msg)
{
    scene_obscured();
    render_backdrop(COL_WHITE);
    if (msg == NULL) {
        msg = "This game is not suitable for children\nor those who are easily disturbed.";
    }
    const char *nl = strchr(msg, '\n');
    if (nl != NULL) {
        char line1[64];
        size_t len1 = (size_t)(nl - msg);
        if (len1 >= sizeof(line1)) {
            len1 = sizeof(line1) - 1;
        }
        memcpy(line1, msg, len1);
        line1[len1] = '\0';
        const char *line2 = nl + 1;
        render_text_centered(line1, 104, COL_BLACK);
        render_text_centered(line2, 124, COL_BLACK);
    } else {
        render_text_centered(msg, 114, COL_BLACK);
    }
}

void render_game_menu(uint8_t mode, uint8_t focused_pane, uint8_t sidebar_selected,
                      uint8_t slot_selected, bool is_in_game, unsigned t)
{
    perf_overlay_frame_start();
    scene_obscured();

    static const char *const in_game_items[] = {
        "Save Game", "Load Game", "Main Menu", "Help", "Quit", "Return"
    };
    static const char *const title_items[] = {
        "New Game", "Load Game", "Help", "Quit", "Return"
    };

    const char *const *items = is_in_game ? in_game_items : title_items;
    const uint8_t item_count = is_in_game ? 6 : 5;

    /* Animated background scroll */
    uint8_t phase = (uint8_t)((t / 120) % 50);
    if (!assets_title_bg(phase, phase, (uint8_t *)gfx_vbuffer)) {
        render_backdrop(COL_WHITE);
    }

    /* Left Navigation Sidebar */
    const int nav_w = 84;
    const int nav_edge_w = 6;
    gfx_SetColor(COL_NAV_FILL);
    gfx_FillRectangle(0, 0, nav_w - nav_edge_w, SCREEN_H);
    gfx_SetColor(COL_NAV_EDGE);
    gfx_FillRectangle(nav_w - nav_edge_w, 0, nav_edge_w, SCREEN_H);

    /* Left Header Title */
    const char *title = (mode == GAME_MENU_SAVE) ? "Save" : (mode == GAME_MENU_LOAD ? "Load" : "Menu");
    print_slice_outlined(title, strlen(title), 10, 14, COL_NAME, COL_BOX_FILL);

    /* Left Sidebar Menu Items */
    int start_y = is_in_game ? 80 : 96;
    for (uint8_t i = 0; i < item_count; i++) {
        int y = start_y + i * 18;
        bool is_cur = (focused_pane == PANE_SIDEBAR && i == sidebar_selected);
        uint8_t fill_col = is_cur ? COL_NAME : COL_WHITE;
        print_slice_outlined(items[i], strlen(items[i]), 8, y, fill_col, COL_BOX_FILL);
    }

    /* Right Main Panel: "Page 1" */
    int header_cx = 84 + (SCREEN_W - 84 - (int)string_width("Page 1")) / 2;
    render_text("Page 1", header_cx, 16, COL_BOX_FILL);

    /* 2x3 Save Slot Grid */
    const int slot_w = 68;
    const int slot_h = 58;
    const int col_x[3] = { 92, 166, 240 };
    const int row_y[2] = { 38, 118 };

    for (uint8_t s = 0; s < SAVE_SLOTS; s++) {
        uint8_t col = s % 3;
        uint8_t row = s / 3;
        int cx = col_x[col];
        int cy = row_y[row];
        bool is_selected = (focused_pane == PANE_GRID && s == slot_selected);
        bool has_save = save_exists(s + 1);

        /* Card background fill */
        gfx_SetColor(COL_NAV_FILL);
        gfx_FillRectangle(cx, cy, slot_w, slot_h);

        /* Card border */
        if (is_selected) {
            gfx_SetColor(COL_WHITE);
            gfx_Rectangle(cx, cy, slot_w, slot_h);
            gfx_Rectangle(cx - 1, cy - 1, slot_w + 2, slot_h + 2);
        } else {
            gfx_SetColor(COL_NAV_EDGE);
            gfx_Rectangle(cx, cy, slot_w, slot_h);
        }

        /* Card labels */
        char slot_hdr[12];
        sprintf(slot_hdr, "Slot %u", s + 1);
        int hdr_x = cx + (slot_w - (int)string_width(slot_hdr)) / 2;
        render_text(slot_hdr, hdr_x, cy + 12, is_selected ? COL_BOX_FILL : COL_WHITE);

        const char *status_str = has_save ? "Saved" : "empty slot";
        int stat_x = cx + (slot_w - (int)string_width(status_str)) / 2;
        render_text(status_str, stat_x, cy + 32, is_selected ? COL_NAME : COL_NAV_EDGE);
    }

    /* Page number footer */
    const char *page_str = "1  2  3  4  5  6  7  8  9";
    int page_x = 84 + (SCREEN_W - 84 - (int)string_width(page_str)) / 2;
    render_text(page_str, page_x, 204, COL_NAV_EDGE);
    /* Highlight '1' */
    render_text("1", page_x, 204, COL_BOX_FILL);

    /* Controls hint */
    const char *hint_str = "2nd: choose   Mode: cancel";
    int hint_x = 84 + (SCREEN_W - 84 - (int)string_width(hint_str)) / 2;
    render_text(hint_str, hint_x, 222, COL_BOX_FILL);
}

#define DISSOLVE_STEPS   8
#define DISSOLVE_STEP_MS 15

bool render_palette_matches(const uint16_t *palette)
{
    return palette != NULL && memcmp(current_palette, palette, sizeof(current_palette)) == 0;
}

void render_dissolve_out(void)
{
    fade_active = true;
    for (unsigned s = 1; s <= DISSOLVE_STEPS; s++) {
        fade_apply((uint8_t)(255U - (s * 255U) / DISSOLVE_STEPS));
        msleep(DISSOLVE_STEP_MS);
    }
}

void render_dissolve_in(void)
{
    for (unsigned s = 1; s <= DISSOLVE_STEPS; s++) {
        fade_apply((uint8_t)((s * 255U) / DISSOLVE_STEPS));
        msleep(DISSOLVE_STEP_MS);
    }
    memcpy(gfx_palette, current_palette, sizeof(current_palette));
    fade_active = false;
}

void render_dissolve_bayer(void)
{
    const uint8_t *vb = (const uint8_t *)gfx_vbuffer;
    gfx_SetDrawScreen();
    uint8_t *vs = (uint8_t *)gfx_vbuffer;
    /* Keep bottom dialogue box crisp before stippling scene region. */
    memcpy(vs + (size_t)SCENE_H * SCREEN_W, vb + (size_t)SCENE_H * SCREEN_W,
           (size_t)(SCREEN_H - SCENE_H) * SCREEN_W);
    for (uint8_t step = 0; step < 3; step++) {
        fast_bayer_dissolve(vs, vb, step);
        msleep(32);
    }
    gfx_SetDrawBuffer();
}

void render_wipe_curtain_out(void)
{
    gfx_SetDrawScreen();
    uint8_t *vs = (uint8_t *)gfx_vbuffer;
    const int strips = 6;
    const int strip_w = (SCREEN_W + strips - 1) / strips;
    for (int s = strips - 1; s >= 0; s--) {
        int x = s * strip_w;
        int w = (x + strip_w > SCREEN_W) ? (SCREEN_W - x) : strip_w;
        for (int y = 0; y < SCENE_H; y++) {
            memset(vs + (size_t)y * SCREEN_W + x, COL_BLACK, (size_t)w);
        }
        msleep(16);
    }
    gfx_SetDrawBuffer();
}

void render_wipe_left_reveal(void)
{
    const uint8_t *vb = (const uint8_t *)gfx_vbuffer;
    gfx_SetDrawScreen();
    uint8_t *vs = (uint8_t *)gfx_vbuffer;
    const int strips = 8;
    const int strip_w = SCREEN_W / strips; /* 40 px */
    memcpy(vs + (size_t)SCENE_H * SCREEN_W, vb + (size_t)SCENE_H * SCREEN_W,
           (size_t)(SCREEN_H - SCENE_H) * SCREEN_W);
    for (int s = strips - 1; s >= 0; s--) {
        int x = s * strip_w;
        fast_rect_blit(vs + x, SCREEN_W, vb + x, SCREEN_W, (size_t)strip_w, SCENE_H);
        msleep(16);
    }
    gfx_SetDrawBuffer();
}

void render_eyes_close_anim(void)
{
    gfx_SetDrawScreen();
    uint8_t *vs = (uint8_t *)gfx_vbuffer;
    for (int s = 1; s <= 6; s++) {
        int band = (s * (SCENE_H / 2)) / 6;
        memset(vs, COL_BLACK, (size_t)band * SCREEN_W);
        memset(vs + (size_t)(SCENE_H - band) * SCREEN_W, COL_BLACK, (size_t)band * SCREEN_W);
        msleep(22);
    }
    gfx_SetDrawBuffer();
}

void render_eyes_open_reveal(void)
{
    const uint8_t *vb = (const uint8_t *)gfx_vbuffer;
    gfx_SetDrawScreen();
    uint8_t *vs = (uint8_t *)gfx_vbuffer;
    memset(vs, COL_BLACK, (size_t)SCENE_H * SCREEN_W);
    memcpy(vs + (size_t)SCENE_H * SCREEN_W, vb + (size_t)SCENE_H * SCREEN_W,
           (size_t)(SCREEN_H - SCENE_H) * SCREEN_W);
    for (int s = 1; s <= 6; s++) {
        int half_h = (s * (SCENE_H / 2)) / 6;
        int y0 = (SCENE_H / 2) - half_h;
        size_t rows = (size_t)(half_h * 2);
        memcpy(vs + (size_t)y0 * SCREEN_W, vb + (size_t)y0 * SCREEN_W, rows * SCREEN_W);
        msleep(22);
    }
    gfx_SetDrawBuffer();
}

/* ---------------------------------------------------------------------------
 * Monika's Python Console Terminal Overlay (Act 2/3 & Credits)
 * ------------------------------------------------------------------------ */

#define CONSOLE_HIST_MAX 4
#define CONSOLE_LINE_MAX 48

static char console_hist[CONSOLE_HIST_MAX][CONSOLE_LINE_MAX];
static uint8_t console_hist_count;

/* Compact 3x5 monospace bitmaps (row-major 15 bits) for ASCII 0x20..0x7A.
 * Covers punctuation, digits, uppercase, and readable lowercase forms so
 * os.remove("characters/natsuki.chr") fits comfortably inside 204x46 px. */
static uint16_t console_glyph3x5(char ch)
{
    uint8_t c = (uint8_t)ch;
    static const uint16_t digits[10] = {
        31599, 11415, 29671, 29647, 23497, 31183, 31215, 29257, 31727, 31695
    };
    static const uint16_t upper[26] = {
        11245, 27566, 31015, 27502, 31143, 31140, 31087, 23533, 29847,  4719,
        23469, 18727, 24557, 27501, 31599, 27556, 31577, 27565, 31183, 29842,
        23407, 23402, 23549, 23213, 23186, 29351
    };
    static const uint16_t lower[26] = {
         1899, 18927,  3879,  5103,  2019, 14820, 15054, 18925,  8338,  4202,
        19373,  9361,  4077,  3949,  1386,  3964,  3961,  1828,  1678, 11921,
         2927,  2922,  2943,  2733,  2766,  3687
    };
    if (c >= '0' && c <= '9') return digits[c - '0'];
    if (c >= 'A' && c <= 'Z') return upper[c - 'A'];
    if (c >= 'a' && c <= 'z') return lower[c - 'a'];
    switch (c) {
        case ' ': return 0;
        case '!': return 9346;
        case '"': return 20480;
        case '#': return 11263;
        case '\'': return 8192;
        case '(': return 10530;
        case ')': return 8778;
        case '*': return 12096;
        case '+': return 1488;
        case ',': return 20;
        case '-': return 448;
        case '.': return 2;
        case '/': return 4772;
        case ':': return 1040;
        case ';': return 1044;
        case '<': return 5265;
        case '=': return 3640;
        case '>': return 17492;
        case '?': return 25218;
        case '[': return 26918;
        case '\\': return 18569;
        case ']': return 12875;
        case '_': return 7;
        default: return 0;
    }
}

static void console_draw_str(uint8_t *fb, const char *s, int x, int y,
                             int max_x, uint8_t ink)
{
    for (; *s && x + 3 <= max_x; s++, x += 4) {
        uint16_t bits = console_glyph3x5(*s);
        for (int row = 0; row < 5; row++) {
            for (int col = 0; col < 3; col++) {
                if (bits & (1U << (14 - row * 3 - col))) {
                    fb[(size_t)(y + row) * SCREEN_W + (size_t)(x + col)] = ink;
                }
            }
        }
    }
}

void render_console_clear(void)
{
    console_hist_count = 0;
    memset(console_hist, 0, sizeof(console_hist));
}

void render_console_hide(void)
{
    console_active = false;
}

bool render_console_is_active(void)
{
    return console_active;
}

void render_console_set(const char *cmd, const char *out, bool clear_first)
{
    (void)cmd;
    if (clear_first) {
        render_console_clear();
    }
    console_active = true;
    if (out && out[0] != '\0') {
        if (console_hist_count >= CONSOLE_HIST_MAX) {
            memmove(console_hist[0], console_hist[1],
                    (CONSOLE_HIST_MAX - 1) * CONSOLE_LINE_MAX);
            console_hist_count = CONSOLE_HIST_MAX - 1;
        }
        strncpy(console_hist[console_hist_count], out, CONSOLE_LINE_MAX - 1);
        console_hist[console_hist_count][CONSOLE_LINE_MAX - 1] = '\0';
        console_hist_count++;
    }
}

void render_console_draw_step(const char *typing_cmd, size_t visible_chars)
{
    if (!console_active) {
        return;
    }
    enum { CX = 2, CY = 2, CW = 204, CH = 46 };
    uint8_t *fb = (uint8_t *)gfx_vbuffer;

    /* Pick darkest paper and brightest ink from the live palette so the
     * terminal stays legible under every scene/CG/credits palette. */
    unsigned darkest = ~0U, brightest = 0;
    uint8_t paper = 0, ink = 255;
    for (unsigned i = 0; i < 256; i++) {
        unsigned c = gfx_palette[i];
        unsigned l = ((c >> 10) & 31U) * 3U + ((c >> 5) & 31U) * 6U + (c & 31U);
        if (l < darkest) { darkest = l; paper = (uint8_t)i; }
        if (l > brightest) { brightest = l; ink = (uint8_t)i; }
    }

    /* Translucent-look 75% stipple over scene background */
    for (int y = CY; y < CY + CH; y++) {
        uint8_t *row = fb + (size_t)y * SCREEN_W + CX;
        for (int x = 0; x < CW; x++) {
            if (((x + y) & 3) != 0) {
                row[x] = paper;
            }
        }
    }
    /* Crisp thin border */
    memset(fb + (size_t)CY * SCREEN_W + CX, ink, CW);
    memset(fb + (size_t)(CY + CH - 1) * SCREEN_W + CX, ink, CW);
    for (int y = CY; y < CY + CH; y++) {
        fb[(size_t)y * SCREEN_W + CX] = ink;
        fb[(size_t)y * SCREEN_W + CX + CW - 1] = ink;
    }

    /* Prompt row: "> <typing_cmd>_" */
    char prompt[CONSOLE_LINE_MAX + 4];
    prompt[0] = '>';
    prompt[1] = ' ';
    size_t plen = 2;
    if (typing_cmd) {
        size_t slen = strlen(typing_cmd);
        if (visible_chars < slen) {
            slen = visible_chars;
        }
        if (slen > CONSOLE_LINE_MAX - 2) {
            slen = CONSOLE_LINE_MAX - 2;
        }
        memcpy(prompt + plen, typing_cmd, slen);
        plen += slen;
    }
    prompt[plen++] = '_';
    prompt[plen] = '\0';
    console_draw_str(fb, prompt, CX + 4, CY + 4, CX + CW - 4, ink);

    /* Subtle divider */
    for (int x = CX + 3; x < CX + CW - 3; x += 2) {
        fb[(size_t)(CY + 11) * SCREEN_W + (size_t)x] = ink;
    }

    /* History rows */
    for (uint8_t i = 0; i < console_hist_count && i < CONSOLE_HIST_MAX; i++) {
        console_draw_str(fb, console_hist[i], CX + 4, CY + 14 + (int)i * 8,
                         CX + CW - 4, ink);
    }
}

