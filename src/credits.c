/**
 * @file credits.c
 * @brief Interactive animated end-credits sequence (Phase 5 #16).
 */

#include "credits.h"

#include "assets.h"
#include "cgpack.h"
#include "fast_ops.h"
#include "perf_memory.h"
#include "poem.h"
#include "render.h"
#include "text.h"
#include "vn.h"

#include <fontlibc.h>
#include <graphx.h>
#include <keypadc.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

extern bool quit_requested;

#define CREDITS_THUMB_W 160
#define CREDITS_THUMB_H 90

static unsigned credits_ticks_to_ms(clock_t ticks)
{
#ifdef __TICE__
    uint32_t t = (uint32_t)ticks;
    return (unsigned)(((t >> 15) * 1000UL) + (((t & 0x7FFFUL) * 125UL) >> 12));
#else
    return (unsigned)(((uint64_t)ticks * 1000ULL) / CLOCKS_PER_SEC);
#endif
}

static bool credits_poll_skip(void)
{
    static bool held_adv;
    kb_Scan();
    if (kb_IsDown(kb_KeyClear)) {
        quit_requested = true;
        return true;
    }
    bool adv = kb_IsDown(kb_Key2nd) || kb_IsDown(kb_KeyEnter);
    bool pressed = adv && !held_adv;
    held_adv = adv;
    return pressed;
}

static void credits_draw_centered_text(const char *s, int cx, int y, uint8_t color)
{
    if (!s || !s[0] || y < 0 || y >= SCREEN_H - 16) {
        return;
    }
    int w = (int)fontlib_GetStringWidth(s);
    int x = cx - w / 2;
    if (x < 4) x = 4;
    if (x + w >= SCREEN_W) x = SCREEN_W - 4 - w;
    if (x < 0) x = 0;
    render_text(s, x, y, color);
}

static void credits_blit_thumb_clipped(const uint8_t *thumb, int dst_x, int dst_y, int min_y)
{
    int y0 = (dst_y < min_y) ? (min_y - dst_y) : 0;
    int y1 = (dst_y + CREDITS_THUMB_H > SCREEN_H) ? (SCREEN_H - dst_y) : CREDITS_THUMB_H;
    if (y0 >= y1) {
        return;
    }
    int draw_y = dst_y + y0;
    int draw_h = y1 - y0;
    fast_rect_blit((uint8_t *)gfx_vbuffer + (size_t)draw_y * SCREEN_W + dst_x,
                   SCREEN_W, thumb + (size_t)y0 * CREDITS_THUMB_W, CREDITS_THUMB_W,
                   CREDITS_THUMB_W, (size_t)draw_h);
    gfx_SetColor(COL_BOX_EDGE);
    gfx_VertLine_NoClip(dst_x, draw_y, draw_h);
    gfx_VertLine_NoClip(dst_x + CREDITS_THUMB_W - 1, draw_y, draw_h);
    if (y0 == 0) {
        gfx_HorizLine_NoClip(dst_x, draw_y, CREDITS_THUMB_W);
    }
    if (y1 == CREDITS_THUMB_H) {
        gfx_HorizLine_NoClip(dst_x, draw_y + draw_h - 1, CREDITS_THUMB_W);
    }
}

/* assets_scene() uses rows SCENE_H..SCREEN_H-1 as scratch for 2x CG upscaling;
 * clear them before swapping so credits screens never leak raw 160px data. */
static void credits_clear_bottom_bar(void)
{
    gfx_SetColor(COL_BLACK);
    gfx_FillRectangle_NoClip(0, SCENE_H, SCREEN_W, SCREEN_H - SCENE_H);
}

/* Clears both buffers to black before switching hardware palette so no prior
 * frame briefly flashes in the next card's colors. */
static void credits_clear_both(void)
{
    gfx_FillScreen(COL_BLACK);
    gfx_SwapDraw();
    gfx_FillScreen(COL_BLACK);
}

