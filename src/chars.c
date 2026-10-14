/**
 * @file chars.c
 * @brief On-calc character-presence AppVars. See chars.h.
 */

#include "chars.h"
#include "save.h"

#include <fileioc.h>

/* 8-char TI variable name limit; all four names fit within it. */
static const char *const chars_name[CHAR_COUNT] = {
    "SAYORI", "NATSUKI", "YURI", "MONIKA",
};

void chars_init(void)
{
    /* Either reset marker being removed requests a clean install. Keep
     * character deletions intact on ordinary launches, but never combine
     * recreated characters with an old act/autoload or a stale save. */
    uint8_t handle = ti_Open("FIRSTRUN", "r");
    if (handle) {
        ti_Close(handle);
        handle = ti_Open("DPERSIST", "r");
        if (handle) {
            ti_Close(handle);
            return;
        }
    }
    ti_Delete("DPERSIST");
    save_delete_all();

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
        uint8_t dummy = 1;
        ti_Write(&dummy, 1, 1, handle);
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
    const char *name = chars_name[character];
    /* FILEIOC returns zero on failure. An already-absent character is
     * already in the requested state, not an erase failure. */
    return ti_Delete(name) != 0 || !chars_present(character);
}
