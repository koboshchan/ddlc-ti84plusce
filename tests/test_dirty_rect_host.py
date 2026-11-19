#!/usr/bin/env python3
"""Host byte-for-byte test comparing src/render.c dirty-rect composition
against full redraws across a scripted sequence of actor changes."""

from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* ---- Fake clock driven deterministically by the test script ---- */
static clock_t fake_clock_ticks = 0;
#define clock() fake_clock_ticks

/* ---- Stub graphx + fontlibc + timers ---- */
#define SCREEN_W 320
#define SCREEN_H 240
#define SCENE_H  180

static uint8_t fb_buf[2][SCREEN_W * SCREEN_H];
static uint8_t draw_idx = 1;
static int clip_x0 = 0, clip_y0 = 0, clip_x1 = SCREEN_W, clip_y1 = SCREEN_H;
static uint8_t cur_color = 0;
uint16_t gfx_palette[256];

#define gfx_vbuffer (fb_buf[draw_idx])
#define gfx_screen  0
#define gfx_buffer  1

static void gfx_Begin(void) {}
static void gfx_End(void) {}
static void gfx_SetDrawBuffer(void) { draw_idx = 1; }
static void gfx_SetDrawScreen(void) { draw_idx = 0; }
static uint8_t gfx_GetDraw(void) { return draw_idx; }
static void gfx_SetDraw(uint8_t d) { draw_idx = d; }
static void gfx_SetTransparentColor(uint8_t c) { (void)c; }
static void gfx_Wait(void) {}
static uint16_t gfx_Darken(uint16_t c, uint8_t a) { (void)a; return c; }
static void msleep(uint16_t ms) { (void)ms; }
static void gfx_SetColor(uint8_t c) { cur_color = c; }
static void gfx_SetClipRegion(int x0, int y0, int x1, int y1)
{
    clip_x0 = x0;
    clip_y0 = y0;
    clip_x1 = x1;
    clip_y1 = y1;
}

static void gfx_FillRectangle_NoClip(int x, int y, int w, int h)
{
    for (int r = 0; r < h; r++) {
        int yy = y + r;
        if (yy < 0 || yy >= SCREEN_H) {
            continue;
        }
        int x0 = x < 0 ? 0 : x;
        int x1 = (x + w) > SCREEN_W ? SCREEN_W : (x + w);
        if (x0 < x1) {
            memset(&fb_buf[draw_idx][yy * SCREEN_W + x0], cur_color, (size_t)(x1 - x0));
        }
    }
}

static void gfx_FillRectangle(int x, int y, int w, int h)
{
    gfx_FillRectangle_NoClip(x, y, w, h);
}

static void gfx_HorizLine_NoClip(int x, int y, int w)
{
    gfx_FillRectangle_NoClip(x, y, w, 1);
}

static void gfx_Rectangle_NoClip(int x, int y, int w, int h)
{
    gfx_FillRectangle_NoClip(x, y, w, 1);
    gfx_FillRectangle_NoClip(x, y + h - 1, w, 1);
    gfx_FillRectangle_NoClip(x, y, 1, h);
    gfx_FillRectangle_NoClip(x + w - 1, y, 1, h);
}

static void gfx_Rectangle(int x, int y, int w, int h)
{
    gfx_Rectangle_NoClip(x, y, w, h);
}

static void gfx_SwapDraw(void)
{
    draw_idx ^= 1u;
}

static void gfx_BlitScreen(void)
{
    memcpy(fb_buf[draw_idx], fb_buf[draw_idx ^ 1u], SCREEN_W * SCREEN_H);
}

static void gfx_BlitLines(uint8_t src, uint8_t y, uint8_t h)
{
    (void)src;
    if (h == 0) {
        return;
    }
    memcpy(&fb_buf[draw_idx][(size_t)y * SCREEN_W],
           &fb_buf[draw_idx ^ 1u][(size_t)y * SCREEN_W],
           (size_t)h * SCREEN_W);
}

