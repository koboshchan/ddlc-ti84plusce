#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <fileioc.h>
#include <graphx.h>

#include "ch5_bands.h"
#include "ch5_motion.h"
#include "ch5_motion_fast.h"
#include "perf_memory.h"

extern void render_apply_palette(const uint16_t *);

void ch5_motion_quant_solid1(uint8_t *, const uint8_t *, unsigned, const uint8_t *, const uint8_t *);
void ch5_motion_quant_solid2(uint8_t *, const uint8_t *, unsigned, const uint8_t *, const uint8_t *);
void ch5_motion_opaque_solid2(uint8_t *, const uint8_t *, unsigned);
void ch5_motion_quant_direct1(uint8_t *, const uint8_t *, unsigned, const uint8_t *, const uint8_t *);
void ch5_motion_quant_direct2(uint8_t *, const uint8_t *, unsigned, const uint8_t *, const uint8_t *);
void ch5_motion_opaque_direct1(uint8_t *, const uint8_t *, unsigned);
void ch5_motion_opaque_direct2(uint8_t *, const uint8_t *, unsigned);
void ch5_motion_quant_fast(uint8_t *, const uint8_t *, const int16_t *, unsigned, const uint8_t *, const uint8_t *);
void ch5_fused_bg_0(uint8_t *, const uint8_t *, const uint8_t *, const uint8_t *, const uint8_t *);
void ch5_fused_bg_1(uint8_t *, const uint8_t *, const uint8_t *, const uint8_t *, const uint8_t *);
void ch5_fused_bg_2(uint8_t *, const uint8_t *, const uint8_t *, const uint8_t *, const uint8_t *);
void ch5_fused_bg_3(uint8_t *, const uint8_t *, const uint8_t *, const uint8_t *, const uint8_t *);

extern uint8_t *resident;

static uint8_t last_scene = 255;
static clock_t started;
static clock_t layer_start[8];
static uint8_t previous_mode[8] = {255, 255, 255, 255, 255, 255, 255, 255};

static __attribute__((noinline)) unsigned ch5_ticks_to_ms(clock_t dt) {
    uint32_t ticks = (uint32_t)dt;
#ifdef __TICE__
    _Static_assert(CLOCKS_PER_SEC == 32768, "32-bit shift requires 32768Hz clock");
    return (unsigned)(((ticks >> 15) * 1000UL) + (((ticks & 0x7FFFUL) * 125UL) >> 12));
#else
    return (unsigned)(((uint64_t)ticks * 1000ULL) / CLOCKS_PER_SEC);
#endif
}

/* Zoomed motion modes are exactly 1x/2x, so the million-unit scale
 * cancels before flooring. General authored zooms delegate to ch5_axis_map. */
static void motion_axis_map(int16_t *map, unsigned count, int32_t origin, uint32_t scale, unsigned extent) {
    if (scale == 1000000u || scale == 2000000u) {
        uint8_t shift = scale == 1000000u ? 10 : 11;
        int32_t start = (-origin) >> shift;
        uint8_t parity = ((uint32_t)(-origin) >> 10) & 1u;
        unsigned factor = scale == 1000000u ? 1 : 2;
        if (factor == 1) parity = 0;
        int32_t lo = -start * factor - parity;
        int32_t hi = ((int32_t)extent - start) * factor - parity;
        unsigned begin = lo < 0 ? 0 : lo > (int32_t)count ? count : (unsigned)lo;
        unsigned end = hi < 0 ? 0 : hi > (int32_t)count ? count : (unsigned)hi;
        memset(map, 255, count * sizeof *map);
        if (begin < end) {
            int source = (int)(start + (factor == 1 ? begin : ((begin + parity) >> 1)));
            if (factor == 1) {
                for (unsigned j = begin; j < end; j++) map[j] = (int16_t)source++;
            } else {
                for (unsigned j = begin; j < end; j++) {
                    map[j] = (int16_t)source;
                    if ((j + parity) & 1u) source++;
                }
            }
        }
        return;
    }
    ch5_axis_map(map, count, origin, scale, extent);
}

static __attribute__((noinline)) void build_quant_pages(uint8_t *table, uint8_t *pages, const uint8_t *qdata,
                              const uint8_t *quant_groups, uint16_t alpha_q10) {
    uint8_t base = (uint8_t)((uintptr_t)table >> 8);
    uint8_t pg = 0;
    do {
        pages[pg] = (uint8_t)(base + quant_groups[pg]);
    } while (++pg);
    memcpy(table, qdata + 272 + (alpha_q10 < 352 ? 0 : alpha_q10 < 544 ? 4096 : 8192), 4096);
}

