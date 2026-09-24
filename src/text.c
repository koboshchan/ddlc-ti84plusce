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
        p++; // skip '%'
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

        if (*p == 's') {
            p++;
            const char *s = va_arg(ap, const char *);
            if (!s) s = "(null)";
            while (*s && written + 1 < max) {
                out[written++] = *s++;
            }
        } else if (*p == 'd') {
            p++;
            int val = va_arg(ap, int);
            unsigned uval;
            if (val < 0) {
                if (written + 1 < max) out[written++] = '-';
                uval = (unsigned)(-val);
            } else {
                uval = (unsigned)val;
            }
            char buf[12];
            int bi = 0;
            do {
                buf[bi++] = (char)('0' + (uval % 10));
                uval /= 10;
            } while (uval > 0 && bi < 12);
            while (bi < width && bi < 12 && pad == '0') {
                buf[bi++] = '0';
            }
            while (bi > 0 && written + 1 < max) {
                out[written++] = buf[--bi];
            }
        } else if (*p == 'u') {
            p++;
            unsigned uval = va_arg(ap, unsigned);
            char buf[12];
            int bi = 0;
            do {
                buf[bi++] = (char)('0' + (uval % 10));
                uval /= 10;
            } while (uval > 0 && bi < 12);
            while (bi < width && bi < 12 && pad == '0') {
                buf[bi++] = '0';
            }
            while (bi > 0 && written + 1 < max) {
                out[written++] = buf[--bi];
            }
        } else if (*p == 'x' || *p == 'X') {
            char base = (*p == 'X') ? 'A' : 'a';
            p++;
            unsigned uval = va_arg(ap, unsigned);
            char buf[12];
            int bi = 0;
            do {
                unsigned rem = uval & 0xF;
                buf[bi++] = (rem < 10) ? (char)('0' + rem) : (char)(base + rem - 10);
                uval >>= 4;
            } while (uval > 0 && bi < 12);
            while (bi < width && bi < 12 && pad == '0') {
                buf[bi++] = '0';
            }
            while (bi > 0 && written + 1 < max) {
                out[written++] = buf[--bi];
            }
        } else {
            if (written + 1 < max) out[written++] = *p++;
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