/* ---- Minimal fontlibc mock (renders deterministic 6x8 glyphs) ---- */
typedef struct { uint8_t id; } fontlib_font_t;
static fontlib_font_t mock_fonts[4] = { {0}, {1}, {2}, {3} };
static unsigned font_cx, font_cy;
static uint8_t font_fg = 2;
static int font_italic = 0;

static void fontlib_SetTransparency(bool t) { (void)t; }
static fontlib_font_t *fontlib_GetFontByIndex(const char *n, uint8_t i)
{
    (void)n;
    return i < 4 ? &mock_fonts[i] : NULL;
}
static void fontlib_SetFont(fontlib_font_t *f, int fl) { (void)f; (void)fl; }
static uint8_t fontlib_GetGlyphWidth(uint8_t c) { (void)c; return 6; }
static uint8_t fontlib_GetCurrentFontHeight(void) { return 8; }
static void fontlib_SetItalicSpacingAdjustment(int a) { font_italic = a; }
static void fontlib_SetForegroundColor(uint8_t c) { font_fg = c; }
static void fontlib_SetCursorPosition(unsigned x, uint8_t y)
{
    font_cx = x;
    font_cy = y;
}
static void fontlib_DrawStringL(const char *s, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        uint8_t ch = (uint8_t)s[i];
        int bx = (int)font_cx;
        int by = (int)font_cy;
        for (int r = 0; r < 6; r++) {
            for (int c = 0; c < 5; c++) {
                if (((ch + r * 7 + c * 3) & 3) == 0) {
                    int px = bx + c;
                    int py = by + r;
                    if (px >= 0 && px < SCREEN_W && py >= 0 && py < SCREEN_H) {
                        fb_buf[draw_idx][py * SCREEN_W + px] = font_fg;
                    }
                }
            }
        }
        font_cx += (unsigned)(6 - font_italic);
    }
}
static void fontlib_DrawString(const char *s)
{
    fontlib_DrawStringL(s, strlen(s));
}

/* ---- Stub headers satisfied before including render.c ---- */
#include "vn.h"
#include "chars.h"
#include "save.h"
#include "text.h"
#include "render.h"

/* fast_ops stubs */
void fast_rect_blit(uint8_t *dest, size_t dest_pitch,
                    const uint8_t *src, size_t src_pitch,
                    size_t w, size_t h)
{
    for (size_t r = 0; r < h; r++) {
        memcpy(dest + r * dest_pitch, src + r * src_pitch, w);
    }
}
void fast_row_shift(uint8_t *row, uint8_t *scratch, size_t off)
{
    memcpy(scratch, row, SCREEN_W);
    memcpy(row, scratch + SCREEN_W - off, off);
    memcpy(row + off, scratch, SCREEN_W - off);
}
void fast_bayer_dissolve(uint8_t *d, const uint8_t *s, uint8_t step)
{
    (void)step;
    memcpy(d, s, SCREEN_W * SCENE_H);
}
void fast_stretch_row(uint8_t *dst, const uint8_t *src, size_t dw, size_t sw)
{
    for (size_t x = 0; x < dw; x++) {
        dst[x] = src[(x * sw) / dw];
    }
}
void fast_remap_bytes(uint8_t *buf, const uint8_t *lut, uint16_t count)
{
    for (uint16_t i = 0; i < count; i++) {
        buf[i] = lut[buf[i]];
    }
}

