/**
 * @file pak.h
 * @brief Shared ~64KB AppVar asset pack reader (DPKIDX + DPK0..DPKn).
 *
 * When DPKIDX is present on-calc, named assets and LUT-indexed groups are
 * packed tightly into shared ~64KB AppVars (DPK0..DPKn), with spanning
 * support across volume boundaries for streamable / RAM-loaded assets.
 * When DPKIDX is absent (legacy bundles or standalone host unit tests), all
 * helpers transparently fall back to individual standalone AppVars.
 */

#ifndef PAK_H
#define PAK_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define PAK_FLAG_SPANNING 1u

typedef struct {
    uint8_t        handle;
    const uint8_t *ptr;
    size_t         size;
} pak_view_t;

#if defined(__TICE__) || defined(DDLC_PAK_ENABLE)

bool pak_init(void);
void pak_shutdown(void);
bool pak_active(void);

bool pak_stat(const char *name, uint8_t *vol_out, uint16_t *off_out,
              size_t *len_out, uint8_t *flags_out);

bool pak_read_span(uint8_t vol, uint16_t off, size_t sub_off,
                   void *dst, size_t len);

uint8_t *pak_read_whole(const char *name, uint16_t *size_out);

bool pak_read_asset(const char *name, size_t sub_off, void *dst, size_t len);

bool pak_view_open(const char *name, pak_view_t *out);
void pak_view_close(pak_view_t *v);

uint8_t pak_open_vol(const char *legacy_prefix, uint8_t vol);

#else
/* Standalone host unit-test fallback (single-file test harnesses that mock
 * ti_Open directly without linking pak.c). */
#include <fileioc.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static inline bool pak_init(void) { return false; }
static inline void pak_shutdown(void) {}
static inline bool pak_active(void) { return false; }

static inline bool pak_stat(const char *name, uint8_t *vol_out, uint16_t *off_out,
                            size_t *len_out, uint8_t *flags_out)
{
    (void)name; (void)vol_out; (void)off_out; (void)len_out; (void)flags_out;
    return false;
}

static inline bool pak_read_span(uint8_t vol, uint16_t off, size_t sub_off,
                                 void *dst, size_t len)
{
    (void)vol; (void)off; (void)sub_off; (void)dst; (void)len;
    return false;
}

static inline uint8_t *pak_read_whole(const char *name, uint16_t *size_out)
{
    uint8_t h = ti_Open(name, "r");
    if (!h) return NULL;
    uint16_t sz = ti_GetSize(h);
    uint8_t *buf = malloc(sz ? sz : 1u);
    if (!buf) { ti_Close(h); return NULL; }
    if (sz) memcpy(buf, ti_GetDataPtr(h), sz);
    ti_Close(h);
    if (size_out) *size_out = sz;
    return buf;
}

static inline bool pak_read_asset(const char *name, size_t sub_off, void *dst, size_t len)
{
    uint8_t h = ti_Open(name, "r");
    if (!h) return false;
    size_t sz = ti_GetSize(h);
    if (sub_off > sz || len > sz - sub_off) { ti_Close(h); return false; }
    memcpy(dst, (const uint8_t *)ti_GetDataPtr(h) + sub_off, len);
    ti_Close(h);
    return true;
}

static inline bool pak_view_open(const char *name, pak_view_t *out)
{
    uint8_t h = ti_Open(name, "r");
    if (!h) return false;
    out->handle = h;
    out->ptr    = (const uint8_t *)ti_GetDataPtr(h);
    out->size   = ti_GetSize(h);
    return true;
}

static inline void pak_view_close(pak_view_t *v)
{
    if (v && v->handle) {
        ti_Close(v->handle);
        v->handle = 0;
        v->ptr    = NULL;
        v->size   = 0;
    }
}

static inline uint8_t pak_open_vol(const char *legacy_prefix, uint8_t vol)
{
    char name[9];
    snprintf(name, sizeof(name), "%s%u", legacy_prefix, vol);
    return ti_Open(name, "r");
}

#endif

#endif
