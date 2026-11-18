/**
 * @file text.c
 * @brief Greedy word wrapping and compact formatting. See text.h.
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

    const char *line = str;
    const char *p    = str;

    while (*p != '\0' && out->count < TEXT_MAX_LINES) {
        /* Advance to the end of the current word or newline/NUL. */
        const char *word_start = p;
        while (*p != '\0' && *p != '\n' && *p != ' ') {
            p++;
        }

        size_t len = (size_t)(p - line);
        if (len > 0 && measure(ctx, line, len) > max_width) {
            if (word_start > line) {
                /* Break before this word (consuming the trailing space). */
                const char *end = word_start;
                if (end > line && end[-1] == ' ') {
                    end--;
                }
                push_line(out, line, (size_t)(end - line));
                line = word_start;
                p    = word_start;
                continue;
            }
            /* Single word wider than max_width: binary-step or char-scan within it. */
            size_t take = 1;
            while (take < len && measure(ctx, line, take + 1) <= max_width) {
                take++;
            }
            push_line(out, line, take);
            line += take;
            p     = line;
            continue;
        }

        if (*p == '\n') {
            push_line(out, line, len);
            line = p + 1;
            p    = line;
        } else if (*p == ' ') {
            /* Include the space if there is more text, or check it on the next word. */
            p++;
        }
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

int mini_vsnprintf(char *out, size_t max, const char *fmt, va_list ap)
{
    if (!out || max == 0) return 0;
    size_t written = 0;
    const char *p = fmt;

    while (*p && written + 1 < max) {
        if (*p != '%') {
            out[written++] = *p++;
            continue;
        }
        p++;
        if (*p == '%') {
            out[written++] = *p++;
            continue;
        }

        int width = 0;
        char pad = ' ';
        if (*p == '0') {
            pad = '0';
            p++;
        }
        while (*p >= '0' && *p <= '9') {
            width = width * 10 + (*p - '0');
            p++;
        }

        char spec = *p;
        if (!spec) {
            break;
        }
        p++;

        if (spec == 's') {
            const char *s = va_arg(ap, const char *);
            if (!s) s = "(null)";
            while (*s && written + 1 < max) {
                out[written++] = *s++;
            }
            continue;
        }

        if (spec == 'd' || spec == 'u' || spec == 'x' || spec == 'X') {
            unsigned uval;
            if (spec == 'd') {
                int val = va_arg(ap, int);
                if (val < 0) {
                    if (written + 1 < max) out[written++] = '-';
                    uval = (unsigned)(-val);
                } else {
                    uval = (unsigned)val;
                }
            } else {
                uval = va_arg(ap, unsigned);
            }

            unsigned base = (spec == 'x' || spec == 'X') ? 16u : 10u;
            char hex_a = (spec == 'X') ? 'A' : 'a';
            char buf[12];
            int bi = 0;
            do {
                unsigned rem = uval % base;
                buf[bi++] = (rem < 10u) ? (char)('0' + rem) : (char)(hex_a + rem - 10u);
                uval /= base;
            } while (uval > 0 && bi < 12);

            while (bi < width && bi < 12 && pad == '0') {
                buf[bi++] = '0';
            }
            while (bi > 0 && written + 1 < max) {
                out[written++] = buf[--bi];
            }
            continue;
        }

        if (written + 1 < max) {
            out[written++] = spec;
        }
    }

    out[written] = '\0';
    return (int)written;
}

int mini_snprintf(char *out, size_t max, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int ret = mini_vsnprintf(out, max, fmt, ap);
    va_end(ap);
    return ret;
}

int mini_sprintf(char *out, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int ret = mini_vsnprintf(out, 65535, fmt, ap);
    va_end(ap);
    return ret;
}