/* noise / ch5 / cgpack stubs */
void noise_release(void) {}
void noise_restore(void) {}
bool noise_is_fullscreen(void) { return false; }
bool ch5_motion_active(uint8_t bg) { (void)bg; return false; }
bool ch5_motion_draw(uint8_t bg) { (void)bg; return false; }
bool ch5_motion_sync_clock(uint8_t bg) { (void)bg; return false; }
uint32_t ch5_motion_elapsed_ms(uint8_t bg) { (void)bg; return 0; }
#include "cgpack.h"
cgpack_status_t cgpack_status(void) { return (cgpack_status_t)0; }
bool cgpack_select_timed_frame(uint8_t b, uint32_t e, uint8_t *f, uint16_t *i, uint32_t *t)
{
    (void)b; (void)e; (void)f; (void)i; (void)t;
    return false;
}
bool cgpack_read_timed_frame(uint8_t b, uint16_t i, uint32_t t, uint8_t *f, uint16_t *p)
{
    (void)b; (void)i; (void)t; (void)f; (void)p;
    return false;
}
void save_slot_summary(uint8_t s, char *b, size_t n) { (void)s; if (n) b[0] = 0; }
bool save_exists(uint8_t s) { (void)s; return false; }

/* ---- Synthetic backgrounds & layered sprites with telemetry counters ---- */
#define NUM_BGS 3
static uint8_t fake_bgs[NUM_BGS][SCREEN_W * SCENE_H];
static bool    fake_bg_raw[NUM_BGS] = { true, true, false };

typedef struct {
    int w;
    int h;
    int dx;
    int dy;
    uint8_t pixels[128 * 180];
} fake_spr_t;

#define NUM_SPRS 10
static fake_spr_t fake_sprs[NUM_SPRS];

static unsigned stat_full_bg = 0;
static unsigned stat_bg_rect = 0;
static unsigned stat_spr_draws = 0;
static unsigned stat_box_draws = 0;
static unsigned stat_blit_screen = 0;
static unsigned stat_blit_lines = 0;

static void reset_stats(void)
{
    stat_full_bg = 0;
    stat_bg_rect = 0;
    stat_spr_draws = 0;
    stat_box_draws = 0;
    stat_blit_screen = 0;
    stat_blit_lines = 0;
}

static void init_fake_assets(void)
{
    for (int i = 0; i < 256; i++) {
        gfx_palette[i] = (uint16_t)(i * 257);
    }
    gfx_palette[COL_BLACK] = 0x0000;
    gfx_palette[COL_WHITE] = 0xFFFF;

    for (int b = 0; b < NUM_BGS; b++) {
        for (int y = 0; y < SCENE_H; y++) {
            for (int x = 0; x < SCREEN_W; x++) {
                uint8_t v = (uint8_t)(16 + ((x * 3 + y * 5 + b * 41) % 200));
                fake_bgs[b][y * SCREEN_W + x] = v;
            }
        }
    }

    /* Bodies: 0 (Sayori), 1 (Yuri), 2 (Natsuki), 3 (Natsuki pose 2)
     * Faces:  4 (Sayori A), 5 (Sayori B), 6 (Yuri A), 7 (Yuri B),
     *         8 (Natsuki A), 9 (Natsuki B - wider face) */
    struct { int w, h, dx, dy; uint8_t base; bool oval; } defs[NUM_SPRS] = {
        { 96, 170,  0,  0,  50, false }, /* 0: body 0 */
        { 92, 174, -2,  0,  90, false }, /* 1: body 1 */
        { 88, 162,  3,  0, 130, false }, /* 2: body 2 */
        { 94, 166, -1,  0, 160, false }, /* 3: body 2 alt pose */
        { 42,  46,  0, -118, 190, true }, /* 4: face 0a */
        { 42,  46,  1, -118, 200, true }, /* 5: face 0b */
        { 44,  48, -2, -120, 210, true }, /* 6: face 1a */
        { 44,  48, -2, -120, 220, true }, /* 7: face 1b */
        { 38,  42,  2, -110, 230, true }, /* 8: face 2a */
        { 46,  44,  2, -110, 240, true }, /* 9: face 2b wider */
    };
    for (int s = 0; s < NUM_SPRS; s++) {
        fake_sprs[s].w = defs[s].w;
        fake_sprs[s].h = defs[s].h;
        fake_sprs[s].dx = defs[s].dx;
        fake_sprs[s].dy = defs[s].dy;
        int cx = defs[s].w / 2;
        int cy = defs[s].h / 2;
        for (int y = 0; y < defs[s].h; y++) {
            for (int x = 0; x < defs[s].w; x++) {
                /* Give non-trivial transparency silhouette + interior pattern */
                int rx = x - cx;
                int ry = y - cy;
                bool opaque;
                if (defs[s].oval) {
                    opaque = (rx * rx * 4 + ry * ry * 4) <= (defs[s].w * defs[s].w);
                } else {
                    int margin = (y < 30) ? (30 - y) / 2 : ((y > 140) ? 4 : 8);
                    opaque = (x >= margin && x < defs[s].w - margin && ((x + y) % 19) != 0);
                }
                fake_sprs[s].pixels[y * defs[s].w + x] =
                    opaque ? (uint8_t)(defs[s].base + ((x * 5 + y * 3) & 7)) : 0;
            }
        }
    }
}

