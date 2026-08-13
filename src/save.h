/**
 * @file save.h
 * @brief Suspend/resume save states, stored one per DSAVEn AppVar.
 *
 * A save is just enough of vn_vm_t to make vn_step() pick up exactly where
 * it left off: the program counter, call stack, story variables, and the
 * currently-displayed scene. It is NOT a snapshot of the whole struct --
 * vn_scene_t.text is a pointer into this run's malloc'd DSCRIPT copy
 * (assets.c), which won't exist at the same address next launch, so the
 * string is re-resolved from its index (vn_scene_t.text_index) on load
 * instead of being copied as a pointer.
 *
 * This is a position in the *compiled bytecode*, not an abstract story
 * checkpoint -- a save only replays correctly against the exact script.vnb
 * it was written against. Re-running the import pipeline with a different
 * --files selection changes that layout and invalidates old saves.
 */

#ifndef SAVE_H
#define SAVE_H

#include "vn.h"

#include <stdbool.h>
#include <stdint.h>

#define SAVE_SLOTS 3  /* slots are numbered 1..SAVE_SLOTS */

/** True if @p slot has a save written. */
bool save_exists(uint8_t slot);

/** Writes @p vm's current resumable state into @p slot, replacing whatever
 * was there. Returns false if the AppVar couldn't be created. */
bool save_write(uint8_t slot, const vn_vm_t *vm);

/** Overwrites @p vm's pc/stack/vars/scene with @p slot's saved state and
 * sets it back to VN_RUNNING. Returns false (leaving @p vm untouched) if
 * @p slot has no save. */
bool save_load(uint8_t slot, vn_vm_t *vm);

/**
 * Erases every save slot, permanently -- compiles DDLC's own
 * `delete_all_saves()` call (see vn.h's OP_DELETE_SAVES). No undo: a slot
 * with nothing in it to begin with is not an error, only a slot whose
 * AppVar exists but can't be removed is.
 */
bool save_delete_all(void);

#endif /* SAVE_H */
