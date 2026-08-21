/**
 * @file save.c
 * @brief See save.h.
 */

#include "save.h"
#include "assets.h"

#include <fileioc.h>

#include <stdio.h>
#include <string.h>

/* Plain fixed-layout struct, written with a single ti_Write.
 *
 * The header exists because "only ever read back by the same build" isn't
 * something this can assume: the player flashes a new DDLC.8xp over the
 * DSAVEn AppVars already in archive. A layout change usually changes
 * sizeof(save_blob_t) too, and ti_Read of a larger struct from a shorter
 * AppVar fails cleanly -- but only usually. Two layouts can easily come out
 * the same size (swap a field, reorder, trade one array's growth against
 * another's), and then an old save loads as plausible garbage: a pc pointing
 * into the middle of an instruction, variables holding another build's
 * values. Cheaper to stamp it than to debug it. */
#define SAVE_MAGIC   0x444C4353UL  /* "SCLD" */
#define SAVE_VERSION 2             /* 2: VN_MAX_VARS 64 -> 256 */

typedef struct {
    uint32_t   magic;
    uint16_t   version;
    uint32_t   pc;
    uint32_t   stack[VN_CALL_DEPTH];
    uint8_t    sp;
    int16_t    vars[VN_MAX_VARS];
    uint8_t    background;
    vn_actor_t actors[VN_MAX_CHARS];
    uint8_t    speaker;
    uint16_t   text_index;
} save_blob_t;

static void slot_name(uint8_t slot, char *out /* [9] */)
{
    sprintf(out, "DSAVE%u", slot);
}

bool save_exists(uint8_t slot)
{
    char name[9];
    slot_name(slot, name);

    uint8_t handle = ti_Open(name, "r");
    if (!handle) {
        return false;
    }
    ti_Close(handle);
    return true;
}

bool save_write(uint8_t slot, const vn_vm_t *vm)
{
    save_blob_t blob;
    blob.magic   = SAVE_MAGIC;
    blob.version = SAVE_VERSION;
    blob.pc = vm->pc;
    memcpy(blob.stack, vm->stack, sizeof(blob.stack));
    blob.sp = vm->sp;
    memcpy(blob.vars, vm->vars, sizeof(blob.vars));
    blob.background = vm->scene.background;
    memcpy(blob.actors, vm->scene.actors, sizeof(blob.actors));
    blob.speaker    = vm->scene.speaker;
    blob.text_index = vm->scene.text_index;

    char name[9];
    slot_name(slot, name);

    /* "w" deletes any existing AppVar of this name and creates a fresh one
     * in RAM -- no separate ti_Delete needed first. */
    uint8_t handle = ti_Open(name, "w");
    if (!handle) {
        return false;
    }
    size_t written = ti_Write(&blob, sizeof(blob), 1, handle);
    ti_Close(handle);
    return written == 1;
}

bool save_load(uint8_t slot, vn_vm_t *vm)
{
    char name[9];
    slot_name(slot, name);

    uint8_t handle = ti_Open(name, "r");
    if (!handle) {
        return false;
    }

    save_blob_t blob;
    size_t got = ti_Read(&blob, sizeof(blob), 1, handle);
    ti_Close(handle);
    if (got != 1) {
        return false;
    }
    /* A save from an older layout, or a same-sized AppVar that isn't one of
     * ours: refuse it rather than resuming into nonsense. The slot still
     * reads as existing, so the player sees a load that declines instead of
     * a slot that vanished. */
    if (blob.magic != SAVE_MAGIC || blob.version != SAVE_VERSION) {
        return false;
    }

    vm->pc = blob.pc;
    memcpy(vm->stack, blob.stack, sizeof(vm->stack));
    vm->sp = blob.sp;
    memcpy(vm->vars, blob.vars, sizeof(vm->vars));
    vm->scene.background = blob.background;
    memcpy(vm->scene.actors, blob.actors, sizeof(vm->scene.actors));
    vm->scene.speaker    = blob.speaker;
    vm->scene.text_index = blob.text_index;
    /* Load the target chunk so blob.text_index resolves against the correct
     * chunk string table, then resolve through vm->host->string() for
     * [player] substitution. */
    assets_load_chunk((uint8_t)VN_CHUNK_ID(blob.pc));
    vm->scene.text = vm->host->string(vm->host->ctx, blob.text_index);
    vm->status = VN_RUNNING;
    return true;
}

bool save_delete_all(void)
{
    bool ok = true;
    for (uint8_t slot = 1; slot <= SAVE_SLOTS; slot++) {
        char name[9];
        slot_name(slot, name);
        /* ti_Delete returns 0 on success; an already-absent slot (nothing
         * was ever saved there) isn't a failure of "erase everything",
         * only a slot that exists but genuinely can't be removed is. */
        if (ti_Delete(name) != 0 && save_exists(slot)) {
            ok = false;
        }
    }
    return ok;
}