/* Mock assets_* functions matching src/assets.h */
static int s_clip_x0 = 0, s_clip_y0 = 0, s_clip_x1 = SCREEN_W, s_clip_y1 = SCREEN_H;

void assets_set_clip_rect(int x0, int y0, int x1, int y1)
{
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > SCREEN_W) x1 = SCREEN_W;
    if (y1 > SCREEN_H) y1 = SCREEN_H;
    s_clip_x0 = x0;
    s_clip_y0 = y0;
    s_clip_x1 = x1;
    s_clip_y1 = y1;
    gfx_SetClipRegion(x0, y0, x1, y1);
}

void assets_reset_clip_rect(void)
{
    assets_set_clip_rect(0, 0, SCREEN_W, SCREEN_H);
}

bool assets_scene_fullscreen(uint8_t id) { (void)id; return false; }

bool assets_scene(uint8_t id, uint8_t *dest)
{
    if (id >= NUM_BGS) {
        return false;
    }
    stat_full_bg++;
    memcpy(dest, fake_bgs[id], SCREEN_W * SCENE_H);
    return true;
}

bool assets_scene_has_bg_rect(uint8_t id)
{
    if (id == VN_NO_SPRITE) {
        return true;
    }
    return id < NUM_BGS && fake_bg_raw[id];
}

bool assets_scene_bg_rect(uint8_t id, int x, int y, int w, int h, uint8_t *dest)
{
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > SCREEN_W) w = SCREEN_W - x;
    if (y + h > SCENE_H) h = SCENE_H - y;
    if (w <= 0 || h <= 0) return true;
    if (id == VN_NO_SPRITE) {
        stat_bg_rect++;
        for (int r = 0; r < h; r++) {
            memset(dest + (size_t)(y + r) * SCREEN_W + (size_t)x, COL_BLACK, (size_t)w);
        }
        return true;
    }
    if (id >= NUM_BGS || !fake_bg_raw[id]) {
        return false;
    }
    stat_bg_rect++;
    fast_rect_blit(dest + (size_t)y * SCREEN_W + (size_t)x, SCREEN_W,
                   fake_bgs[id] + (size_t)y * SCREEN_W + (size_t)x, SCREEN_W,
                   (size_t)w, (size_t)h);
    return true;
}

bool assets_sprite_bounds(uint16_t id, int *w, int *h, int *dx, int *dy)
{
    if (id >= NUM_SPRS) {
        return false;
    }
    *w = fake_sprs[id].w;
    *h = fake_sprs[id].h;
    *dx = fake_sprs[id].dx;
    *dy = fake_sprs[id].dy;
    return true;
}

bool assets_zoom_prepare(uint16_t id) { return id < NUM_SPRS; }
void assets_sprite_end(void) {}
void assets_sprite_cache_clear(void) {}
void assets_box_end(void) {}

