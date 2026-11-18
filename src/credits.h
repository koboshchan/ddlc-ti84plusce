/**
 * @file credits.h
 * @brief End-credits presentation (Your Reality lyrics + scrolling CG cards + chibis).
 */

#ifndef CREDITS_H
#define CREDITS_H

#include <stdbool.h>
#include <stdint.h>

/**
 * Runs stage 0 (end-glitch + Monika's "Your Reality" lyrics) or stage 1
 * (credits_logo, 10 scrolling CG cards with persistent.clear[0..9]/clearall
 * silhouette gating, bouncing chibi stickers, and Team Salvato ending card).
 */
void credits_run(uint8_t stage, bool clearall, uint16_t clear_mask,
                 const uint8_t *scenes, const char *player_name);

#endif /* CREDITS_H */