static __attribute__((noinline)) bool draw_fused_bg(uint8_t *table, uint8_t *pages,
                          int16_t *pair_x, int16_t *pair_y,
                          int16_t *xmap, int16_t *ymap,
                          const uint8_t *record, const uint8_t *qdata,
                          const uint8_t *quant_groups, uint8_t *first_layer,
                          bool solid_bg0_window) {
    uint8_t bg_idx = *first_layer;
    unsigned ms0 = ch5_ticks_to_ms(clock() - layer_start[bg_idx]);
    unsigned ms1 = ch5_ticks_to_ms(clock() - layer_start[bg_idx + 1]);
    ch5_motion_t a = ch5_motion(ms0, record[3 + bg_idx * 2], 1280, 720);
    ch5_motion_t b = ch5_motion(ms1, record[3 + (bg_idx + 1) * 2], 1280, 720);
    bool eligible = a.alpha_q10 == 1024 && b.alpha_q10 > 0 && b.alpha_q10 < 1024 &&
                    a.zoom_q10 == 2000000u && b.zoom_q10 == 2000000u;
    if (!eligible) return true;

    motion_axis_map(pair_x, 320, a.x, a.zoom_q10, 320);
    motion_axis_map(pair_y, 180, a.y, a.zoom_q10, 180);
    motion_axis_map(xmap, 320, b.x, b.zoom_q10, 320);
    motion_axis_map(ymap, 180, b.y, b.zoom_q10, 180);
    for (unsigned k = 0; k < 320; k++) {
        if (pair_x[k] < 0 || xmap[k] < 0) return true;
    }
    for (unsigned k = 0; k < 180; k++) {
        if (pair_y[k] < 0 || ymap[k] < 0) return true;
    }

    int16_t x0_min = pair_x[0], x0_max = pair_x[319];
    int16_t x1_min = xmap[0], x1_max = xmap[319];
    int16_t y0_min = pair_y[0], y0_max = pair_y[179];
    int16_t y1_min = ymap[0], y1_max = ymap[179];
    unsigned w0 = (unsigned)(x0_max - x0_min + 1);
    unsigned w1 = (unsigned)(x1_max - x1_min + 1);
    unsigned h0 = (unsigned)(y0_max - y0_min + 1);
    unsigned h1 = (unsigned)(y1_max - y1_min + 1);

    /* Pack bg0 & bg1 active crops inside resident (30720 B) when safe so neither
     * DMOT0 nor DMOT1 band cache thrashes or double-decodes between preflight and draw.
     * y0_min >= 25 guarantees packed dst ends strictly before resident source row offset. */
    bool packed = resident != NULL &&
                  y0_min >= 25 && y0_max < 120 && y0_min <= y0_max &&
                  y1_min <= y1_max &&
                  (size_t)h0 * w0 + (size_t)h1 * w1 <= 30720u;
    bool skip_r0_scan = solid_bg0_window &&
                        y0_min >= 26 && y0_max <= 116 &&
                        x0_min >= 31 && x0_max <= 191;

    if (packed) {
        for (int16_t r0 = y0_min; r0 <= y0_max; r0++) {
            const uint8_t *src = ch5_band_row(0, (uint16_t)r0);
            if (!src) {
                ch5_band_invalidate();
                return false;
            }
            if (!skip_r0_scan && memchr(src + x0_min, 255, w0)) {
                ch5_band_invalidate();
                return true;
            }
            memmove(resident + (size_t)(r0 - y0_min) * w0, src + x0_min, w0);
        }
        uint8_t *dst1 = resident + (size_t)h0 * w0;
        for (int16_t r1 = y1_min; r1 <= y1_max; r1++) {
            const uint8_t *src = ch5_band_row(1, (uint16_t)r1);
            if (!src) {
                ch5_band_invalidate();
                return false;
            }
            if (memchr(src + x1_min, 255, w1)) {
                ch5_band_invalidate();
                return true;
            }
            memcpy(dst1 + (size_t)(r1 - y1_min) * w1, src + x1_min, w1);
        }
        ch5_band_invalidate();
    } else {
        /* Preflight both source windows. No partial fusion before fallback. */
        for (unsigned k = 0; k < 180; k++) {
            if (k && pair_y[k] == pair_y[k - 1] && ymap[k] == ymap[k - 1]) continue;
            const uint8_t *r0 = ch5_band_row(0, pair_y[k]);
            const uint8_t *r1 = ch5_band_row(1, ymap[k]);
            if (!r0 || !r1) return false;
            if ((!skip_r0_scan && memchr(r0 + x0_min, 255, w0)) ||
                memchr(r1 + x1_min, 255, w1)) {
                return true;
            }
        }
    }

    build_quant_pages(table, pages, qdata, quant_groups, b.alpha_q10);
    unsigned phase = (unsigned)(pair_x[1] != pair_x[0]) | ((unsigned)(xmap[1] != xmap[0]) << 1);
    void (*kernel)(uint8_t *, const uint8_t *, const uint8_t *, const uint8_t *, const uint8_t *) =
        phase == 0 ? ch5_fused_bg_0 :
        phase == 1 ? ch5_fused_bg_1 :
        phase == 2 ? ch5_fused_bg_2 : ch5_fused_bg_3;
    const uint8_t *base1 = resident + (size_t)h0 * w0;
    for (unsigned k = 0; k < 180; k++) {
        uint8_t *dst = (uint8_t *)gfx_vbuffer + k * 320;
        if (k && pair_y[k] == pair_y[k - 1] && ymap[k] == ymap[k - 1]) {
            memcpy(dst, dst - 320, 320);
            continue;
        }
        const uint8_t *r0;
        const uint8_t *r1;
        if (packed) {
            r0 = resident + (size_t)(pair_y[k] - y0_min) * w0;
            r1 = base1 + (size_t)(ymap[k] - y1_min) * w1;
        } else {
            const uint8_t *p0 = ch5_band_row(0, pair_y[k]);
            const uint8_t *p1 = ch5_band_row(1, ymap[k]);
            if (!p0 || !p1) return false;
            r0 = p0 + x0_min;
            r1 = p1 + x1_min;
        }
        kernel(dst, r0, r1, pages, table);
    }
    *first_layer = (uint8_t)(bg_idx + 2);
    return true;
}

