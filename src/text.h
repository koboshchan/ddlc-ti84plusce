/**
 * @file text.h
 * @brief Greedy word wrapping for the dialogue box.
 *
 * Like vn.c this is free of calculator headers: the caller supplies a
 * width-measuring callback, so the same wrapper serves graphx on-calc and a
 * fixed-width terminal on the host.
 */

#ifndef TEXT_H
#define TEXT_H

#include <stdint.h>
#include <stddef.h>

#define TEXT_MAX_LINES 4    /* lines that fit in the 60px dialogue box */

/** Measure the rendered width of @p len bytes starting at @p str, in pixels. */
typedef unsigned (*text_measure_t)(void *ctx, const char *str, size_t len);

/** One wrapped line: a slice of the source string, not a copy. */
typedef struct {
    const char *start;
    size_t      len;
} text_line_t;

typedef struct {
    text_line_t lines[TEXT_MAX_LINES];
    uint8_t     count;
    size_t      total;   /* total bytes across all lines, for the typewriter */
} text_layout_t;

/**
 * Wrap @p str to @p max_width pixels, greedily and on whitespace.
 *
 * Words longer than a full line are hard-broken rather than overflowing.
 * Text beyond TEXT_MAX_LINES is dropped -- the script compiler is responsible
 * for splitting long lines into separate OP_SAYs.
 */
void text_wrap(text_layout_t *out, const char *str, unsigned max_width,
               text_measure_t measure, void *ctx);

/**
 * Clamp @p layout to the first @p visible characters, for the typewriter
 * reveal. Lines past the cut are removed and the partial line is shortened.
 */
void text_clamp(text_layout_t *layout, const text_layout_t *full,
                size_t visible);

#endif /* TEXT_H */
