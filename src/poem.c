/**
 * @file poem.c
 * @brief The poem-writing minigame. See poem.h.
 */

#include "poem.h"

#include "assets.h"
#include "render.h"
#include "text.h"

#include <fileioc.h>
#include <graphx.h>
#include <keypadc.h>
#include <ti/getcsc.h>
#include <sys/timers.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* main.c's global -- set on Clear here the same way every other screen in
 * this codebase does, so quitting mid-minigame still unwinds the whole app
 * (vn_step()'s host->quit() picks it up on the next step). */
extern bool quit_requested;

/* Real DDLC: numWords = 20, 10 words shown per round (2 columns x 5 rows),
 * all 10 removed from the pool each round regardless of pick. 20*10 = 200 of
 * the real word bank's 228 words -- see docs/FORMAT.md's "Poem minigame". */
#define POEM_ROUNDS     20
#define POEM_PER_ROUND  10
#define POEM_COLS        2
#define POEM_ROWS        5  /* POEM_COLS * POEM_ROWS == POEM_PER_ROUND */

#define POEM_WORD_MAX   14  /* longest real word, "uncontrollable" */
#define POEM_WORDS_CAP 256  /* real bank is 228 -- headroom to spare */

typedef struct {
    char    word[POEM_WORD_MAX + 1];
    uint8_t sPoint, nPoint, yPoint;
    bool    glitch;
} poem_word_t;

static poem_word_t poem_words[POEM_WORDS_CAP];
static uint16_t     poem_word_count;

/* Which words are still available to draw this game -- indices into
 * poem_words[], shuffled: pool[0 .. pool_remaining) is the available set.
 * pool_take() swaps a random available slot to the end and shrinks the
 * range, an O(1) draw-without-replacement instead of rejection sampling
 * (which would slow down a lot near the end: 20 rounds * 10 draws consumes
 * 200 of 228 words, an 88% pool by the last round). */
static uint16_t pool[POEM_WORDS_CAP];
static uint16_t pool_remaining;

/** Reads DPOEM (see docs/FORMAT.md's "Poem minigame") into poem_words[].
 * Reads and copies out fully before this handle closes or any other AppVar
 * opens -- see assets.c's file comment for why a ti_GetDataPtr() pointer
 * can't be held past that. */
static bool load_words(void)
{
    uint8_t handle = ti_Open("DPOEM", "r");
    if (!handle) {
        return false;
    }

    const uint8_t *data = ti_GetDataPtr(handle);
    uint16_t total = (uint16_t)(data[0] | ((uint16_t)data[1] << 8));
    size_t   pos   = 2;
    uint16_t kept  = 0;

    for (uint16_t i = 0; i < total; i++) {
        uint8_t len = data[pos++];
        if (kept < POEM_WORDS_CAP) {
            uint8_t copy_len = len < POEM_WORD_MAX ? len : POEM_WORD_MAX;
            memcpy(poem_words[kept].word, data + pos, copy_len);
            poem_words[kept].word[copy_len] = '\0';
        }
        pos += len;

        uint8_t s = data[pos], n = data[pos + 1], y = data[pos + 2];
        pos += 3;

        if (kept < POEM_WORDS_CAP) {
            poem_words[kept].sPoint = s;
            poem_words[kept].nPoint = n;
            poem_words[kept].yPoint = y;
            poem_words[kept].glitch = false;
            kept++;
        }
    }

    ti_Close(handle);
    poem_word_count = kept;
    return poem_word_count > 0;
}

static void pool_init(void)
{
    for (uint16_t i = 0; i < poem_word_count; i++) {
        pool[i] = i;
    }
    pool_remaining = poem_word_count;
}

static uint16_t pool_take(void)
{
    if (pool_remaining == 0) {
        return 0;
    }
    uint16_t slot = (uint16_t)(rand() % pool_remaining);
    uint16_t idx  = pool[slot];
    pool_remaining--;
    pool[slot] = pool[pool_remaining];
    return idx;
}

/* ---------------------------------------------------------------------------
 * Input -- a small self-contained poll, the same edge-detected shape as
 * main.c's input_poll(), kept local rather than shared: this is the only
 * screen in the codebase big enough to live outside main.c, and duplicating
 * a dozen lines here is simpler than threading a shared input module through
 * for one caller.
 * ------------------------------------------------------------------------ */

typedef struct {
    bool up, down, left, right, advance, quit;
} poem_input_t;

static void poem_poll(poem_input_t *in)
{
    static bool held_up, held_down, held_left, held_right, held_advance;

    kb_Scan();

    bool up      = kb_IsDown(kb_KeyUp);
    bool down    = kb_IsDown(kb_KeyDown);
    bool left    = kb_IsDown(kb_KeyLeft);
    bool right   = kb_IsDown(kb_KeyRight);
    bool advance = kb_IsDown(kb_Key2nd) || kb_IsDown(kb_KeyEnter);

    in->up      = up      && !held_up;
    in->down    = down    && !held_down;
    in->left    = left    && !held_left;
    in->right   = right   && !held_right;
    in->advance = advance && !held_advance;
    in->quit    = kb_IsDown(kb_KeyClear);

    held_up = up;
    held_down = down;
    held_left = left;
    held_right = right;
    held_advance = advance;

    if (in->quit) {
        quit_requested = true;
    }
}

/* ---------------------------------------------------------------------------
 * Rendering
 * ------------------------------------------------------------------------ */

#define POEM_COL_X0   36
#define POEM_COL_X1  172
#define POEM_ROW_Y0   50
#define POEM_ROW_H    24

static int poem_col_x(uint8_t col)
{
    return col == 0 ? POEM_COL_X0 : POEM_COL_X1;
}

#define POEM_STICKER_Y_IDLE  234
#define POEM_STICKER_Y_HOP   218

#define POEM_S_X_ACT1         65
#define POEM_N_X_ACT1        160
#define POEM_Y_X_ACT1        255

#define POEM_N_X_ACT2        105
#define POEM_Y_X_ACT2        215
#define POEM_M_X_ACT2        160

static bool persistent_seen_sticker = false;

static void draw_stickers(int16_t playthrough, int16_t chapter,
                          bool s_hop, bool n_hop, bool y_hop,
                          bool m_hop, bool y_glitch, bool y_cut,
                          bool poem_glitched)
{
    (void)chapter;
    if (poem_glitched) {
        assets_draw_sticker_centered(STICKER_Y_BROKEN, SCREEN_W / 2, POEM_STICKER_Y_IDLE);
        return;
    }

    if (playthrough == 0) {
        uint8_t s_id = s_hop ? STICKER_S_HOP : STICKER_S_IDLE;
        int s_y = s_hop ? POEM_STICKER_Y_HOP : POEM_STICKER_Y_IDLE;
        assets_draw_sticker_centered(s_id, POEM_S_X_ACT1, s_y);

        uint8_t n_id = n_hop ? STICKER_N_HOP : STICKER_N_IDLE;
        int n_y = n_hop ? POEM_STICKER_Y_HOP : POEM_STICKER_Y_IDLE;
        assets_draw_sticker_centered(n_id, POEM_N_X_ACT1, n_y);

        uint8_t y_id = y_hop ? STICKER_Y_HOP : STICKER_Y_IDLE;
        int y_y = y_hop ? POEM_STICKER_Y_HOP : POEM_STICKER_Y_IDLE;
        assets_draw_sticker_centered(y_id, POEM_Y_X_ACT1, y_y);
    } else {
        uint8_t n_id = n_hop ? STICKER_N_HOP : STICKER_N_IDLE;
        int n_y = n_hop ? POEM_STICKER_Y_HOP : POEM_STICKER_Y_IDLE;
        assets_draw_sticker_centered(n_id, POEM_N_X_ACT2, n_y);

        uint8_t y_id;
        if (y_glitch) {
            y_id = STICKER_Y_GLITCH;
        } else if (y_cut) {
            y_id = STICKER_Y_CUT;
        } else if (y_hop) {
            y_id = STICKER_Y_HOP;
        } else {
            y_id = STICKER_Y_IDLE;
        }
        int y_y = (y_hop || y_glitch || y_cut) ? POEM_STICKER_Y_HOP : POEM_STICKER_Y_IDLE;
        assets_draw_sticker_centered(y_id, POEM_Y_X_ACT2, y_y);

        if (m_hop) {
            assets_draw_sticker_centered(STICKER_M_HOP, POEM_M_X_ACT2, POEM_STICKER_Y_HOP - 8);
        }
    }
}

static void draw_background(bool poem_glitched)
{
    if (poem_glitched) {
        render_backdrop(COL_WHITE);
    } else if (!assets_poem_bg((uint8_t *)gfx_vbuffer)) {
        render_backdrop(COL_WHITE);
    }
}

static void draw_round(const uint16_t *shown, uint8_t round, uint8_t sel_col, uint8_t sel_row,
                       int16_t playthrough, int16_t chapter,
                       bool s_hop, bool n_hop, bool y_hop,
                       bool m_hop, bool y_glitch, bool y_cut,
                       bool poem_glitched)
{
    draw_background(poem_glitched);

    char progress[24];
    if (playthrough >= 2 && chapter == 2) {
        uint8_t ones = round + 1;
        if (ones > 20) ones = 20;
        for (uint8_t k = 0; k < ones; k++) {
            progress[k] = '1';
        }
        sprintf(progress + ones, "/%u", POEM_ROUNDS);
    } else {
        sprintf(progress, "%u/%u", round + 1, POEM_ROUNDS);
    }
    render_text(progress, SCREEN_W - 55, 10, COL_BLACK);

    for (uint8_t col = 0; col < POEM_COLS; col++) {
        for (uint8_t row = 0; row < POEM_ROWS; row++) {
            uint8_t  i        = (uint8_t)(col * POEM_ROWS + row);
            int      x        = poem_col_x(col);
            int      y        = POEM_ROW_Y0 + row * POEM_ROW_H;
            bool     selected = col == sel_col && row == sel_row;

            if (selected) {
                gfx_SetColor(COL_HIGHLIGHT);
                gfx_FillRectangle_NoClip(x - 4, y - 3, 120, POEM_ROW_H - 4);
            }
            render_text(poem_words[shown[i]].word, x, y, COL_BLACK);
        }
    }

    draw_stickers(playthrough, chapter, s_hop, n_hop, y_hop,
                  m_hop, y_glitch, y_cut, poem_glitched);

    render_present(TRANS_CUT);
    gfx_Wait();
}

/* ---------------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------------ */

uint8_t poem_run(int16_t *s_appeal, int16_t *n_appeal, int16_t *y_appeal, int16_t playthrough, int16_t chapter)
{
    if (s_appeal) *s_appeal = 0;
    if (n_appeal) *n_appeal = 0;
    if (y_appeal) *y_appeal = 0;

    if (!load_words()) {
        return 0;
    }

    srand((unsigned)clock());
    pool_init();

    uint16_t totals[3] = { 0, 0, 0 };
    bool poemgame_glitched = false;
    bool played_baa = false;

    for (uint8_t round = 0; round < POEM_ROUNDS; round++) {
        uint16_t shown[POEM_PER_ROUND];
        for (uint8_t i = 0; i < POEM_PER_ROUND; i++) {
            shown[i] = pool_take();
        }

        /* 1/401 chance per word slot on Act 2 (chapter >= 1) to become a glitched word */
        if (playthrough >= 2 && !poemgame_glitched && chapter >= 1 && round < POEM_ROUNDS - 1) {
            for (uint8_t i = 0; i < POEM_PER_ROUND; i++) {
                if ((rand() % 401) == 0) {
                    poem_word_t *gw = &poem_words[shown[i]];
                    gw->glitch = true;
                    static const char glitch_chars[] = "!@#$%^&*<>~?/{}[];^=+";
                    for (int k = 0; k < 12; k++) {
                        gw->word[k] = glitch_chars[rand() % (sizeof(glitch_chars) - 1)];
                    }
                    gw->word[12] = '\0';
                    gw->sPoint = 0;
                    gw->nPoint = 0;
                    gw->yPoint = 0;
                    break;
                }
            }
        }

        uint8_t sel_col = 0, sel_row = 0;
        for (;;) {
            draw_round(shown, round, sel_col, sel_row,
                       playthrough, chapter,
                       false, false, false, false, false, false,
                       poemgame_glitched);

            poem_input_t in;
            poem_poll(&in);
            if (in.quit) {
                return 0;
            }
            if (in.up) {
                sel_row = sel_row == 0 ? POEM_ROWS - 1 : (uint8_t)(sel_row - 1);
            }
            if (in.down) {
                sel_row = (uint8_t)((sel_row + 1) % POEM_ROWS);
            }
            if (in.left) {
                sel_col = 0;
            }
            if (in.right) {
                sel_col = POEM_COLS - 1;
            }
            if (in.advance) {
                break;
            }
        }

        const poem_word_t *picked = &poem_words[shown[sel_col * POEM_ROWS + sel_row]];
        if (picked->glitch) {
            poemgame_glitched = true;
            /* White flash + broken Yuri head jump */
            draw_background(true);
            assets_draw_sticker_centered(STICKER_Y_BROKEN, SCREEN_W / 2, SCREEN_H / 2 + 22);
            render_present(TRANS_CUT);
            gfx_Wait();
            msleep(400);
        } else {
            bool s_hop = false, n_hop = false, y_hop = false;
            bool m_hop = false, y_glitch = false, y_cut = false;

            if (playthrough == 0) {
                s_hop = (picked->sPoint >= 3);
                n_hop = (picked->nPoint >= 3);
                y_hop = (picked->yPoint >= 3);
            } else {
                if (chapter == 2 && (rand() % 11) == 0) {
                    m_hop = true;
                } else if (picked->nPoint > picked->yPoint) {
                    n_hop = true;
                } else if (!persistent_seen_sticker && (rand() % 101) == 0) {
                    y_glitch = true;
                    persistent_seen_sticker = true;
                } else if (chapter == 2) {
                    y_cut = true;
                } else {
                    y_hop = true;
                }
            }

            draw_round(shown, round, sel_col, sel_row,
                       playthrough, chapter,
                       s_hop, n_hop, y_hop,
                       m_hop, y_glitch, y_cut,
                       poemgame_glitched);
            msleep(150);
        }

        if (poemgame_glitched && !played_baa && (rand() % 11) == 0) {
            played_baa = true;
        }

        totals[0] += picked->sPoint;
        totals[1] += picked->nPoint;
        totals[2] += picked->yPoint;
    }

    uint8_t winner = 0;
    if (playthrough > 0) {
        winner = (totals[1] > totals[2]) ? 1 : 2;
    } else {
        if (totals[1] > totals[winner]) {
            winner = 1;
        }
        if (totals[2] > totals[winner]) {
            winner = 2;
        }
    }

    /* totals[] is uint16_t (a sum of always-non-negative per-word points,
     * see poem_word_t), well inside int16_t's positive range for 20 rounds
     * of real word-bank values -- a plain cast, no clamping needed. */
    if (s_appeal) *s_appeal = (int16_t)totals[0];
    if (n_appeal) *n_appeal = (int16_t)totals[1];
    if (y_appeal) *y_appeal = (int16_t)totals[2];
    return winner;
}

#include <fontlibc.h>

#define POEM_VIEW_MAX_LINES 128
#define POEM_PAGE_LINES     13
#define POEM_TEXT_X         32
#define POEM_TEXT_MAX_W     256
#define POEM_TEXT_Y0        38
#define POEM_LINE_H         13

typedef struct {
    char author[8];
    char title[32];
    uint16_t text_len;
    char text[];
} dpoem_entry_t;

typedef struct {
    const char *start;
    uint16_t    len;
} poem_line_slice_t;

void poem_view(uint8_t poem_id)
{
    uint8_t handle = ti_Open("DPOEMT", "r");
    if (!handle) {
        return;
    }
    const uint8_t *data = ti_GetDataPtr(handle);
    uint16_t count = *(const uint16_t *)data;
    if (poem_id >= count) {
        ti_Close(handle);
        return;
    }
    const uint16_t *offsets = (const uint16_t *)(data + 2);
    const dpoem_entry_t *poem = (const dpoem_entry_t *)(data + offsets[poem_id]);

    /* Split and word-wrap poem text into lines */
    poem_line_slice_t lines[POEM_VIEW_MAX_LINES];
    uint16_t total_lines = 0;

    const char *p = poem->text;
    while (*p && total_lines < POEM_VIEW_MAX_LINES) {
        const char *nl = strchr(p, '\n');
        size_t par_len = nl ? (size_t)(nl - p) : strlen(p);

        if (par_len == 0) {
            lines[total_lines].start = p;
            lines[total_lines].len = 0;
            total_lines++;
        } else {
            size_t pos = 0;
            while (pos < par_len && total_lines < POEM_VIEW_MAX_LINES) {
                while (pos < par_len && p[pos] == ' ') {
                    pos++;
                }
                if (pos >= par_len) {
                    break;
                }

                size_t line_start = pos;
                size_t last_break = pos;
                size_t line_len = 0;
                unsigned cur_w = 0;

                while (pos < par_len) {
                    if (p[pos] == ' ') {
                        last_break = pos;
                    }
                    uint8_t c = (uint8_t)p[pos];
                    unsigned gw = (c >= 32 && c <= 126) ? fontlib_GetGlyphWidth(c) : 6;
                    if (cur_w + gw > POEM_TEXT_MAX_W && pos > line_start) {
                        if (last_break > line_start) {
                            line_len = last_break - line_start;
                            pos = last_break + 1;
                        } else {
                            line_len = pos - line_start;
                        }
                        break;
                    }
                    cur_w += gw;
                    pos++;
                }
                if (pos >= par_len && line_len == 0) {
                    line_len = par_len - line_start;
                }
                lines[total_lines].start = p + line_start;
                lines[total_lines].len = (uint16_t)line_len;
                total_lines++;
            }
        }
        p = nl ? nl + 1 : p + par_len;
    }

    uint16_t scroll = 0;

    while (!quit_requested) {
        draw_background(false);

        /* Draw Title */
        if (poem->title[0]) {
            render_text(poem->title, POEM_TEXT_X, 18, COL_NAME);
        }

        /* Draw visible lines */
        for (uint8_t i = 0; i < POEM_PAGE_LINES; i++) {
            uint16_t l_idx = scroll + i;
            if (l_idx >= total_lines) break;
            if (lines[l_idx].len > 0) {
                char buf[96];
                size_t l = lines[l_idx].len < sizeof(buf) - 1 ? lines[l_idx].len : sizeof(buf) - 1;
                memcpy(buf, lines[l_idx].start, l);
                buf[l] = '\0';
                render_text(buf, POEM_TEXT_X, POEM_TEXT_Y0 + i * POEM_LINE_H, COL_BLACK);
            }
        }

        /* Draw bottom prompt */
        bool has_more = (scroll + POEM_PAGE_LINES < total_lines);
        if (has_more) {
            render_text("v 2nd / Down for more", SCREEN_W - 145, SCREEN_H - 18, COL_NAME);
        } else {
            render_text("2nd / Enter to dismiss", SCREEN_W - 145, SCREEN_H - 18, COL_NAME);
        }

        render_present(TRANS_CUT);
        gfx_Wait();

        poem_input_t in;
        poem_poll(&in);
        if (in.quit) {
            break;
        }
        if (in.down) {
            if (has_more) {
                scroll += POEM_PAGE_LINES - 2;
                if (scroll + POEM_PAGE_LINES > total_lines) {
                    scroll = total_lines > POEM_PAGE_LINES ? total_lines - POEM_PAGE_LINES : 0;
                }
            }
        }
        if (in.up) {
            if (scroll > POEM_PAGE_LINES - 2) {
                scroll -= POEM_PAGE_LINES - 2;
            } else {
                scroll = 0;
            }
        }
        if (in.advance) {
            if (has_more) {
                scroll += POEM_PAGE_LINES - 2;
                if (scroll + POEM_PAGE_LINES > total_lines) {
                    scroll = total_lines > POEM_PAGE_LINES ? total_lines - POEM_PAGE_LINES : 0;
                }
            } else {
                break;
            }
        }
    }
    ti_Close(handle);
}
