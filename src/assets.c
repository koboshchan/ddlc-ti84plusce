/**
 * @file assets.c
 * @brief AppVar loader. See assets.h and docs/FORMAT.md's "Chunking" and
 * "Image assets" sections for the on-disk formats this reads.
 */

#include "assets.h"

#include <compression.h>
#include <fileioc.h>

#include <stdio.h>
#include <string.h>

/* A single resident chunk's string pool, generously capped -- script+ch0
 * currently uses 352. Table of pointers, not copies: each points directly
 * into the DSCRIPT AppVar's flash bytes. */
#define MAX_STRINGS 1024

static const uint8_t *script_code;
static size_t         script_code_size;
static const char     *string_ptrs[MAX_STRINGS];
static uint16_t        string_count;

/* Sprite/scene lookup tables: tools/import_game.py's build_lut() format --
 * u16 count, then per entry u8 appvar_index, u16 offset, u16 length. Kept
 * as raw pointers into their own (tiny) AppVars and parsed on each lookup;
 * there are at most a few hundred entries, so this is cheap. */
static const uint8_t *sprite_lut;
static uint16_t        sprite_lut_count;
static const uint8_t *scene_lut;
static uint16_t        scene_lut_count;

static uint16_t read_u16le(const uint8_t *p)
{
    return (uint16_t)(p[0] | (uint16_t)(p[1] << 8));
}

/**
 * Opens @p name read-only, returns a direct pointer to its data, and closes
 * the handle immediately. Safe for archived AppVars: the returned pointer
 * addresses flash directly and stays valid after close (per fileioc.h's
 * ti_GetDataPtr docs) as long as no *other* AppVar is created, deleted, or
 * resized while it's in use.
 *
 * NOTE: src/chars.c's Act 2/3 file-deletion effect (ti_Delete) is exactly
 * such an operation, but nothing compiled into the current script chunk
 * calls it yet (compile_script.py currently drops `delete_character(...)`
 * as an unsupported Python statement) -- if/when that gets wired up, any
 * pointer obtained here across a deletion needs revalidating.
 */
static const uint8_t *open_direct(const char *name)
{
    uint8_t handle = ti_Open(name, "r");
    if (!handle) {
        return NULL;
    }
    const uint8_t *data = ti_GetDataPtr(handle);
    ti_Close(handle);
    return data;
}

static bool load_script(void)
{
    const uint8_t *data = open_direct("DSCRIPT");
    if (!data) {
        return false;
    }

    uint16_t code_len = read_u16le(data);
    script_code = data + 2;
    script_code_size = code_len;

    const uint8_t *p = data + 2 + code_len;
    string_count = read_u16le(p);
    p += 2;

    if (string_count > MAX_STRINGS) {
        return false;
    }

    for (uint16_t i = 0; i < string_count; i++) {
        uint16_t len = read_u16le(p);
        p += 2;
        string_ptrs[i] = (const char *)p;
        p += (size_t)len + 1; /* +1: trailing NUL, see vnasm.py's to_chunk_bytes */
    }
    return true;
}

static bool load_lut(const char *name, const uint8_t **lut_out, uint16_t *count_out)
{
    const uint8_t *data = open_direct(name);
    if (!data) {
        return false;
    }
    *count_out = read_u16le(data);
    *lut_out = data + 2;
    return true;
}

bool assets_init(void)
{
    if (!load_script()) {
        return false;
    }
    if (!load_lut("DSPRLUT", &sprite_lut, &sprite_lut_count)) {
        return false;
    }
    if (!load_lut("DSCNLUT", &scene_lut, &scene_lut_count)) {
        return false;
    }

    const uint8_t *pal = open_direct("DPALGAME");
    if (!pal) {
        return false;
    }
    memcpy(gfx_palette, pal, 256 * sizeof(uint16_t));

    return true;
}

const uint8_t *assets_script(size_t *size_out)
{
    if (size_out) {
        *size_out = script_code_size;
    }
    return script_code;
}

const char *assets_string(uint16_t index)
{
    return index < string_count ? string_ptrs[index] : "";
}

/* LUT entry layout: u8 appvar_index, u16 offset, u16 length (5 bytes). */
static bool lut_lookup(const uint8_t *lut, uint16_t count, uint8_t id,
                       uint8_t *appvar_out, uint16_t *offset_out)
{
    if (id >= count) {
        return false;
    }
    const uint8_t *e = lut + (size_t)id * 5;
    *appvar_out = e[0];
    *offset_out = read_u16le(e + 1);
    return true;
}

const gfx_rletsprite_t *assets_sprite(uint8_t id)
{
    uint8_t appvar_idx;
    uint16_t offset;
    if (!lut_lookup(sprite_lut, sprite_lut_count, id, &appvar_idx, &offset)) {
        return NULL;
    }

    char name[9];
    sprintf(name, "DSPR%u", appvar_idx);
    const uint8_t *data = open_direct(name);
    if (!data) {
        return NULL;
    }

    return (const gfx_rletsprite_t *)(data + offset);
}

bool assets_scene(uint8_t id, uint8_t *dest)
{
    uint8_t appvar_idx;
    uint16_t offset;
    if (!lut_lookup(scene_lut, scene_lut_count, id, &appvar_idx, &offset)) {
        return false;
    }

    char name[9];
    sprintf(name, "DSCN%u", appvar_idx);
    const uint8_t *data = open_direct(name);
    if (!data) {
        return false;
    }

    zx0_Decompress(dest, data + offset);
    return true;
}