static void credits_hold_scene(uint8_t scene_id, unsigned duration_ms)
{
    if (scene_id == VN_NO_SPRITE || quit_requested) {
        return;
    }
    credits_clear_both();
    render_apply_palette(assets_scene_palette(scene_id));
    assets_scene(scene_id, (uint8_t *)gfx_vbuffer);
    credits_clear_bottom_bar();
    gfx_SwapDraw();

    clock_t start = clock();
    while (!quit_requested) {
        if (credits_poll_skip()) {
            break;
        }
        if (credits_ticks_to_ms(clock() - start) >= duration_ms) {
            break;
        }
        gfx_Wait();
    }
}

static void credits_run_stage0(const uint8_t *scenes)
{
    /* Brief end-glitch ambient beat before Monika's vocal lyrics. */
    if (scenes && scenes[2] != VN_NO_SPRITE) {
        credits_hold_scene(scenes[2], 3200U);
    }
    if (quit_requested) {
        return;
    }

    static const struct {
        unsigned t_ms;
        const char *line1;
        const char *line2;
    } lyrics[6] = {
        {    0U, "Every day, I imagine a future where", "I can be with you" },
        { 2600U, "In my hand is a pen that will write", "a poem of me and you" },
        { 5200U, "The ink flows down into a dark puddle", "" },
        { 7600U, "Just move your hand --", "write the way into his heart!" },
        {10000U, "But in this world of infinite choices", "" },
        {12400U, "What will it take just to find", "that special day?" },
    };

    render_apply_palette(assets_scene_palette(VN_NO_SPRITE));
    clock_t start = clock();
    size_t prev_vis = 0;

    while (!quit_requested) {
        unsigned elapsed = credits_ticks_to_ms(clock() - start);
        if (elapsed >= 15600U) {
            break;
        }
        if (credits_poll_skip()) {
            break;
        }
        size_t vis = 0;
        for (size_t i = 0; i < 6; i++) {
            if (elapsed >= lyrics[i].t_ms) {
                vis = i + 1;
            }
        }
        if (vis != prev_vis) {
            prev_vis = vis;
            gfx_FillScreen(COL_BLACK);
            for (size_t i = 0; i < vis; i++) {
                int base_y = 26 + (int)i * 32;
                credits_draw_centered_text(lyrics[i].line1, SCREEN_W / 2, base_y, COL_WHITE);
                if (lyrics[i].line2[0] != '\0') {
                    credits_draw_centered_text(lyrics[i].line2, SCREEN_W / 2, base_y + 14, COL_WHITE);
                }
            }
            gfx_SwapDraw();
        }
        gfx_Wait();
    }
}

static void credits_sticker_dance(void)
{
    render_apply_palette(assets_scene_palette(VN_NO_SPRITE));
    clock_t start = clock();
    static const uint8_t s_ids[4] = {
        STICKER_S_IDLE, STICKER_N_IDLE, STICKER_Y_IDLE, STICKER_M_IDLE
    };
    static const uint8_t h_ids[4] = {
        STICKER_S_HOP, STICKER_N_HOP, STICKER_Y_HOP, STICKER_M_HOP
    };
    static const int xs[4] = { 64, 128, 192, 256 };

    while (!quit_requested) {
        unsigned elapsed = credits_ticks_to_ms(clock() - start);
        if (elapsed >= 2400U || credits_poll_skip()) {
            break;
        }
        unsigned beat = (elapsed / 240U) & 3U;
        gfx_FillScreen(COL_BLACK);
        credits_draw_centered_text("Special Thanks", SCREEN_W / 2, 54, COL_WHITE);
        credits_draw_centered_text("David Evelyn  &  Corey Shin", SCREEN_W / 2, 74, COL_WHITE);
        for (unsigned i = 0; i < 4; i++) {
            bool hop = (beat == i);
            assets_draw_sticker_centered(hop ? h_ids[i] : s_ids[i], xs[i], hop ? 172 : 186);
        }
        gfx_SwapDraw();
        gfx_Wait();
    }
}