static __attribute__((noinline)) bool draw_motion_layer(uint8_t layer, uint8_t mode, uint8_t i_slot,
                              uint8_t *ramlut, uint8_t *pages,
                              int16_t *xmap, int16_t *ymap,
                              const uint8_t *qdata, const uint8_t *quant_groups,
                              uint8_t fg_cache_layer, uint8_t *fg_row_bits) {
    unsigned tw = layer < 2 ? 320 : 72;
    unsigned th = layer < 2 ? 180 : 263;
    unsigned layer_ms = ch5_ticks_to_ms(clock() - layer_start[i_slot]);
    ch5_motion_t pose = ch5_motion(layer_ms, mode, layer < 2 ? 1280 : 286, layer < 2 ? 720 : 1050);
    if (!pose.alpha_q10) return true;

    motion_axis_map(xmap, 320, pose.x, pose.zoom_q10, tw);
    motion_axis_map(ymap, 180, pose.y, pose.zoom_q10, th);
    const bool opaque = pose.alpha_q10 == 1024;
    if (!opaque) build_quant_pages(ramlut, pages, qdata, quant_groups, pose.alpha_q10);

    unsigned first = 0, end = 320;
    while (first < end && xmap[first] < 0) first++;
    while (end > first && xmap[end - 1] < 0) end--;
    unsigned span = end - first;
    if (!span) return true;

    bool linear = true, doubled = true, odd_doubled = true;
    if (pose.zoom_q10 == 1000000u) {
        doubled = false;
        odd_doubled = false;
    } else if (pose.zoom_q10 == 2000000u) {
        linear = false;
        doubled = xmap[first + (span > 1)] == xmap[first];
        odd_doubled = !doubled;
    } else {
        int16_t dx = xmap[first + span - 1] - xmap[first];
        if (dx != (int16_t)(span - 1)) linear = false;
        if (dx != (int16_t)((span - 1) / 2)) doubled = false;
        if (dx != (int16_t)(span / 2)) odd_doubled = false;
        for (unsigned k = 0; k < span && (linear || doubled || odd_doubled); k++) {
            if (linear && xmap[first + k] != xmap[first] + (int16_t)k) linear = false;
            if (doubled && xmap[first + k] != xmap[first] + (int16_t)(k / 2)) doubled = false;
            if (odd_doubled && xmap[first + k] != xmap[first] + (int16_t)((k + 1) / 2)) odd_doubled = false;
        }
    }

    int16_t prior_source = -1;
    int16_t prior_drawn_source = -1;
    const uint8_t *prior_row = NULL;
    bool prior_solid = false;
    for (uint8_t y = 0; y < 180; y++) {
        if (ymap[y] < 0) {
            prior_drawn_source = -1;
            continue;
        }
        const uint8_t *row;
        if (ymap[y] == prior_source) {
            row = prior_row;
        } else if (layer == fg_cache_layer &&
                   ((fg_row_bits[(unsigned)ymap[y] >> 3] >> ((unsigned)ymap[y] & 7u)) & 1u)) {
            row = resident + (size_t)ymap[y] * 72u;
        } else {
            row = ch5_band_row(layer, ymap[y]);
            if (!row) return false;
            if (layer == fg_cache_layer) {
                memcpy(resident + (size_t)ymap[y] * 72u, row, 72u);
                fg_row_bits[(unsigned)ymap[y] >> 3] |= (uint8_t)(1u << ((unsigned)ymap[y] & 7u));
            }
        }
        if (ymap[y] != prior_source) prior_solid = layer < 2 && !memchr(row,255,tw);
        prior_source = ymap[y];
        prior_row = row;
        uint8_t *dst = (uint8_t *)gfx_vbuffer + y * 320;
        /* Never assume packaged backgrounds are opaque. Scan the decoded row;
         * unusual transparent assets retain the original guarded kernels. */
        if (opaque && prior_solid) {
            if (y > 0 && ymap[y] == prior_drawn_source) {
                memcpy(dst + first, dst - 320 + first, span);
                continue;
            }
            if (linear) {
                memcpy(dst + first, row + xmap[first], span);
                prior_drawn_source = ymap[y];
                continue;
            }
        }
        void (*copy1)(uint8_t *, const uint8_t *, unsigned) = ch5_motion_opaque_direct1;
        void (*copy2)(uint8_t *, const uint8_t *, unsigned) =
            prior_solid ? ch5_motion_opaque_solid2 : ch5_motion_opaque_direct2;
        void (*blend1)(uint8_t *, const uint8_t *, unsigned, const uint8_t *, const uint8_t *) =
            prior_solid ? ch5_motion_quant_solid1 : ch5_motion_quant_direct1;
        void (*blend2)(uint8_t *, const uint8_t *, unsigned, const uint8_t *, const uint8_t *) =
            prior_solid ? ch5_motion_quant_solid2 : ch5_motion_quant_direct2;
        if (linear) {
            if (opaque) copy1(dst + first, row + xmap[first], span);
            else blend1(dst + first, row + xmap[first], span, pages, ramlut);
        } else if ((odd_doubled && !doubled && span >= 3) || (doubled && span >= 2)) {
            unsigned edge = odd_doubled && !doubled, remaining = span - edge, even = remaining & ~1u;
            if (edge) {
                if (opaque) {
                    if (prior_solid) dst[first] = row[xmap[first]];
                    else copy1(dst + first, row + xmap[first], 1);
                } else {
                    blend1(dst + first, row + xmap[first], 1, pages, ramlut);
                }
            }
            if (opaque) copy2(dst + first + edge, row + xmap[first + edge], even / 2);
            else blend2(dst + first + edge, row + xmap[first + edge], even / 2, pages, ramlut);
            if (remaining != even) {
                unsigned last = first + edge + even;
                if (opaque) {
                    if (prior_solid) dst[last] = row[xmap[last]];
                    else copy1(dst + last, row + xmap[last], 1);
                } else {
                    blend1(dst + last, row + xmap[last], 1, pages, ramlut);
                }
            }
        } else {
            if (opaque) ch5_motion_opaque_fast(dst + first, row, xmap + first, span);
            else ch5_motion_quant_fast(dst + first, row, xmap + first, span, pages, ramlut);
        }
        prior_drawn_source = (opaque && prior_solid) ? ymap[y] : -1;
    }
    return true;
}

