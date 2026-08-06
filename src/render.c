/**
 * @file render.c
 * @brief Scene composition via graphx. See render.h.
 *
 * Milestone 1 draws placeholder geometry -- flat-colored backgrounds and
 * rounded sprite blocks -- so the layout, wrapping and input can be validated
 * before the asset pipeline exists. Once assets.c lands, only the two
 * draw_placeholder_* helpers here need to be replaced with real blits.
 */

#include "render.h"
#include "text.h"

#include <graphx.h>

#include <stdint.h>
#include <string.h>

/* ---------------------------------------------------------------------------
 * Palette
 * ------------------------------------------------------------------------ */

/* Reserved UI colors, mirroring the fixed-entries block the convimg palette
 * will pin at indices 0..7. */
static const uint16_t ui_palette[8] = {
    /* COL_TRANSPARENT */ 0x0000,
    /* COL_BLACK       */ 0x0000,
    /* COL_WHITE       */ 0xFFFF,
    /* COL_BOX_FILL    */ 0x5011, /* deep plum, DDLC-ish dialogue box */
    /* COL_BOX_EDGE    */ 0xFDF7, /* pale pink border                 */
    /* COL_NAME        */ 0xFE9B, /* name plate text                  */
    /* COL_HIGHLIGHT   */ 0xFFE0, /* selected menu entry              */
    /* COL_SHADOW      */ 0x2108,
};

void render_init(void)
{
    gfx_Begin();
    gfx_SetDefaultPalette(gfx_8bpp);

    for (unsigned i = 0; i < 8; i++) {
        gfx_palette[i] = ui_palette[i];
    }

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

/* Distinct flat colors keyed off the background id, so scene changes are
 * visibly distinguishable while running on placeholder data. */
static void draw_placeholder_background(uint8_t bg)
{
    if (bg == VN_NO_SPRITE) {
        gfx_FillRectangle_NoClip(0, 0, SCREEN_W, SCENE_H);
        return;
    }

    static const uint16_t tints[] = {
        0x6B7A, /* classroom  */ 0x4E8C, /* clubroom */
        0x8C31, /* corridor   */ 0x35B6, /* bedroom  */
        0xA5FA, /* residential*/ 0x2966, /* night    */
    };

    gfx_palette[8] = tints[bg % (sizeof(tints) / sizeof(tints[0]))];
    gfx_SetColor(8);
    gfx_FillRectangle_NoClip(0, 0, SCREEN_W, SCENE_H);

    /* A horizon line gives the flat fill some sense of depth. */
    gfx_SetColor(COL_SHADOW);
    gfx_FillRectangle_NoClip(0, SCENE_H - 40, SCREEN_W, 40);
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

static void draw_placeholder_actor(const vn_actor_t *actor)
{
    const int w = 72;
    const int h = 150;
    const int x = pos_center(actor->pos) - w / 2;
    const int y = SCENE_H - h;   /* feet anchored to the box edge */

    /* One hue per character, each in its OWN palette index (9..12). gfx_palette
     * is a direct pointer into hardware palette memory, not a per-draw value --
     * writing the same index for every actor recolors every pixel already on
     * screen that uses it, which is what caused two on-screen actors to flash
     * and swap colors as each other's OP_SHOW ran. One index per character,
     * indexed by character id rather than by draw order, keeps them stable. */
    static const uint16_t hues[] = { 0xFD59, 0x64BF, 0xF9CC, 0x2E8B };
    const uint8_t idx = (uint8_t)(9 + (actor->character % (sizeof(hues) / sizeof(hues[0]))));

    gfx_palette[idx] = hues[actor->character % (sizeof(hues) / sizeof(hues[0]))];
    gfx_SetColor(idx);
    gfx_FillRectangle(x, y, w, h);

    gfx_SetColor(COL_BLACK);
    gfx_Rectangle(x, y, w, h);

    /* Sprite id, so OP_SHOW's operand is verifiable on screen. */
    gfx_SetTextFGColor(COL_BLACK);
    gfx_SetTextXY(x + 6, y + 6);
    gfx_PrintUInt(actor->sprite, 1);
}

/* ---------------------------------------------------------------------------
 * Public drawing
 * ------------------------------------------------------------------------ */

void render_scene(const vn_scene_t *scene)
{
    gfx_SetColor(COL_BLACK);
    draw_placeholder_background(scene->background);

    for (int i = 0; i < VN_MAX_CHARS; i++) {
        if (scene->actors[i].character != VN_NO_SPRITE) {
            draw_placeholder_actor(&scene->actors[i]);
        }
    }
}

/** Placeholder speaker names until the pipeline supplies the real table. */
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