bool assets_draw_sprite(uint16_t id, int center_x, int feet_y)
{
    if (id >= NUM_SPRS) {
        return false;
    }
    stat_spr_draws++;
    const fake_spr_t *s = &fake_sprs[id];
    int dst_x = center_x - s->w / 2 + s->dx;
    int dst_y = feet_y - s->h + s->dy;
    for (int y = 0; y < s->h; y++) {
        int py = dst_y + y;
        if (py < s_clip_y0 || py >= s_clip_y1) {
            continue;
        }
        for (int x = 0; x < s->w; x++) {
            int px = dst_x + x;
            if (px < s_clip_x0 || px >= s_clip_x1) {
                continue;
            }
            uint8_t pix = s->pixels[y * s->w + x];
            if (pix != 0) {
                fb_buf[draw_idx][py * SCREEN_W + px] = pix;
            }
        }
    }
    return true;
}

bool assets_draw_sprite_scaled(uint16_t id, int center_x, int feet_y, unsigned int scale_permille)
{
    if (id >= NUM_SPRS) {
        return false;
    }
    stat_spr_draws++;
    const fake_spr_t *s = &fake_sprs[id];
    int src_w = s->w, src_h = s->h;
    int dst_w = (int)(((uint32_t)src_w * scale_permille + 500) / 1000);
    int dst_h = (int)(((uint32_t)src_h * scale_permille + 500) / 1000);
    int zdx = (s->dx >= 0) ? (int)(((uint32_t)s->dx * scale_permille + 500) / 1000)
                           : -(int)(((uint32_t)(-s->dx) * scale_permille + 500) / 1000);
    int zdy = (s->dy >= 0) ? (int)(((uint32_t)s->dy * scale_permille + 500) / 1000)
                           : -(int)(((uint32_t)(-s->dy) * scale_permille + 500) / 1000);
    int dst_x = center_x - dst_w / 2 + zdx;
    int dst_y = feet_y - dst_h + zdy;

    int x0 = dst_x > s_clip_x0 ? dst_x : s_clip_x0;
    int x1 = (dst_x + dst_w) < s_clip_x1 ? (dst_x + dst_w) : s_clip_x1;
    int y0 = dst_y > s_clip_y0 ? dst_y : s_clip_y0;
    int y1 = (dst_y + dst_h) < s_clip_y1 ? (dst_y + dst_h) : s_clip_y1;
    if (x0 >= x1 || y0 >= y1) {
        return true;
    }

    int start_dx = x0 - dst_x;
    int start_dy = y0 - dst_y;
    uint32_t init_num_x = (uint32_t)start_dx * (uint32_t)src_w + (uint32_t)(dst_w / 2);
    int start_sx = (int)(init_num_x / (uint32_t)dst_w);
    uint32_t init_acc_x = init_num_x % (uint32_t)dst_w;

    uint32_t init_num_y = (uint32_t)start_dy * (uint32_t)src_h + (uint32_t)(dst_h / 2);
    int cur_sy = (int)(init_num_y / (uint32_t)dst_h);
    uint32_t acc_y = init_num_y % (uint32_t)dst_h;

    for (int py = y0; py < y1; py++) {
        int sy = cur_sy < src_h ? cur_sy : (src_h - 1);
        int sx = start_sx;
        uint32_t ax = init_acc_x;
        for (int px = x0; px < x1; px++) {
            uint8_t pix = s->pixels[sy * src_w + sx];
            if (pix != 0) {
                fb_buf[draw_idx][py * SCREEN_W + px] = pix;
            }
            ax += (uint32_t)src_w;
            while (ax >= (uint32_t)dst_w) {
                ax -= (uint32_t)dst_w;
                sx++;
            }
        }
        acc_y += (uint32_t)src_h;
        while (acc_y >= (uint32_t)dst_h) {
            acc_y -= (uint32_t)dst_h;
            cur_sy++;
        }
    }
    return true;
}

bool assets_draw_box(bool namebox, uint8_t *dest)
{
    stat_box_draws++;
    int w = namebox ? NAMEBOX_W : TEXTBOX_W;
    int h = namebox ? NAMEBOX_H : TEXTBOX_H;
    uint8_t fill = namebox ? 14 : 12;
    for (int r = 0; r < h; r++) {
        memset(dest + (size_t)r * SCREEN_W, fill, (size_t)w);
    }
    return true;
}

