/**
 * @file poem.h
 * @brief The poem-writing minigame, run from OP_MINIGAME's host callback.
 *
 * See docs/FORMAT.md's "Poem minigame" section for what this reproduces
 * from the real game and what it deliberately doesn't.
 */

#ifndef POEM_H
#define POEM_H

#include <stdint.h>

enum {
    STICKER_S_IDLE   = 0,
    STICKER_S_HOP    = 1,
    STICKER_N_IDLE   = 2,
    STICKER_N_HOP    = 3,
    STICKER_Y_IDLE   = 4,
    STICKER_Y_HOP    = 5,
    STICKER_Y_CUT    = 6,
    STICKER_Y_GLITCH = 7,
    STICKER_M_IDLE   = 8,
    STICKER_M_HOP    = 9,
    STICKER_Y_BROKEN = 10,
    STICKER_COUNT    = 11
};

/**
 * Runs the word-picking minigame to completion (20 rounds, 10 words a
 * round) and reports the outcome: the winning character's id (TAG_TO_CHAR
 * order: 0 = sayori, 1 = natsuki, 2 = yuri) plus each club member's
 * normalized poemappeal tier (-1 dislike, 0 neutral, +1 like; winner forced
 * to +1) matching script-poemgame.rpyc's POEM_DISLIKE/LIKE_THRESHOLD.
 *
 * @p playthrough (from persistent.playthrough) and @p chapter (0, 1, 2)
 * gate Act 1/2 behavior, and @p ch1_choice_char (0/1/2 or -1 if unset)
 * applies DDLC's Day 1 +5 bonus on chapter == 1.
 *
 * Any of the three appeal out-params may be NULL if that call site has no
 * slot to store it in. Called by main.c's vn_host_t.minigame.
 */
uint8_t poem_run(int16_t *s_appeal, int16_t *n_appeal, int16_t *y_appeal,
                 int16_t playthrough, int16_t chapter, int8_t ch1_choice_char);

/**
 * Renders the in-game poem @p poem_id on full-screen notebook paper,
 * handling multi-page scrolling and waiting for dismissal.
 */
void poem_view(uint8_t poem_id);

#endif /* POEM_H */
