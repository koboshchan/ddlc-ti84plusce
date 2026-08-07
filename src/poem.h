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
 * round) and returns the winning character's id, TAG_TO_CHAR order:
 * 0 = sayori, 1 = natsuki, 2 = yuri. Called by main.c's vn_host_t.minigame,
 * which OP_MINIGAME stores into a story variable.
 */
uint8_t poem_run(void);

#endif /* POEM_H */
