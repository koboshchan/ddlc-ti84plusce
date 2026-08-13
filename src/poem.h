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

/**
 * Runs the word-picking minigame to completion (20 rounds, 10 words a
 * round) and reports the outcome: the winning character's id (TAG_TO_CHAR
 * order: 0 = sayori, 1 = natsuki, 2 = yuri) plus each of the three club
 * members' individual point totals, which the internal scoring already
 * computed and used to be discarded once the winner was picked. DDLC's own
 * per-chapter poemwinner[]/s_poemappeal[]/n_poemappeal[]/y_poemappeal[]
 * need all four, not just the winner -- see OP_MINIGAME in vn.h and
 * docs/FORMAT.md's "Poem minigame".
 *
 * Any of the three appeal out-params may be NULL if that call site has no
 * slot to store it in (compile_script.py always resolves all three
 * alongside the winner today, so this is defensive, not exercised).
 * Called by main.c's vn_host_t.minigame.
 */
uint8_t poem_run(int16_t *s_appeal, int16_t *n_appeal, int16_t *y_appeal);

#endif /* POEM_H */
