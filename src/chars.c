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
    /* Present means some previous boot (of this same flashed .b84) already
     * ran this -- the 4 character AppVars are whatever the player's left
     * them as since, and this function has nothing left to do. */
    uint8_t handle = ti_Open("FIRSTRUN", "r");
    if (handle) {
        ti_Close(handle);
        return;
    }

    /* First launch: create the 4 character AppVars in Archive */
    for (uint8_t i = 0; i < CHAR_COUNT; i++) {
        uint8_t ch = ti_Open(chars_name[i], "w");
        if (ch) {
            uint8_t dummy = 0xFF;
            ti_Write(&dummy, 1, 1, ch);
            ti_SetArchiveStatus(true, ch);
            ti_Close(ch);
        }
    }

    handle = ti_Open("FIRSTRUN", "w");
    if (handle) {
        ti_SetArchiveStatus(true, handle);
        ti_Close(handle);
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
    return ti_Delete(chars_name[character]) == 0;
}
