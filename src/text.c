/**
 * @file text.c
 * @brief Greedy word wrapping. See text.h.
 */

#include "text.h"

#include <string.h>

static void push_line(text_layout_t *out, const char *start, size_t len)
{
    if (out->count >= TEXT_MAX_LINES) {
        return;
    }
    out->lines[out->count].start = start;
    out->lines[out->count].len   = len;
    out->count++;
    out->total += len;
}

void text_wrap(text_layout_t *out, const char *str, unsigned max_width,
               text_measure_t measure, void *ctx)
{
    memset(out, 0, sizeof(*out));

    if (str == NULL || max_width == 0) {
        return;
    }

    const char *line = str;   /* start of the line being built     */
    const char *last = NULL;  /* last break opportunity seen       */
    const char *p    = str;

    while (*p != '\0' && out->count < TEXT_MAX_LINES) {
        if (*p == '\n') {
            push_line(out, line, (size_t)(p - line));
            line = p + 1;
            last = NULL;
            p++;
            continue;
        }

        if (*p == ' ') {
            last = p;
        }

        size_t len = (size_t)(p - line) + 1;
        if (measure(ctx, line, len) > max_width) {
            if (last != NULL && last > line) {
                /* Break at the space; it is consumed, not rendered. */
                push_line(out, line, (size_t)(last - line));
                line = last + 1;
                p    = line;
            } else {
                /* A single word wider than the box: hard-break it so the
                 * text stays inside the dialogue area. */
                size_t take = len > 1 ? len - 1 : 1;
                push_line(out, line, take);
                line += take;
                p     = line;
            }
            last = NULL;
            continue;
        }

        p++;
    }

    if (*line != '\0' && out->count < TEXT_MAX_LINES) {
        push_line(out, line, strlen(line));
    }
}

void text_clamp(text_layout_t *layout, const text_layout_t *full,
                size_t visible)
{
    memset(layout, 0, sizeof(*layout));

    for (uint8_t i = 0; i < full->count; i++) {
        size_t len = full->lines[i].len;

        if (visible == 0) {
            break;
        }
        if (len > visible) {
            len = visible;
        }

        layout->lines[layout->count].start = full->lines[i].start;
        layout->lines[layout->count].len   = len;
        layout->count++;
        layout->total += len;

        visible -= len;
    }
}