bool ch5_motion_active(uint8_t scene) {
#ifdef __TICE__
    static uint8_t cached;
    static uint8_t scene_bits[32];
    if (!cached) {
        uint8_t h = ti_Open("DMOTMAP", "r");
        if (h) {
            const uint8_t *p = ti_GetDataPtr(h);
            unsigned n = ti_GetSize(h);
            for (unsigned i = 0; i + 18 <= n; i += 18) {
                uint8_t s = p[i];
                scene_bits[s >> 3] |= (uint8_t)(1u << (s & 7));
            }
            ti_Close(h);
        }
        cached = 1;
    }
    return (scene_bits[scene >> 3] >> (scene & 7)) & 1u;
#else
    char name[] = "DMOTMAP";
    uint8_t h = ti_Open(name, "r");
    bool active = false;
    if (h) {
        const uint8_t *p = ti_GetDataPtr(h);
        unsigned n = ti_GetSize(h), i;
        for (i = 0; i + 18 <= n; i += 18) {
            if (p[i] == scene) { active = true; break; }
        }
        ti_Close(h);
    }
    return active;
#endif
}

/* Clock lifetime is independent of framebuffer drawing. A successful USB
 * frame must advance the same scene/layer clocks as the on-calc renderer. */
bool ch5_motion_sync_clock(uint8_t scene) {
    uint8_t h = ti_Open("DMOTMAP", "r");
    if (!h) return false;
    const uint8_t *p = ti_GetDataPtr(h);
    unsigned n = ti_GetSize(h), i, j;
    clock_t now = clock();
    bool found = false;
    for (i = 0; i + 18 <= n; i += 18) {
        if (p[i] == scene) {
            if (last_scene != scene) {
                last_scene = scene;
                started = now;
                ch5_band_invalidate();
            }
            for (j = 0; j < p[i + 1] && j < 8; j++) {
                uint8_t mode = p[i + 3 + j * 2];
                if (previous_mode[j] != mode) {
                    previous_mode[j] = mode;
                    layer_start[j] = now;
                }
            }
            found = true;
            break;
        }
    }
    ti_Close(h);
    return found;
}

