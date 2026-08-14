/**
 * @file poem.c
 * @brief The poem-writing minigame. See poem.h.
 */

#include "poem.h"

#include "assets.h"
#include "render.h"

#include <fileioc.h>
#include <graphx.h>
#include <keypadc.h>
#include <ti/getcsc.h>

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

static void draw_background(void)
{
    /* Full screen, unlike an ordinary dialogue scene -- the poem minigame
     * has no dialogue box reserving the bottom 60px, so its background is
     * baked and stored at the full 320x240 at full resolution, rather than
     * through assets_scene()'s half-resolution upscale path -- see
     * image_resolve.py's poem_background()/BG_SIZE. */
    if (!assets_poem_bg((uint8_t *)gfx_vbuffer)) {
        render_backdrop(COL_WHITE);
    }
}

static void draw_round(const uint16_t *shown, uint8_t round, uint8_t sel_col, uint8_t sel_row)
{
    draw_background();

    char progress[16];
    sprintf(progress, "%u/%u", round + 1, POEM_ROUNDS);
    render_text(progress, SCREEN_W - 50, 10, COL_BLACK);

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

    render_present(TRANS_CUT);
    gfx_Wait();
}

/* ---------------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------------ */

uint8_t poem_run(int16_t *s_appeal, int16_t *n_appeal, int16_t *y_appeal)
{
    /* Every real exit path below overwrites these before returning except
     * the two "bail immediately" ones (missing word bank, player quit
     * mid-game) -- zeroed up front so those don't leave the caller reading
     * whatever was on the stack. */
    if (s_appeal) *s_appeal = 0;
    if (n_appeal) *n_appeal = 0;
    if (y_appeal) *y_appeal = 0;

    if (!load_words()) {
        /* DPOEM missing -- shouldn't happen in a bundle this engine itself
         * built, but degrade to a fixed winner rather than crash, same
         * spirit as the rest of this codebase's optional-AppVar handling. */
        return 0;
    }

    srand((unsigned)clock());
    pool_init();

    /* Index order matches OP_MINIGAME's TAG_TO_CHAR result: 0 sayori,
     * 1 natsuki, 2 yuri. */
    uint16_t totals[3] = { 0, 0, 0 };

    for (uint8_t round = 0; round < POEM_ROUNDS; round++) {
        uint16_t shown[POEM_PER_ROUND];
        for (uint8_t i = 0; i < POEM_PER_ROUND; i++) {
            shown[i] = pool_take();
        }

        uint8_t sel_col = 0, sel_row = 0;
        for (;;) {
            draw_round(shown, round, sel_col, sel_row);

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
        totals[0] += picked->sPoint;
        totals[1] += picked->nPoint;
        totals[2] += picked->yPoint;
    }

    /* Real DDLC's winner is whoever's total is highest, but only on a first
     * playthrough (`persistent.playthrough == 0`) -- this engine has no
     * persistent multi-playthrough state, so it always takes that branch;
     * see docs/FORMAT.md's "Poem minigame". */
    uint8_t winner = 0;
    if (totals[1] > totals[winner]) {
        winner = 1;
    }
    if (totals[2] > totals[winner]) {
        winner = 2;
    }

    /* totals[] is uint16_t (a sum of always-non-negative per-word points,
     * see poem_word_t), well inside int16_t's positive range for 20 rounds
     * of real word-bank values -- a plain cast, no clamping needed. */
    if (s_appeal) *s_appeal = (int16_t)totals[0];
    if (n_appeal) *n_appeal = (int16_t)totals[1];
    if (y_appeal) *y_appeal = (int16_t)totals[2];
    return winner;
}
