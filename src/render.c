/**
 * @file render.c
 * @brief Scene composition via graphx. See render.h.
 */

#include "assets.h"
#include "render.h"
#include "text.h"

#include <graphx.h>

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
 * Placeholder art (Milestone 1)
 * ------------------------------------------------------------------------ */

static void draw_background(uint8_t bg)
{
    /* assets_scene() decompresses exactly SCENE_H rows of SCREEN_W palette-index
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
    const gfx_rletsprite_t *sprite = assets_sprite(actor->sprite);
    if (sprite == NULL) {
        return; /* missing/out-of-range sprite: skip rather than draw garbage */
    }

    const int x = pos_center(actor->pos) - sprite->width / 2;
    const int y = SCENE_H - sprite->height; /* feet anchored to the box edge */

    gfx_RLETSprite(sprite, x, y);
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

    gfx_SwapDraw();
    gfx_BlitScreen();
}