bool assets_title_layout(uint8_t id, int *x, int *y, int *dx, int *dy)
{
    (void)id; (void)x; (void)y; (void)dx; (void)dy;
    return false;
}
bool assets_draw_title(uint8_t id, int x, int y) { (void)id; (void)x; (void)y; return false; }
void assets_title_end(void) {}
bool assets_title_bg(uint8_t px, uint8_t py, uint8_t *dest) { (void)px; (void)py; (void)dest; return false; }
const uint16_t *assets_scene_palette(uint8_t id) { (void)id; return gfx_palette; }

/* Include real text.c and render.c directly */
#include "text.c"
#include "render.c"

static uint8_t snap_dirty[SCREEN_W * SCREEN_H];

static void verify_step(const char *label, const vn_scene_t *scene,
                        const char *speaker, const char *dialogue,
                        bool expect_dirty, bool expect_skip)
{
    reset_stats();
    render_scene(scene);
    render_box(scene, speaker, dialogue, SIZE_MAX);
    render_present(TRANS_CUT);

    if (expect_skip) {
        if (stat_full_bg != 0 || stat_bg_rect != 0 || stat_spr_draws != 0) {
            fprintf(stderr, "FAIL %s: expected SKIP (full=%u rect=%u spr=%u)\n",
                    label, stat_full_bg, stat_bg_rect, stat_spr_draws);
            exit(1);
        }
    } else if (expect_dirty) {
        if (stat_full_bg != 0 || stat_bg_rect == 0) {
            fprintf(stderr, "FAIL %s: expected DIRTY (full=%u rect=%u spr=%u)\n",
                    label, stat_full_bg, stat_bg_rect, stat_spr_draws);
            exit(1);
        }
    } else {
        if (stat_full_bg == 0) {
            fprintf(stderr, "FAIL %s: expected FULL (full=%u rect=%u)\n",
                    label, stat_full_bg, stat_bg_rect);
            exit(1);
        }
    }

    /* Snapshot visible + back buffer and assert both framebuffers stayed in sync */
    memcpy(snap_dirty, fb_buf[draw_idx ^ 1u], sizeof(snap_dirty));
    if (memcmp(fb_buf[0], fb_buf[1], sizeof(snap_dirty)) != 0) {
        fprintf(stderr, "FAIL %s: front and back buffers out of sync after present!\n", label);
        exit(1);
    }

    /* Now force a clean FULL redraw at the exact same fake_clock_ticks and compare byte-for-byte! */
    render_invalidate_scene();
    memset(fb_buf[draw_idx], 0xAA, SCREEN_W * SCREEN_H);
    render_scene(scene);
    render_box(scene, speaker, dialogue, SIZE_MAX);
    render_present(TRANS_CUT);

    if (memcmp(snap_dirty, fb_buf[draw_idx ^ 1u], sizeof(snap_dirty)) != 0) {
        for (size_t i = 0; i < SCREEN_W * SCREEN_H; i++) {
            if (snap_dirty[i] != fb_buf[draw_idx ^ 1u][i]) {
                fprintf(stderr, "MISMATCH %s at (%zu, %zu): dirty=%u full=%u\n",
                        label, i % SCREEN_W, i / SCREEN_W,
                        snap_dirty[i], fb_buf[draw_idx ^ 1u][i]);
                exit(1);
            }
        }
    }
}

static void advance_ms(unsigned ms)
{
    fake_clock_ticks += (clock_t)(((uint64_t)ms * (uint64_t)CLOCKS_PER_SEC) / 1000ULL);
}

