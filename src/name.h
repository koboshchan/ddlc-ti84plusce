/**
 * @file name.h
 * @brief The player's name, DDLC's `[player]` dialogue substitution.
 *
 * DDLC asks for the player's name once, at the very start of Act 1, and
 * every later line that would say "[player]" substitutes it in instead
 * (`renpy.input()`, in the real game). This engine asks the same question at
 * the same point -- program startup, before a name has ever been saved --
 * rather than as a real-time Ren'Py `$ name = renpy.input(...)` statement,
 * since compile_script.py has no way to lower that call (it would need to
 * suspend bytecode execution for player text input, which OP_SAY/OP_MENU's
 * fixed shapes have no room for). Storing it once, up front, and having the
 * host substitute it into every line as it's displayed, gets the same
 * result without needing a new opcode.
 */

#ifndef NAME_H
#define NAME_H

#include <stdbool.h>
#include <stddef.h>

#define NAME_MAX_LEN 8  /* chars, not counting the NUL */

/** True if a name has already been saved (DNAME AppVar exists). */
bool name_exists(void);

/** Reads the saved name into @p out (NUL-terminated, up to @p cap bytes
 * including the NUL). Returns false (leaving @p out untouched) if no name
 * has been saved yet. */
bool name_load(char *out, size_t cap);

/** Saves @p name, replacing whatever was saved before. Returns false if the
 * AppVar couldn't be written. */
bool name_save(const char *name);

#endif /* NAME_H */
