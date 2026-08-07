/**
 * @file assets.h
 * @brief Loads the AppVars tools/import_game.py packages (see docs/FORMAT.md).
 *
 * Everything here is read-only, zero-copy where possible: strings and
 * sprites are pointers straight into the (archived) AppVar's flash bytes,
 * never buffered in RAM. Only backgrounds are decompressed, into a
 * caller-supplied destination (the graphx draw buffer).
 */

#ifndef ASSETS_H
#define ASSETS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <graphx.h>

/**
 * Opens the DSCRIPT, DSPRn, DSCNn, and DPALGAME AppVars and loads
 * gfx_palette. Call once before vn_init(). Returns false if any required
 * AppVar is missing -- e.g. the user sent the program but not the asset
 * bundle.
 */
bool assets_init(void);

/** Zero-copy pointer to the loaded chunk's bytecode, plus its size. */
const uint8_t *assets_script(size_t *size_out);

/** Zero-copy pointer to string @p index's NUL-terminated UTF-8 bytes. */
const char *assets_string(uint16_t index);

/**
 * Zero-copy pointer to sprite @p id, ready for gfx_RLETSprite(). Returns
 * NULL if @p id is out of range or its AppVar is missing.
 */
const gfx_rletsprite_t *assets_sprite(uint8_t id);

/**
 * Decompresses background/CG @p id's raw 320x180 palette-index pixels
 * directly into @p dest (typically the graphx draw buffer). Returns false
 * if @p id is out of range or its AppVar is missing.
 */
bool assets_scene(uint8_t id, uint8_t *dest);

#endif /* ASSETS_H */
