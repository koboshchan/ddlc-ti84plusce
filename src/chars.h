/**
 * @file chars.h
 * @brief On-calc stand-in for each character's Ren'Py ".chr" persistence file.
 *
 * In the original game, Monika's Act 2/3 corruption "deletes" a character by
 * removing their .chr file from disk. The calc equivalent: one empty AppVar
 * per character (SAYORI/NATSUKI/YURI/MONIKA), baked into the bundle already
 * present (tools/import_game.py's do_package()) rather than created here.
 * Deleting the AppVar with ti_Delete *is* the effect -- no game logic needs
 * to track deletion state separately, since chars_present() just asks the
 * filesystem.
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
 * First boot after install, creates the 4 character AppVars and marks
 * it done by creating FIRSTRUN in Archive. Every later boot, the marker
 * AppVar (FIRSTRUN, never baked into the bundle -- only ever created here)
 * already exists, so this returns immediately without touching the character
 * AppVars at all, whatever state they're actually in. That distinction is the whole point:
 * a naive "create any of the 4 that are missing" loop run on every boot
 * can't tell "never existed" apart from "deliberately deleted in a
 * previous session", so it would silently resurrect a deleted character
 * the very next time the program launched.
 */
void chars_init(void);

/** True if @p character's AppVar still exists. */
bool chars_present(uint8_t character);

/** Delete @p character's AppVar. Returns true on success. */
bool chars_delete(uint8_t character);

#endif /* CHARS_H */
