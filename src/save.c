/**
 * @file save.c
 * @brief See save.h.
 */

#include "save.h"

#include <fileioc.h>

#include <stdio.h>
#include <string.h>

/* Plain fixed-layout struct, written with a single ti_Write -- no LUT or
 * versioning needed since it's only ever read back by the exact same
 * engine build that wrote it (see save.h). */
typedef struct {
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

    vm->pc = blob.pc;
    memcpy(vm->stack, blob.stack, sizeof(vm->stack));
    vm->sp = blob.sp;
    memcpy(vm->vars, blob.vars, sizeof(vm->vars));
    vm->scene.background = blob.background;
    memcpy(vm->scene.actors, blob.actors, sizeof(vm->scene.actors));
    vm->scene.speaker    = blob.speaker;
    vm->scene.text_index = blob.text_index;
    /* Through vm->host->string(), not assets_string() directly: the host
     * (main.c's host_string) also does "[player]" substitution, and a
     * loaded line needs that exactly as much as one reached by playing
     * forward does. */
    vm->scene.text = vm->host->string(vm->host->ctx, blob.text_index);
    vm->status = VN_RUNNING;
    return true;
}
