/**
 * @file persist.h
 * @brief The subset of story variables that survive past this playthrough.
 *
 * Ren'Py's own `persistent` object is the model: unlike an ordinary story
 * variable (which starts over from load_variable_defaults() every New Game)
 * or a save slot (one specific resume point the player chose to keep),
 * `persistent.*` values live outside all of that -- progress markers like
 * how many times the player has started the game, which route(s) they've
 * cleared, whether they've already seen a given one-time surprise. DDLC's
 * own Act 2/3 pacing and its easter eggs both depend on this: the ghost
 * menu and Monika's poem-game eyes are each meant to happen *once ever*,
 * not once per playthrough, which only means anything if "have I already
 * shown this" itself survives a New Game.
 *
 * Which variable slots qualify is decided entirely at compile time --
 * tools/import_game.py identifies every `persistent.*`-named variable
 * (compiler.persistent_slots) and ships the list as DPSLOT. This module
 * doesn't know or care what any of them mean, only that they're the ones
 * to carry forward; DPSLOT is what makes that data-driven rather than a
 * hardcoded list here that could drift out of sync with the compiler.
 */

#ifndef PERSIST_H
#define PERSIST_H

#include "vn.h"

#include <stdbool.h>

/**
 * Overlays every previously-saved persistent value onto @p vm's variables,
 * for the slots DPSLOT names. Call once per New Game/Continue, after
 * assets_apply_var_defaults() (so a real persisted value wins over a plain
 * Ren'Py default) and before a possible save_load() (so a loaded save's own
 * vars[] -- which already reflects whatever was persistent as of that save
 * -- isn't second-guessed).
 *
 * A no-op, not a failure, if this is the first time the game has ever run
 * (no DPERSIST AppVar yet) -- every persistent slot then simply keeps
 * whatever assets_apply_var_defaults() already gave it, correctly matching
 * a fresh install.
 */
void persist_load(vn_vm_t *vm);

/**
 * Writes @p vm's current value for every DPSLOT-named slot back out, so it
 * survives whatever happens next -- a New Game, a restart, the calculator
 * being turned off. Call whenever play returns to the title screen (a
 * natural checkpoint main.c's own loop already passes through) and once
 * more before the program exits, so nothing from the session is lost
 * between checkpoints.
 *
 * Returns false if the write failed (no room in archive) -- not fatal to
 * gameplay, but worth the caller knowing progress markers didn't stick.
 */
bool persist_save(const vn_vm_t *vm);

#endif /* PERSIST_H */
