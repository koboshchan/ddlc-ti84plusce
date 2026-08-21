/**
 * @file persist.c
 * @brief See persist.h.
 */

#include "persist.h"

#include <fileioc.h>

#include <stdio.h>
#include <stdlib.h>

/* DPSLOT: u16 count, then one u8 slot per entry -- which vars[] indices are
 * `persistent.*` (see tools/import_game.py's packaging). Compiler-generated
 * and read-only from this module's side; loaded once and kept for the
 * program's whole run, the same pattern assets.c's DVSTR/DVLBL use, and for
 * the same reason -- small (22 entries today) and needed on both the load
 * and every save checkpoint, not worth re-reading each time. */
static uint8_t *slot_buf;
static uint16_t slot_count;

static uint16_t read_u16le(const uint8_t *p)
{
    return (uint16_t)(p[0] | (uint16_t)(p[1] << 8));
}

/* Mirrors assets.c's read_whole() exactly (open, malloc, read whole,
 * close) -- not shared with it directly since assets.c's copy is file-
 * local (static) and this module has no other reason to link against it. */
static uint8_t *read_whole(const char *name, uint16_t *size_out)
{
    uint8_t handle = ti_Open(name, "r");
    if (!handle) {
        return NULL;
    }
    uint16_t size = ti_GetSize(handle);
    uint8_t *buf = malloc(size);
    if (!buf) {
        ti_Close(handle);
        return NULL;
    }
    ti_Read(buf, 1, size, handle);
    ti_Close(handle);
    if (size_out) {
        *size_out = size;
    }
    return buf;
}

/* Loads DPSLOT into slot_buf/slot_count if it hasn't been already. Both
 * persist_load() and persist_save() need it, and neither is a per-frame
 * call (a New Game/Continue, or returning to the title screen), so lazy
 * loading avoids paying for it if a session never touches persistence at
 * all -- unlikely in practice, but free to allow for. */
static void ensure_slots(void)
{
    if (slot_buf) {
        return;
    }
    uint16_t size;
    slot_buf = read_whole("DPSLOT", &size);
    /* A bundle whose script has no persistent.* variable at all ships no
     * DPSLOT -- slot_count stays 0, and both functions below correctly
     * become no-ops rather than failing. */
    slot_count = slot_buf ? read_u16le(slot_buf) : 0;
}

void persist_load(vn_vm_t *vm)
{
    ensure_slots();
    if (slot_count == 0) {
        return;
    }

    uint16_t values_size;
    uint8_t *values = read_whole("DPERSIST", &values_size);
    if (!values) {
        return;   /* first run ever -- nothing saved yet, defaults stand */
    }

    /* One i16 per DPSLOT entry, DPSLOT order -- see persist_save(). A
     * transfer that's shorter than expected (an interrupted sync, or an
     * older DPERSIST written before a script rebuild added more persistent
     * variables) applies only the prefix it actually has rather than
     * reading past the buffer; the slots past that just keep their
     * assets_apply_var_defaults() value, which is the same as if this
     * were their first time ever being persisted. */
    uint16_t usable = (uint16_t)(values_size / 2);
    uint16_t count  = (slot_count < usable) ? slot_count : usable;

    /* No `slot < VN_MAX_VARS` guard needed: slot is a uint8_t and
     * VN_MAX_VARS is 256, the full range that type can hold -- see vn.c's
     * _Static_assert, which is exactly the invariant this also leans on. */
    const uint8_t *slots = slot_buf + 2;
    for (uint16_t i = 0; i < count; i++) {
        uint8_t slot  = slots[i];
        int16_t value = (int16_t)read_u16le(values + (size_t)i * 2);
        vm->vars[slot] = value;
    }
    free(values);
}

bool persist_save(const vn_vm_t *vm)
{
    ensure_slots();
    if (slot_count == 0) {
        return true;   /* nothing to persist is not a failure to persist it */
    }

    uint8_t *buf = malloc((size_t)slot_count * 2);
    if (!buf) {
        return false;
    }
    /* No bounds guard here either -- see persist_load()'s comment. */
    const uint8_t *slots = slot_buf + 2;
    for (uint16_t i = 0; i < slot_count; i++) {
        uint8_t slot  = slots[i];
        int16_t value = vm->vars[slot];
        buf[i * 2]     = (uint8_t)(value & 0xFF);
        buf[i * 2 + 1] = (uint8_t)((value >> 8) & 0xFF);
    }

    /* "w" deletes any existing DPERSIST and creates a fresh one, same as
     * save.c's save_write() -- no separate ti_Delete needed first. */
    uint8_t handle = ti_Open("DPERSIST", "w");
    if (!handle) {
        free(buf);
        return false;
    }
    size_t written = ti_Write(buf, (size_t)slot_count * 2, 1, handle);
    ti_Close(handle);
    free(buf);
    return written == 1;
}

