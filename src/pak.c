/**
 * @file pak.c
 * @brief Shared ~64KB AppVar asset pack reader (DPKIDX + DPK0..DPKn).
 */

#include "pak.h"

#if defined(__TICE__) || defined(DDLC_PAK_ENABLE)

#include <fileioc.h>
#include <stdlib.h>
#include <string.h>
#include "perf_memory.h"

#define PAK_HDR_SIZE   8u
#define PAK_ENTRY_SIZE 16u
#define PAK_NOINLINE   __attribute__((noinline))

static uint8_t *idx_buf;
static uint16_t idx_count;

static uint16_t rd16(const uint8_t *p)
{
    return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}

static size_t rd24(const uint8_t *p)
{
    return (size_t)p[0] | ((size_t)p[1] << 8) | ((size_t)p[2] << 16);
}

PAK_NOINLINE bool pak_init(void)
{
    if (idx_buf) {
        return true;
    }
    uint8_t h = ti_Open("DPKIDX", "r");
    if (!h) {
        return false;
    }
    size_t sz = ti_GetSize(h);
    if (sz < PAK_HDR_SIZE) {
        ti_Close(h);
        return false;
    }
    const uint8_t *hdr = (const uint8_t *)ti_GetDataPtr(h);
    if (memcmp(hdr, "DPK1", 4) != 0) {
        ti_Close(h);
        return false;
    }
    uint16_t count = rd16(hdr + 4);
    size_t table_bytes = (size_t)count * PAK_ENTRY_SIZE;
    if (PAK_HDR_SIZE + table_bytes > sz) {
        ti_Close(h);
        return false;
    }
    idx_buf = malloc(table_bytes ? table_bytes : 1u);
    if (!idx_buf) {
        ti_Close(h);
        return false;
    }
    if (table_bytes) {
        memcpy(idx_buf, hdr + PAK_HDR_SIZE, table_bytes);
    }
    idx_count = count;
    ti_Close(h);
    return true;
}

void pak_shutdown(void)
{
    free(idx_buf);
    idx_buf = NULL;
    idx_count = 0;
}

bool pak_active(void)
{
    return idx_buf != NULL;
}

PAK_NOINLINE bool pak_stat(const char *name, uint8_t *vol_out, uint16_t *off_out,
                           size_t *len_out, uint8_t *flags_out)
{
    if (!idx_buf || !idx_count || !name) {
        return false;
    }
    char key[8];
    memset(key, 0, sizeof(key));
    for (uint8_t i = 0; i < 8 && name[i]; i++) {
        key[i] = name[i];
    }

    uint16_t lo = 0;
    uint16_t hi = idx_count;
    while (lo < hi) {
        uint16_t mid = (uint16_t)(lo + ((hi - lo) >> 1));
        const uint8_t *e = idx_buf + (size_t)mid * PAK_ENTRY_SIZE;
        int cmp = memcmp(e, key, 8);
        if (cmp == 0) {
            if (vol_out)   *vol_out   = e[8];
            if (flags_out) *flags_out = e[9];
            if (off_out)   *off_out   = rd16(e + 10);
            if (len_out)   *len_out   = rd24(e + 12);
            return true;
        }
        if (cmp < 0) {
            lo = (uint16_t)(mid + 1);
        } else {
            hi = mid;
        }
    }
    return false;
}

static char *append_u8(char *p, uint8_t v)
{
    if (v >= 100u) {
        *p++ = (char)('0' + v / 100u);
        v %= 100u;
        *p++ = (char)('0' + v / 10u);
        *p++ = (char)('0' + v % 10u);
    } else if (v >= 10u) {
        *p++ = (char)('0' + v / 10u);
        *p++ = (char)('0' + v % 10u);
    } else {
        *p++ = (char)('0' + v);
    }
    *p = '\0';
    return p;
}

static PAK_NOINLINE uint8_t open_dpk_vol(uint8_t vol)
{
    char vname[9];
    vname[0] = 'D'; vname[1] = 'P'; vname[2] = 'K';
    append_u8(vname + 3, vol);
    return ti_Open(vname, "r");
}

