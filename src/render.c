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
#include <string.h>

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
 * Scene art
 * ------------------------------------------------------------------------ */

/* Idle "breathing" bob: DDLC runs a real per-frame ATL transform for this
 * that this engine doesn't interpret (see compile_script.py's position
 * comment for the general ATL gap). This is a cheap stand-in, not a
 * reproduction of the original curve: a small sine table, offset a quarter
 * cycle per character slot so multiple actors don't bob in lockstep. */
#define BOB_LUT_SIZE    32
#define BOB_SPEED_DIV   2  /* frames per LUT step -- lower = faster bob */

static const int8_t bob_lut[BOB_LUT_SIZE] = {
     0,  0,  1,  1,  2,  2,  2,  2,  2,  2,  2,  2,  2,  1,  1,  0,
     0,  0, -1, -1, -2, -2, -2, -2, -2, -2, -2, -2, -2, -1, -1,  0,
};

static unsigned frame_counter;

static int bob_offset(uint8_t character)
{
    unsigned step = frame_counter / BOB_SPEED_DIV
                  + (unsigned)character * (BOB_LUT_SIZE / 4);
    return bob_lut[step % BOB_LUT_SIZE];
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

/** Horizontal center for each enum vn_pos anchor. */
static int pos_center(uint8_t pos)
{
    switch (pos) {
        case POS_FARLEFT:  return SCREEN_W / 6;
        case POS_LEFT:     return SCREEN_W / 3;
        case POS_RIGHT:    return (SCREEN_W * 2) / 3;
        case POS_FARRIGHT: return (SCREEN_W * 5) / 6;
        case POS_CENTER:
        default:           return SCREEN_W / 2;
    }
}

static void draw_actor(const vn_actor_t *actor)
{
    /* feet anchored to the box edge (plus the idle bob); assets_draw_sprite
     * centers on width once it knows it (only it has the sprite's AppVar
     * open to check) */
    assets_draw_sprite(actor->sprite, pos_center(actor->pos),
                       SCENE_H + bob_offset(actor->character));
}

/* ---------------------------------------------------------------------------
 * Public drawing
 * ------------------------------------------------------------------------ */

void render_scene(const vn_scene_t *scene)
{
    draw_background(scene->background);

    for (int i = 0; i < VN_MAX_CHARS; i++) {
        if (scene->actors[i].character != VN_NO_SPRITE) {
            draw_actor(&scene->actors[i]);
        }
    }
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

    frame_counter++;
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

/* Remaining-offset curves, 255 (fully displaced) -> 0 (arrived), sampled 32
 * ways. Integer tables rather than float easing: this runs per element per
 * frame, and the eZ80 has no FPU. Max amplitude is 300, so 300*255 stays well
 * inside a 24-bit int -- no overflow, no division. */
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

void render_pause_box(int x, int y, int w, int h)
{
    gfx_SetColor(COL_BOX_FILL);
    gfx_FillRectangle_NoClip(x, y, w, h);
    gfx_SetColor(COL_BOX_EDGE);
    gfx_Rectangle_NoClip(x, y, w, h);
}
