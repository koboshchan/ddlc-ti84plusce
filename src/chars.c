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

bool chars_init(void)
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
            return true;
        }
    }
    /* Refuse partial reset: a surviving progress/save AppVar must never
     * be combined with recreated characters and a completed reset marker. */
    if (ti_Delete("DPERSIST") == 0) {
        handle = ti_Open("DPERSIST", "r");
        if (handle) {
            ti_Close(handle);
            return false;
        }
    }
    if (!save_delete_all()) {
        return false;
    }

    /* First launch: create the 4 character AppVars in Archive */
    for (uint8_t i = 0; i < CHAR_COUNT; i++) {
        uint8_t ch = ti_Open(chars_name[i], "w");
        if (!ch) {
            return false;
        }
        uint8_t dummy = 0xFF;
        bool written = ti_Write(&dummy, 1, 1, ch) == 1;
        if (written) {
            ti_SetArchiveStatus(true, ch);
        }
        ti_Close(ch);
        if (!written) {
            return false;
        }
    }

    handle = ti_Open("FIRSTRUN", "w");
    if (!handle) {
        return false;
    }
    uint8_t dummy = 1;
    bool written = ti_Write(&dummy, 1, 1, handle) == 1;
    if (written) {
        ti_SetArchiveStatus(true, handle);
    }
    ti_Close(handle);
    return written;
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