PAK_NOINLINE bool pak_read_span(uint8_t vol, uint16_t off, size_t sub_off,
                                void *dst, size_t len)
{
    uint8_t *out = (uint8_t *)dst;
    size_t cur_off = (size_t)off + sub_off;
    uint8_t cur_vol = vol;

    while (len > 0) {
        uint8_t h = open_dpk_vol(cur_vol);
        if (!h) {
            return false;
        }
        size_t vsz = ti_GetSize(h);
        if (cur_off >= vsz) {
            ti_Close(h);
            cur_off -= vsz;
            cur_vol++;
            continue;
        }
        size_t avail = vsz - cur_off;
        size_t take = (len < avail) ? len : avail;
        memcpy(out, (const uint8_t *)ti_GetDataPtr(h) + cur_off, take);
        ti_Close(h);

        out += take;
        len -= take;
        cur_off = 0;
        cur_vol++;
    }
    return true;
}

PAK_NOINLINE uint8_t *pak_read_whole(const char *name, uint16_t *size_out)
{
    uint8_t vol, flags;
    uint16_t off;
    size_t total;
    if (pak_stat(name, &vol, &off, &total, &flags)) {
        if (total > 0xFFFFu) {
            return NULL;
        }
        uint8_t *buf = malloc(total ? total : 1u);
        if (!buf) {
            return NULL;
        }
        if (total && !pak_read_span(vol, off, 0, buf, total)) {
            free(buf);
            return NULL;
        }
        if (size_out) {
            *size_out = (uint16_t)total;
        }
        return buf;
    }

    uint8_t h = ti_Open(name, "r");
    if (!h) {
        return NULL;
    }
    uint16_t sz = ti_GetSize(h);
    uint8_t *buf = malloc(sz ? sz : 1u);
    if (!buf) {
        ti_Close(h);
        return NULL;
    }
    size_t got = sz ? ti_Read(buf, 1, sz, h) : 0;
    ti_Close(h);
    if (got != sz) {
        free(buf);
        return NULL;
    }
    if (size_out) {
        *size_out = sz;
    }
    return buf;
}

PAK_NOINLINE bool pak_read_asset(const char *name, size_t sub_off, void *dst, size_t len)
{
    uint8_t vol, flags;
    uint16_t off;
    size_t total;
    if (pak_stat(name, &vol, &off, &total, &flags)) {
        if (sub_off > total || len > total - sub_off) {
            return false;
        }
        return pak_read_span(vol, off, sub_off, dst, len);
    }

    uint8_t h = ti_Open(name, "r");
    if (!h) {
        return false;
    }
    size_t sz = ti_GetSize(h);
    if (sub_off > sz || len > sz - sub_off) {
        ti_Close(h);
        return false;
    }
    if (len) {
        memcpy(dst, (const uint8_t *)ti_GetDataPtr(h) + sub_off, len);
    }
    ti_Close(h);
    return true;
}

PAK_NOINLINE bool pak_view_open(const char *name, pak_view_t *out)
{
    uint8_t vol, flags;
    uint16_t off;
    size_t total;
    if (pak_stat(name, &vol, &off, &total, &flags)) {
        if (flags & PAK_FLAG_SPANNING) {
            return false;
        }
        uint8_t h = open_dpk_vol(vol);
        if (!h) {
            return false;
        }
        if ((size_t)off + total > (size_t)ti_GetSize(h)) {
            ti_Close(h);
            return false;
        }
        out->handle = h;
        out->ptr    = (const uint8_t *)ti_GetDataPtr(h) + off;
        out->size   = total;
        return true;
    }

    uint8_t h = ti_Open(name, "r");
    if (!h) {
        return false;
    }
    out->handle = h;
    out->ptr    = (const uint8_t *)ti_GetDataPtr(h);
    out->size   = ti_GetSize(h);
    return true;
}

PAK_NOINLINE void pak_view_close(pak_view_t *v)
{
    if (v && v->handle) {
        ti_Close(v->handle);
        v->handle = 0;
        v->ptr    = NULL;
        v->size   = 0;
    }
}

PAK_NOINLINE uint8_t pak_open_vol(const char *legacy_prefix, uint8_t vol)
{
    if (idx_buf) {
        return open_dpk_vol(vol);
    }
    char name[9];
    size_t n = strlen(legacy_prefix);
    if (n > 5u) n = 5u;
    memcpy(name, legacy_prefix, n);
    append_u8(name + n, vol);
    return ti_Open(name, "r");
}

#endif