int main(void)
{
    init_fake_assets();
    render_init();

    vn_scene_t scene;
    memset(&scene, 0, sizeof(scene));
    scene.background = 0;
    for (int i = 0; i < VN_MAX_CHARS; i++) {
        scene.actors[i].character = VN_NO_SPRITE;
        scene.actors[i].overlay = VN_NO_OVERLAY;
    }

    /* 1. Initial BG + 1 actor (FULL) */
    scene.actors[0].character = 0;
    scene.actors[0].sprite = 0;
    scene.actors[0].overlay = 4;
    scene.actors[0].pos = 48; /* x = 96 */
    scene.actors[0].show_seq = 1;
    verify_step("1_initial_1actor", &scene, "Sayori", "Welcome to the club!", false, false);

    /* 2. Actor 1 enters overlapping Actor 0 (DIRTY) */
    scene.actors[1].character = 1;
    scene.actors[1].sprite = 1;
    scene.actors[1].overlay = 6;
    scene.actors[1].pos = 76; /* x = 152, overlaps Actor 0 */
    scene.actors[1].show_seq = 1;
    verify_step("2_enter_actor1_overlap", &scene, "Yuri", "Nice to meet you.", true, false);

    /* 3. Actor 2 enters overlapping Actor 1 (DIRTY) */
    scene.actors[2].character = 2;
    scene.actors[2].sprite = 2;
    scene.actors[2].overlay = 8;
    scene.actors[2].pos = 104; /* x = 208, overlaps Actor 1 */
    scene.actors[2].show_seq = 1;
    verify_step("3_enter_actor2_overlap", &scene, "Natsuki", "Seriously?", true, false);

    /* 4. Actor 1 expression swap (face only, DIRTY) */
    scene.actors[1].overlay = 7;
    verify_step("4_expr_swap_actor1", &scene, "Yuri", "I brought tea.", true, false);

    /* 5. Actor 0 expression swap while overlapped by Actor 1 (z-order test, DIRTY) */
    scene.actors[0].overlay = 5;
    verify_step("5_expr_swap_actor0_behind", &scene, "Sayori", "Yay, tea!", true, false);

    /* 6. Actor 2 body + wider face change (DIRTY) */
    scene.actors[2].sprite = 3;
    scene.actors[2].overlay = 9;
    verify_step("6_pose_change_actor2", &scene, "Natsuki", "I made cupcakes!", true, false);

    /* 7. Actor 1 hop animation across multiple ticks (SKIP at t=0, DIRTY on moving frames!) */
    scene.actors[1].flags = VN_FLAG_HOP;
    scene.actors[1].show_seq = 2;
    verify_step("7a_hop_t0", &scene, "Yuri", "Oh!", false, true);
    advance_ms(60);
    verify_step("7b_hop_t60", &scene, "Yuri", "Oh!", true, false);
    advance_ms(60);
    verify_step("7c_hop_t120", &scene, "Yuri", "Oh!", true, false);
    advance_ms(120);
    verify_step("7d_hop_settled", &scene, "Yuri", "Oh!", true, false);
    scene.actors[1].flags = 0;

    /* 8. Actor 0 speaking zoom 1.00 -> 1.05 across multiple ticks (scaled clip test!) */
    scene.actors[0].flags = VN_FLAG_ZOOM;
    verify_step("8a_zoom_start", &scene, "Sayori", "Let's read poems!", false, true);
    advance_ms(90);
    verify_step("8b_zoom_mid", &scene, "Sayori", "Let's read poems!", true, false);
    advance_ms(200);
    verify_step("8c_zoom_full", &scene, "Sayori", "Let's read poems!", true, false);

    /* 9. Actor 2 sink animation down and back up */
    scene.actors[2].flags = VN_FLAG_SINK;
    verify_step("9a_sink_start", &scene, "Natsuki", "Hmph...", false, true);
    advance_ms(220);
    verify_step("9b_sink_mid", &scene, "Natsuki", "Hmph...", true, false);
    advance_ms(350);
    verify_step("9c_sink_done", &scene, "Natsuki", "Hmph...", true, false);
    scene.actors[2].flags = 0;
    verify_step("9d_unsink_start", &scene, "Natsuki", "Okay fine.", false, true);
    advance_ms(200);
    verify_step("9e_unsink_done", &scene, "Natsuki", "Okay fine.", true, false);

    /* 10. Actor 1 position change + LEFTIN horizontal slide easing */
    scene.actors[1].pos = 90; /* x = 180 */
    verify_step("10a_pos_move", &scene, "Yuri", "Excuse me.", true, false);
    scene.actors[1].flags = VN_FLAG_LEFTIN;
    scene.actors[1].show_seq = 3;
    verify_step("10b_leftin_t0", &scene, "Yuri", "Excuse me.", true, false);
    advance_ms(90);
    verify_step("10c_leftin_mid", &scene, "Yuri", "Excuse me.", true, false);
    advance_ms(200);
    verify_step("10d_leftin_done", &scene, "Yuri", "Excuse me.", true, false);
    scene.actors[1].flags = 0;

    /* 11. Actor 1 exits (restores clean BG + Actor 0 + Actor 2!) */
    scene.actors[1].character = VN_NO_SPRITE;
    verify_step("11_exit_actor1", &scene, "Sayori", "See you tomorrow!", true, false);

    /* 12. Identical scene + narration erases namebox via repair rect (DIRTY), then SKIP */
    verify_step("12a_narration_namebox_erase", &scene, NULL, "The room grows quiet.", true, false);
    verify_step("12b_identical_scene_skip", &scene, NULL, "Still quiet.", false, true);

    /* 13. BG change -> FULL redraw */
    scene.background = 1;
    verify_step("13_bg_change_full", &scene, "Sayori", "Walking home!", false, false);

    /* 14. Scene obscured by menu -> FULL redraw */
    const char *choices[2] = { "Walk with Sayori", "Go home alone" };
    render_menu(choices, 2, 0);
    verify_step("14_after_menu_full", &scene, "Sayori", "Wait up!", false, false);

    /* 15. Compressed-only CG BG -> FULL redraw fallback */
    scene.background = 2;
    verify_step("15_cg_initial", &scene, "Sayori", "CG frame.", false, false);
    scene.actors[0].overlay = 4;
    verify_step("15b_cg_actor_change_full", &scene, "Sayori", "CG expr.", false, false);

    printf("dirty-rect byte-for-byte verification PASS (all 15 sequences)\n");
    return 0;
}
'''


class DirtyRectHostTest(unittest.TestCase):
    def test_dirty_rect_matches_full_redraw_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            (tmp / "sys").mkdir()
            stubs = {
                "graphx.h": "",
                "fontlibc.h": "",
                "sys/timers.h": "",
                "noise.h": "",
                "ch5_bands.h": "",
                "assets.h": "",
                "fast_ops.h": "",
                "perf_overlay.h": (
                    "#define perf_overlay_frame_start() ((void)0)\n"
                    "#define perf_overlay_mark_rendered() ((void)0)\n"
                    "#define perf_overlay_end() ((void)0)\n"
                    "#define perf_overlay_present_begin() ((void)0)\n"
                    "#define perf_overlay_present_end() ((void)0)\n"
                    "#define perf_overlay_shutdown() ((void)0)\n"
                    "#define perf_overlay_stage_begin(s) ((void)0)\n"
                    "#define perf_overlay_stage_end(s) ((void)0)\n"
                    "#define perf_overlay_vram_begin() ((void)0)\n"
                    "#define perf_overlay_vram_end() ((void)0)\n"
                    "#define perf_overlay_set_mode(m) ((void)0)\n"
                    "#define perf_overlay_clear_sprites() ((void)0)\n"
                ),
                "perf_memory.h": "",
            }
            for name, body in stubs.items():
                (tmp / name).write_text(body)

            src = tmp / "test_dirty_rect.c"
            exe = tmp / "test_dirty_rect"
            src.write_text(HARNESS)

            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Wno-unused-function",
                    "-Wno-unused-parameter",
                    "-O2",
                    "-fsanitize=undefined,address",
                    "-I",
                    str(tmp),
                    "-I",
                    str(ROOT / "src"),
                    str(src),
                    "-o",
                    str(exe),
                ],
                check=True,
            )
            subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    unittest.main()
