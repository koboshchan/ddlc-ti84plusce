#ifndef FAST_OPS_H
#define FAST_OPS_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

/* All drawing callers must provide non-overlapping buffers, valid pitches,
 * and framebuffer-clipped dimensions. Scaler output row count is <= 320;
 * source/destination sprite dimensions fit 16 bits; denominator is nonzero
 * and acc_x < div_x. This is a drawing primitive, not an unchecked general
 * resampler. IX/IY and SP are preserved under the CEdev ADL=1 C ABI. */
#ifdef __cplusplus
extern "C" {
#endif

/**
 * Fast 2x Nearest-Neighbor CG Upscaler (160x90 -> 320x180)
 *
 * Expands a 160x90 source buffer into 320x180 in the destination backbuffer
 * (stride = 320), streaming pixel rows directly in eZ80 registers without
 * stack allocation.
 *
 * @param dest Pointer to top-left of 320-wide destination area
 * @param src  Pointer to 160x90 source pixels (14,400 bytes)
 */
void fast_cg_upscale_2x(uint8_t *dest, const uint8_t *src);

/**
 * Variable-height 2x Nearest-Neighbor CG Upscaler (160xN -> 320x(2N))
 *
 * @param dest Pointer to top-left of 320-wide destination area
 * @param src  Pointer to 160x@p rows source pixels
 * @param rows Number of source rows to upscale (1..255; 0 is a no-op)
 */
void fast_cg_upscale_2x_rows(uint8_t *dest, const uint8_t *src, uint8_t rows);

/**
 * Fast 2D Rectangular Block Blit with Strides
 *
 * Copies a w * h rectangular block from src to dest using hardware LDIR.
 *
 * @param dest       Destination buffer pointer
 * @param dest_pitch Destination stride in bytes (e.g. 320 for screen)
 * @param src        Source buffer pointer
 * @param src_pitch  Source stride in bytes
 * @param w          Width in bytes
 * @param h          Height in rows
 */
void fast_rect_blit(uint8_t *dest, size_t dest_pitch,
                    const uint8_t *src, size_t src_pitch,
                    size_t w, size_t h);

/**
 * Fast 1.05x Character Zoom Horizontal Row Scaler (Bresenham 20:21 ratio)
 *
 * Scales one row of sprite pixels from src into dst, repeating every 20th
 * pixel to scale 20 -> 21.
 *
 * @param dst Destination row buffer (zw bytes)
 * @param src Source row buffer
 * @param zw  Destination width (output pixel count)
 */
void fast_zoom_row(uint8_t *dst, const uint8_t *src, size_t zw);

/**
 * Fast Circular Row Barrel-Shifter for Glitch Scanlines
 *
 * Circularly shifts one 320-byte scanline row by off_wrapped pixels using
 * block copy transfers and a single static scratch buffer.
 *
 * @param row         Pointer to 320-byte scanline in framebuffer
 * @param scratch     Pointer to 320-byte scratch buffer
 * @param off_wrapped Shift offset in [0, 320)
 */
void fast_row_shift(uint8_t *row, uint8_t *scratch, size_t off_wrapped);

/**
 * Fast Row Scaler with Transparency Skipping
 *
 * Scales one row of sprite pixels from src_row into dest with Bresenham
 * stepping in 24-bit eZ80 registers, skipping transparent pixels (color index 0).
 *
 * @param dest    Destination scanline pointer in framebuffer
 * @param src_row Source row pointer in uncompressed sprite
 * @param count   Number of destination pixels to output
 * @param acc_x   Initial Bresenham error/accumulator
 * @param step_x  Bresenham step numerator (source width)
 * @param div_x   Bresenham step denominator (destination width)
 */
void fast_scale_row_trans(uint8_t *dest, const uint8_t *src_row, size_t count,
                          unsigned int acc_x, unsigned int step_x, unsigned int div_x);

/**
 * Fast 2D Sprite Scaler with Transparency Skipping
 *
 * Scales an uncompressed 2D sprite directly into the destination buffer with
 * Bresenham stepping in both axes, skipping color index 0.
 *
 * @param dest       Destination buffer pointer (top-left of destination rect)
 * @param dest_pitch Destination stride in bytes (e.g. 320 for screen)
 * @param src        Source sprite pixel buffer
 * @param src_w      Source sprite width in pixels
 * @param src_h      Source sprite height in pixels
 * @param dst_w      Scaled destination width in pixels
 * @param dst_h      Scaled destination height in pixels
 */
void fast_scale_sprite_trans(uint8_t *dest, size_t dest_pitch,
                             const uint8_t *src, size_t src_w, size_t src_h,
                             size_t dst_w, size_t dst_h);

/**
 * Skip @p n_rows rows in an RLET sprite data stream without decoding pixels.
 *
 * @param rlet   Pointer into gfx_rletsprite_t->data at a row boundary
 * @param n_rows Number of rows to advance
 * @param width  Sprite width in pixels (1..255)
 * @return Updated pointer at the start of row @p n_rows
 */
const uint8_t *fast_rlet_skip_rows(const uint8_t *rlet, size_t n_rows, uint8_t width);

/**
 * Decode a single RLET row into an uncompressed destination row buffer.
 * Transparent runs are written as 0.
 *
 * @param dst_row Destination buffer of @p width bytes
 * @param rlet    Pointer into gfx_rletsprite_t->data at a row boundary
 * @param width   Sprite width in pixels (1..255)
 * @return Updated pointer at the start of the following row
 */
const uint8_t *fast_rlet_decode_row(uint8_t *dst_row, const uint8_t *rlet, uint8_t width);

/**
 * Blit the 320x240 scrolling title background from a 370x50 tile strip.
 *
 * @param dest  320x240 destination framebuffer
 * @param strip 370x50 tiled strip in flash
 * @param px    Horizontal tile offset in [0, 50)
 * @param py    Vertical tile offset in [0, 50)
 */
void fast_title_bg(uint8_t *dest, const uint8_t *strip, uint8_t px, uint8_t py);

/**
 * Opaque nearest-neighbor horizontal row stretch (dst_w <= 511, src_w >= 1).
 *
 * @param dst   Destination row buffer (dst_w bytes)
 * @param src   Source row buffer (src_w bytes)
 * @param dst_w Output width in pixels
 * @param src_w Input width in pixels
 */
void fast_stretch_row(uint8_t *dst, const uint8_t *src, size_t dst_w, size_t src_w);

/**
 * 4x4 Bayer ordered stipple copy across 320x180 scene area (step 0..3 -> 25/50/75/100%).
 *
 * @param dst  Destination buffer (320x180)
 * @param src  Source buffer (320x180)
 * @param step Dissolve pass index (0..3)
 */
void fast_bayer_dissolve(uint8_t *dst, const uint8_t *src, uint8_t step);

#ifdef __cplusplus
}
#endif

#endif /* FAST_OPS_H */
