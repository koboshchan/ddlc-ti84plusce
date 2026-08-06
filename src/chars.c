/**
 * @file chars.c
 * @brief On-calc character-presence AppVars. See chars.h.
 */

#include "chars.h"

#include <fileioc.h>

/* 8-char TI variable name limit; all four names fit within it. */
static const char *const chars_name[CHAR_COUNT] = {
    "SAYORI", "NATSUKI", "YURI", "MONIKA",
};

void chars_init(void)
{
    for (uint8_t i = 0; i < CHAR_COUNT; i++) {
        /* Already exists (present, or previously deleted-and-not-recreated
         * by this loop since "r" only succeeds on the former) -- leave it
         * alone either way. Only a missing AppVar gets created. */
        uint8_t handle = ti_Open(chars_name[i], "r");
        if (handle) {
            ti_Close(handle);
            continue;
        }

        handle = ti_Open(chars_name[i], "w");
        if (handle) {
            ti_SetArchiveStatus(true, handle);
            ti_Close(handle);
        }
    }
}

bool chars_present(uint8_t character)
{
    if (character >= CHAR_COUNT) {
        return false;
    }

    uint8_t handle = ti_Open(chars_name[character], "r");
    if (!handle) {
        return false;
    }
    ti_Close(handle);
    return true;
}

bool chars_delete(uint8_t character)
{
    if (character >= CHAR_COUNT) {
        return false;
    }
    return ti_Delete(chars_name[character]) != 0;
}
