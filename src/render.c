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
#include <stdlib.h>
#include <string.h>
#include <time.h>

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
 * That scale isn't free, though (real per-frame pixel work + a malloc that
 * can fail alongside a large resident script chunk -- see its own file
 * comment in assets.c for the measured numbers), so zoom_fallback_offset()
 * below is kept, not deleted: it's the small vertical-rise approximation
 * this engine used exclusively before real scaling existed, now repurposed
 * as what draw_actor() falls back to on the frames assets_draw_sprite_zoomed()
 * can't run. */
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
 * correctly even across frames where the value goes unused. */
#define SINK_PX      5
#define SINK_DOWN_MS 500
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
    }
}

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

    /* feet anchored to the box edge, plus the one-shot hop if this Show
     * authored one and the persistent sink drift if it's currently sunk --
     * both the real zoom and its fallback anchor from this same feet_y, so
     * both apply identically either way. */
    int feet_y = SCENE_H;
    if (actor->flags & VN_FLAG_HOP) {
        feet_y += hop_offset(actor->character, actor->show_seq, t);
    }
    feet_y += sink_offset(actor->character, (actor->flags & VN_FLAG_SINK) != 0, t);

    /* One scratch buffer, sized for whichever of this character's layers is
     * larger, serves both of them -- and, crucially, decides for both at
     * once whether the real scale happens at all.
     *
     * Allocating per layer instead let the two disagree: the body atom is
     * by far the bigger allocation, so on a tight chunk it was the one that
     * failed, while the small expression atom right after it succeeded --
     * drawing a 1.05x head on a 1.00x body. Deciding once, before either
     * layer is drawn, makes that impossible: the character zooms whole or
     * not at all. A zero from assets_sprite_plain_size() (unresolvable id)
     * also disqualifies the pair, so no layer is ever handed a buffer sized
     * for the other one. */
    void *scratch = NULL;
    if (zoom_wanted) {
        size_t need = assets_sprite_plain_size(actor->sprite);
        if (need != 0 && actor->overlay != VN_NO_OVERLAY) {
            size_t over = assets_sprite_plain_size(actor->overlay);
            need = (over == 0) ? 0 : (over > need ? over : need);
        }
        if (need != 0) {
            scratch = malloc(need);
        }
    }

    /* The real scale can still be unavailable on a real device (no room for
     * that buffer alongside a large resident script chunk -- see assets.c's
     * file comment), which is why this doesn't just branch on zoom_wanted:
     * fall back to the plain draw, nudged by fallback_off, whenever the
     * real scale didn't actually happen, not only when it wasn't wanted. */
    if (!(scratch && assets_draw_sprite_zoomed(actor->sprite, center_x, feet_y, scratch))) {
        assets_draw_sprite(actor->sprite, center_x, feet_y + fallback_off);
    }

    /* Most actors are one flattened sprite (overlay == VN_NO_OVERLAY); a
     * layered one draws its expression atom second, at the same anchor --
     * its own (dx, dy) from DSPROFF is what places it correctly relative to
     * the body atom just drawn, at either scale. */
    if (actor->overlay != VN_NO_OVERLAY &&
        !(scratch && assets_draw_sprite_zoomed(actor->overlay, center_x, feet_y, scratch))) {
        assets_draw_sprite(actor->overlay, center_x, feet_y + fallback_off);
    }

    free(scratch);
}

/* ---------------------------------------------------------------------------
 * Public drawing
 * ------------------------------------------------------------------------ */

/* Whether the last render_scene() left every actor at rest, and whether one
 * has happened at all yet. Together these are what render_scene_lazy() reads
 * to decide it can skip -- see it below. */
static bool scene_settled;
static bool have_drawn_once;

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

    for (int i = 0; i < VN_MAX_CHARS; i++) {
        const vn_actor_t *actor = &scene->actors[i];
        if (actor->character != VN_NO_SPRITE) {
            draw_actor(actor, t);
        }
    }

    scene_settled   = !anim_moving;
    have_drawn_once = true;
}

void render_scene_lazy(const vn_scene_t *scene)
{
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

/* Character ids are fixed (compile_script.py's TAG_TO_CHAR), not part of
 * the imported data, so display names are a plain lookup table rather than
 * something assets.c needs to load. */
static const char *speaker_name(uint8_t speaker)
{
    static const char *const names[] = { "Sayori", "Natsuki", "Yuri", "Monika" };

    if (speaker == VN_SPEAKER_NONE ||
        speaker >= sizeof(names) / sizeof(names[0])) {
        return NULL;
    }
    return names[speaker];
}

void render_box(const vn_scene_t *scene, const char *text, size_t visible)
{
    const int pad = 6;

    gfx_SetColor(COL_BOX_FILL);
    gfx_FillRectangle_NoClip(0, BOX_Y, SCREEN_W, BOX_H);
    gfx_SetColor(COL_BOX_EDGE);
    gfx_HorizLine_NoClip(0, BOX_Y, SCREEN_W);

    int y = BOX_Y + 4;

    const char *name = speaker_name(scene->speaker);
    if (name != NULL) {
        gfx_SetTextFGColor(COL_NAME);
        gfx_PrintStringXY(name, pad, y);
        y += 11;
    }

    text_layout_t full, shown;
    text_wrap(&full, text, SCREEN_W - 2 * pad, measure, NULL);

    if (visible >= full.total) {
        shown = full;
    } else {
        text_clamp(&shown, &full, visible);
    }

    gfx_SetTextFGColor(COL_WHITE);
    for (uint8_t i = 0; i < shown.count; i++) {
        print_slice(shown.lines[i].start, shown.lines[i].len, pad, y);
        y += 10;
    }
}

void render_menu(const char *const *choices, uint8_t count, uint8_t selected)
{
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

void render_pause_box(int x, int y, int w, int h)
{
    gfx_SetColor(COL_BOX_FILL);
    gfx_FillRectangle_NoClip(x, y, w, h);
    gfx_SetColor(COL_BOX_EDGE);
    gfx_Rectangle_NoClip(x, y, w, h);
}