bool ch5_motion_draw(uint8_t scene) {
    uint8_t record[18], h, blend_handle;
    unsigned n, x;
    uint8_t i;
    bool found = false;

    h = ti_Open("DMOTMAP", "r");
    if (!h) return false;
    const uint8_t *p = ti_GetDataPtr(h);
    n = ti_GetSize(h);
    for (x = 0; x + 18 <= n; x += 18) {
        if (p[x] == scene) { memcpy(record, p + x, 18); found = true; break; }
    }
    ti_Close(h);
    if (!found) { last_scene = 255; return false; }
    if (record[1] > 8) return false;

    h = ti_Open("DMOTPAL", "r");
    if (!h) return false;
    n = ti_GetSize(h);
    ti_Close(h);
    if (n != 512) return false;
    if (!ch5_motion_sync_clock(scene)) return false;

    blend_handle = ti_Open("DMOTRGB", "r");
    if (!blend_handle) return false;
    if (ti_GetSize(blend_handle) != 32768u) { ti_Close(blend_handle); return false; }

    /* DMOTRGB is validated above; no unused data-pointer lookup. */
    uint8_t qh = ti_Open("DLUTQ", "r");
    if (!qh) { ti_Close(blend_handle); return false; }
    if (ti_GetSize(qh) != 12560) { ti_Close(qh); ti_Close(blend_handle); return false; }
    const uint8_t *qdata = ti_GetDataPtr(qh);
    const uint8_t *quant_groups = qdata;
    {
        uint8_t q = 0;
        do {
            if (quant_groups[q] >= 16) {
                ti_Close(qh);
                ti_Close(blend_handle);
                return false;
            }
        } while (++q);
    }
    ti_Close(qh);

#ifdef __TICE__
    /* Pack LUT (4KB-aligned), pages (256B), band scratch (6272B on-calc), and
     * axis maps (2000B) inside rows 180..239 of gfx_vbuffer (19,200B free):
     * max alignment pad 4095 + 12,624 = 16,719B <= 19,200B.
     * Optional 30KB band-0 cache falls back gracefully if heap is low. */
    uint8_t *scratch = (uint8_t *)gfx_vbuffer + 320u * 180u;
    uint8_t *table = (uint8_t *)(((uintptr_t)scratch + 4095u) & ~(uintptr_t)4095u);
    uint8_t *pages = table + 4096;
    ch5_band_scratch = pages + 256;
    int16_t *pair_x = (int16_t *)(pages + 256 + 6272);
    int16_t *xmap = pair_x + 320;
    int16_t *pair_y = xmap + 320;
    int16_t *ymap = pair_y + 180;
    uint8_t *lookup_alloc = NULL;
#else
    /* Host adversarial tests assert gfx_vbuffer[57600..] stays untouched. */
    uint8_t *lookup_alloc = malloc(4096 + 256 + 4095 + 2000);
    if (!lookup_alloc) { ti_Close(blend_handle); return false; }
    uint8_t *table = (uint8_t *)(((uintptr_t)lookup_alloc + 4095u) & ~(uintptr_t)4095u);
    uint8_t *pages = table + 4096;
    int16_t *pair_x = (int16_t *)(pages + 256);
    int16_t *xmap = pair_x + 320;
    int16_t *pair_y = xmap + 320;
    int16_t *ymap = pair_y + 180;
#endif

    bool success = false;
    resident = malloc(30720);
    ch5_band_begin();

    /* Validate each distinct stream before modifying frame. Existing scene is fallback. */
    uint8_t seen_layers = 0;
    for (i = 0; i < record[1]; i++) {
        uint8_t l = record[2 + i * 2];
        if (l < 8) {
            uint8_t bit = (uint8_t)(1u << l);
            if (seen_layers & bit) continue;
            seen_layers |= bit;
        }
        if (!ch5_band_row(l, 0)) goto motion_done;
    }

    uint8_t first_layer = 0;
    bool solid_bg_skip = false;
    int bg_idx = -1;
    if (record[1] > 0 && record[2] == 0 && record[3] == 3) {
        bg_idx = 0;
    } else if (record[1] > 1 && record[4] == 0 && record[5] == 3) {
        bg_idx = 1;
    }
    /* Authored bg_zoom has 2x coverage for every permitted phase. Never
     * skip for other modes or assets containing transparent pixels. */
    if (bg_idx >= 0) {
        bool solid = true;
        for (uint8_t row = 26; row <= 116; row++) {
            const uint8_t *pixels = ch5_band_row(0, row);
            if (!pixels) goto motion_done;
            if (memchr(pixels + 31, 255, 161)) { solid = false; break; }
        }
        if (solid) {
            first_layer = (uint8_t)bg_idx;
            solid_bg_skip = true;
        }
    }

    /* Proven full-screen opaque mode-3 coverage overwrites every pixel. */
    if (!solid_bg_skip) memset(gfx_vbuffer, 0, 320 * 180);

    /* Opt-in only for the actual adjacent background pair. Every other path
     * retains the existing compositor. Maps and transparency proved before writes. */
    if (solid_bg_skip && record[1] >= first_layer + 2 &&
        record[2 + first_layer * 2] == 0 && record[2 + (first_layer + 1) * 2] == 1) {
        if (!draw_fused_bg(table, pages, pair_x, pair_y, xmap, ymap,
                           record, qdata, quant_groups, &first_layer, solid_bg_skip)) {
            goto motion_done;
        }
    }

    uint8_t fg_cache_layer = 255;
    uint8_t fg_row_bits[33];
    if (resident != NULL) {
        bool has_layer0 = false;
        uint8_t fg_seen = 0;
        for (i = first_layer; i < record[1]; i++) {
            uint8_t l = record[2 + i * 2];
            if (l == 0) has_layer0 = true;
            else if (l >= 2 && l < 8) {
                uint8_t bit = (uint8_t)(1u << l);
                if (fg_seen & bit) {
                    if (fg_cache_layer == 255) fg_cache_layer = l;
                } else {
                    fg_seen |= bit;
                }
            }
        }
        if (has_layer0) fg_cache_layer = 255;
        if (fg_cache_layer != 255) {
            ch5_band_invalidate();
            memset(fg_row_bits, 0, sizeof(fg_row_bits));
        }
    }

    for (i = first_layer; i < record[1]; i++) {
        if (!draw_motion_layer(record[2 + i * 2], record[3 + i * 2], i,
                               table, pages, xmap, ymap, qdata, quant_groups,
                               fg_cache_layer, fg_row_bits)) {
            goto motion_done;
        }
    }

    success = true;
motion_done:
    ch5_band_end();
    free(resident);
    resident = NULL;
    free(lookup_alloc);
#ifdef __TICE__
    ch5_band_scratch = NULL;
#endif
    ti_Close(blend_handle);
    if (success) {
        h = ti_Open("DMOTPAL", "r");
        if (h) {
            if (ti_GetSize(h) == 512) {
                /* Publish palette and fade state only after dependency/stream success. */
                render_apply_palette((const uint16_t *)ti_GetDataPtr(h));
            } else {
                success = false;
            }
            ti_Close(h);
        } else {
            success = false;
        }
    }
    /* Bottom rows are transient work space, not scene pixels. Every caller
     * must compose the dialogue/letterbox after this draw and before present. */
    return success;
}

uint32_t ch5_motion_elapsed_ms(uint8_t scene) {
    if (last_scene != scene) return 0;
    return (uint32_t)ch5_ticks_to_ms(clock() - started);
}
