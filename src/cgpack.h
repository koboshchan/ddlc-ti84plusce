/**
 * @file cgpack.h
 * @brief Optional full-resolution CG pack, read from a FAT32-formatted USB
 * drive plugged in via a USB-OTG adapter.
 *
 * CGs ship on-calc at half resolution (image_resolve.py's CG_SIZE) to fit
 * the archive budget -- see assets.c's assets_scene(). If a drive containing
 * a matching pack (tools/export_cgpack.py's output, copied onto the drive as
 * /DDLC/) is connected, every CG draw transparently upgrades to the full-res
 * version instead. No drive, or a drive without a matching pack: identical
 * to today's half-res-only behavior.
 *
 * "Matching" is checked against DCGVER (assets_cgpack_fingerprint()) against
 * the pack's own /DDLC/BUILD.ID -- CG scene ids are positional, assigned in
 * compile order (see image_resolve.py's scene_id()), so a pack built from a
 * different --files selection could otherwise show the wrong picture for a
 * given id without any error.
 */

#ifndef CGPACK_H
#define CGPACK_H

#include <stdbool.h>
#include <stdint.h>

/**
 * Starts the USB driver. Never blocks -- returns immediately whether or not
 * anything is plugged in; a drive (if any) is detected asynchronously via
 * cgpack_poll(). Call once, after assets_init() succeeds.
 */
void cgpack_init(void);

/**
 * Releases the USB driver. Safe to call even if cgpack_init() was never
 * called (e.g. assets_init() failed first) or no drive was ever seen. Call
 * once before the program exits, alongside render_end().
 */
void cgpack_end(void);

/**
 * Processes any pending USB device events (connect/enable/disable/
 * disconnect), non-blocking. Call every frame -- main.c's input_poll() is
 * the natural spot, since it already runs from every one of the game's
 * input loops.
 */
void cgpack_poll(void);

/**
 * Reads CG @p id's full-res pixels (320x180 raw palette indices, SCREEN_W
 * row stride) directly into @p dest -- typically the graphx draw buffer,
 * same contract as assets_scene(). Only pixels come from the pack; the
 * palette doesn't need to (see assets_scene_palette()'s own comment for
 * why). False (leaving @p dest untouched) if the pack isn't available or
 * the read fails, in which case the caller should fall back to the built-in
 * half-res decompress+upscale.
 */
bool cgpack_read_pixels(uint8_t id, uint8_t *dest);

/**
 * Diagnostic snapshot of the USB/pack state, for the debug menu's external
 * CG test -- not needed by cgpack_read_pixels() itself, which already
 * degrades correctly on its own.
 */
typedef enum {
    CGPACK_NO_USB,       /* usb_Init() itself failed/never ran */
    CGPACK_NO_DRIVE,     /* usb ready, nothing plugged in/mounted */
    CGPACK_WRONG_PACK,   /* a drive mounted, but no matching /DDLC/BUILD.ID */
    CGPACK_READY,        /* mounted AND fingerprint matched -- reads will work */
} cgpack_status_t;

cgpack_status_t cgpack_status(void);

#endif /* CGPACK_H */
