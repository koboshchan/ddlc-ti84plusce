/**
 * @file chars.h
 * @brief On-calc stand-in for each character's Ren'Py ".chr" persistence file.
 *
 * In the original game, Monika's Act 2/3 corruption "deletes" a character by
 * removing their .chr file from disk. The calc equivalent: one empty AppVar
 * per character (SAYORI/NATSUKI/YURI/MONIKA). Deleting the AppVar with
 * ti_Delete *is* the effect -- no game logic needs to track deletion state
 * separately, since chars_present() just asks the filesystem.
 *
 * The AppVars are empty by design: this module only tracks presence/absence,
 * not content. Wiring an OP_* to trigger chars_delete() from the bytecode is
 * deferred to the meta/glitch content milestone.
 */

#ifndef CHARS_H
#define CHARS_H

#include <stdbool.h>
#include <stdint.h>

enum {
    CHAR_SAYORI = 0,
    CHAR_NATSUKI = 1,
    CHAR_YURI = 2,
    CHAR_MONIKA = 3,
    CHAR_COUNT = 4,
};

/**
 * Ensure all 4 character AppVars exist, creating any that are missing.
 * Safe to call every boot: existing AppVars (including ones already deleted
 * by chars_delete() in a prior session) are left untouched, so deletion
 * persists across restarts instead of being silently undone.
 */
void chars_init(void);

/** True if @p character's AppVar still exists. */
bool chars_present(uint8_t character);

/** Delete @p character's AppVar. Returns true on success. */
bool chars_delete(uint8_t character);

#endif /* CHARS_H */