static void credits_run_stage1(bool clearall, uint16_t clear_mask,
                               const uint8_t *scenes, const char *player_name)
{
    static const struct {
        const char *header;
        const char *line1;
        const char *line2;
        const char *del_msg;
        bool cg_right;
    } entries[10] = {
        { "Concept & Design", "Dan Salvato", "", "os.remove(\"images/cg/n_cg1.png\")", true },
        { "Character Art", "Satchely", "", "os.remove(\"images/cg/n_cg2.png\")", false },
        { "Background Art", "Velinquent", "", "os.remove(\"images/cg/y_cg1.png\")", true },
        { "Writing", "Dan Salvato", "", "os.remove(\"images/cg/y_cg2.png\")", false },
        { "Music", "Dan Salvato", "", "os.remove(\"images/cg/n_cg3.png\")", true },
        { "Vocals", "Jillian Ashcraft", "", "os.remove(\"images/cg/y_cg3.png\")", false },
        { "Special Thanks", "Masha Gutin", "Kagefumi", "os.remove(\"images/cg/s_cg1.png\")", true },
        { "Special Thanks", "David Evelyn", "Corey Shin", "os.remove(\"images/cg/s_cg2.png\")", false },
        { "Special Thanks", "Alecia Bardachino", "Matt Naples", "os.remove(\"images/cg/s_cg3.png\")", true },
        { "Special Thanks", "Monika", NULL, "os.remove(\"images/cg/m_cg1.png\")", false },
    };
    static const char *const cg_outs[10] = {
        "n_cg1.png deleted successfully.",
        "n_cg2.png deleted successfully.",
        "y_cg1.png deleted successfully.",
        "y_cg2.png deleted successfully.",
        "n_cg3.png deleted successfully.",
        "y_cg3.png deleted successfully.",
        "s_cg1.png deleted successfully.",
        "s_cg2.png deleted successfully.",
        "s_cg3.png deleted successfully.",
        "m_cg1.png deleted successfully.",
    };

    if (scenes && scenes[0] != VN_NO_SPRITE) {
        credits_hold_scene(scenes[0], 3200U); /* credits_logo */
    }
    if (quit_requested) {
        return;
    }

    render_console_clear();
    uint8_t *thumb = malloc((size_t)CREDITS_THUMB_W * CREDITS_THUMB_H);

    for (unsigned i = 0; i < 10 && !quit_requested; i++) {
        uint8_t cg_id = scenes ? scenes[3U + i] : VN_NO_SPRITE;
        bool unlocked = clearall || ((clear_mask & (1U << i)) != 0);
        bool has_thumb = false;

        if (thumb && cg_id != VN_NO_SPRITE) {
            gfx_FillScreen(COL_BLACK);
            if (assets_scene(cg_id, (uint8_t *)gfx_vbuffer)) {
                for (int y = 0; y < CREDITS_THUMB_H; y++) {
                    const uint8_t *sr = (const uint8_t *)gfx_vbuffer + (size_t)(y * 2) * SCREEN_W;
                    uint8_t *dr = thumb + (size_t)y * CREDITS_THUMB_W;
                    for (int x = 0; x < CREDITS_THUMB_W; x++) {
                        dr[x] = sr[x * 2];
                    }
                }
                has_thumb = true;
            }
        }

        credits_clear_both();
        if (cg_id != VN_NO_SPRITE) {
            vn_scene_t tmp_s;
            memset(&tmp_s, 0, sizeof(tmp_s));
            tmp_s.background = cg_id;
            tmp_s.pal_fx = unlocked ? VN_FX_NONE : VN_FX_SILHOUETTE;
            render_apply_palette(render_filter_palette(&tmp_s, assets_scene_palette(cg_id)));
        } else {
            render_apply_palette(assets_scene_palette(VN_NO_SPRITE));
        }

        const char *l2 = entries[i].line2;
        if (i == 9 && l2 == NULL) {
            l2 = (player_name && player_name[0] != '\0') ? player_name : "you";
        }
        int cg_x = entries[i].cg_right ? 152 : 8;
        int txt_cx = entries[i].cg_right ? 76 : 236;
        int min_y = clearall ? 0 : 56;

        clock_t start = clock();
        const unsigned card_ms = 3400U;
        bool committed_out = false;
        if (!clearall) {
            render_console_set(entries[i].del_msg, NULL, false);
        }
        while (!quit_requested) {
            unsigned elapsed = credits_ticks_to_ms(clock() - start);
            if (elapsed >= card_ms || credits_poll_skip()) {
                break;
            }
            if (!clearall && !committed_out && elapsed >= 650U) {
                render_console_set(entries[i].del_msg, cg_outs[i], false);
                committed_out = true;
            }
            int scroll_y = 200 - (int)((elapsed * 260UL) / card_ms);

            gfx_FillScreen(COL_BLACK);
            if (has_thumb) {
                credits_blit_thumb_clipped(thumb, cg_x, scroll_y - 10, min_y);
            }
            if (scroll_y + 12 >= min_y) {
                credits_draw_centered_text(entries[i].header, txt_cx, scroll_y + 12, COL_WHITE);
            }
            if (scroll_y + 32 >= min_y) {
                credits_draw_centered_text(entries[i].line1, txt_cx, scroll_y + 32, COL_WHITE);
            }
            if (l2 && l2[0] != '\0' && scroll_y + 48 >= min_y) {
                credits_draw_centered_text(l2, txt_cx, scroll_y + 48, COL_WHITE);
            }
            if (!clearall) {
                size_t cmd_len = strlen(entries[i].del_msg);
                size_t vis = committed_out ? 0 : (size_t)((elapsed * cmd_len) / 600U + 1U);
                render_console_draw_step(committed_out ? NULL : entries[i].del_msg, vis);
            }
            gfx_SwapDraw();
            gfx_Wait();
        }
        if (!clearall && !committed_out) {
            render_console_set(entries[i].del_msg, cg_outs[i], false);
        }

        if (i == 7 && !quit_requested) {
            credits_sticker_dance();
        }
    }

    free(thumb);

    /* Core game script deletions right before credits_ts (credits.rpyc). */
    if (!quit_requested) {
        static const struct {
            const char *cmd;
            const char *out;
        } tail_del[4] = {
            { "os.remove(\"game/screens.rpy\")", "screens.rpy deleted successfully." },
            { "os.remove(\"game/gui.rpy\")", "gui.rpy deleted successfully." },
            { "os.remove(\"game/menu.rpy\")", "menu.rpy deleted successfully." },
            { "os.remove(\"game/script.rpy\")", "script.rpy deleted successfully." },
        };
        credits_clear_both();
        render_apply_palette(assets_scene_palette(VN_NO_SPRITE));
        for (unsigned j = 0; j < 4 && !quit_requested; j++) {
            clock_t start = clock();
            bool committed = false;
            render_console_set(tail_del[j].cmd, NULL, false);
            while (!quit_requested) {
                unsigned elapsed = credits_ticks_to_ms(clock() - start);
                if (elapsed >= 900U || credits_poll_skip()) {
                    break;
                }
                if (!committed && elapsed >= 450U) {
                    render_console_set(tail_del[j].cmd, tail_del[j].out, false);
                    committed = true;
                }
                gfx_FillScreen(COL_BLACK);
                size_t clen = strlen(tail_del[j].cmd);
                size_t vis = committed ? 0 : (size_t)((elapsed * clen) / 420U + 1U);
                render_console_draw_step(committed ? NULL : tail_del[j].cmd, vis);
                gfx_SwapDraw();
                gfx_Wait();
            }
            if (!committed) {
                render_console_set(tail_del[j].cmd, tail_del[j].out, false);
            }
        }
    }
    render_console_hide();
    render_console_clear();

    if (!quit_requested && scenes && scenes[1] != VN_NO_SPRITE) {
        credits_clear_both();
        render_apply_palette(assets_scene_palette(scenes[1]));
        assets_scene(scenes[1], (uint8_t *)gfx_vbuffer);
        credits_clear_bottom_bar();
        credits_draw_centered_text("made with love by", SCREEN_W / 2, 12, COL_WHITE);
        gfx_SwapDraw();

        clock_t start = clock();
        while (!quit_requested) {
            if (credits_poll_skip() || credits_ticks_to_ms(clock() - start) >= 3500U) {
                break;
            }
            gfx_Wait();
        }
    }
}

void credits_run(uint8_t stage, bool clearall, uint16_t clear_mask,
                 const uint8_t *scenes, const char *player_name)
{
    gfx_SetDrawBuffer();
    if (stage == 0) {
        credits_run_stage0(scenes);
    } else {
        credits_run_stage1(clearall, clear_mask, scenes, player_name);
    }
    gfx_FillScreen(COL_BLACK);
    gfx_SwapDraw();
    gfx_BlitScreen();
    render_invalidate_scene();
}
