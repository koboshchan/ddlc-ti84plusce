/**
 * @file name.c
 * @brief See name.h.
 */

#include "name.h"

#include <fileioc.h>
#include <string.h>

#define NAME_APPVAR "DNAME"

bool name_exists(void)
{
    uint8_t handle = ti_Open(NAME_APPVAR, "r");
    if (!handle) {
        return false;
    }
    ti_Close(handle);
    return true;
}

bool name_load(char *out, size_t cap)
{
    uint8_t handle = ti_Open(NAME_APPVAR, "r");
    if (!handle) {
        return false;
    }

    uint16_t size = ti_GetSize(handle);
    if (size >= cap) {
        size = (uint16_t)(cap - 1);
    }
    size_t got = ti_Read(out, 1, size, handle);
    ti_Close(handle);

    out[got] = '\0';
    return true;
}

bool name_save(const char *name)
{
    uint8_t handle = ti_Open(NAME_APPVAR, "w");
    if (!handle) {
        return false;
    }
    size_t len = strlen(name);
    size_t written = ti_Write(name, 1, len, handle);
    ti_Close(handle);
    return written == len;
}
