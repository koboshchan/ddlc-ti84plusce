/* Debug-only telemetry. Dev menu toggles; off at startup. Timer 3 is borrowed
 * ONLY if already disabled. Never touches CPU interrupt state or shadow regs.
 * Both Page 1 (memory, frame-ms ring, per-stage breakdown) and Page 2 (script,
 * scene, asset, and DPK inspector) share a 128x65 box (8320 B) that fits inside
 * TI-OS pixelShadow (0xD031F6, 8400 B strictly below BSSHEAP_LOW) for atomic
 * off-screen staging with zero BSS/heap cost. Toggle pages with GRAPH. */

#include "perf_overlay.h"

#ifdef DDLC_PERF_OVERLAY

#include "perf_overlay_history.h"
#include "render.h"
#define DDLC_PERF_MEMORY_IMPL
#include "perf_memory.h"

#include <graphx.h>
#include <sys/timers.h>
#include <sys/lcd.h>
#include <ti/vars.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#ifdef __TICE__
#include "text.h"
#define ov_vsnprintf mini_vsnprintf
#else
#include <stdio.h>
#define ov_vsnprintf vsnprintf
#endif

extern unsigned char __bss_low[], __bss_high[];

static perf_history_t history;
static bool enabled;
static bool owns_timer;
static bool measuring;
static bool rendered;
static uint8_t ov_page;
static bool page_dirty;
static uint16_t saved_control;
static uint32_t saved_count;
static uint32_t started;

#define TIMER3_BITS ((7u << 6) | (1u << 11))

/* Per-stage profiler ticks (Timer 3 @ 32768 Hz, native 24-bit unsigned on
 * eZ80 -> 512s wrap, avoiding 32-bit library helpers).
 * cur_stages accumulates during the active frame; sc_stages retains the most
 * recent frame that executed BG or actor work so scene redraw costs remain
 * visible even after subsequent typewriter frames settle. */
static unsigned stage_start[PERF_STAGE_COUNT];
static unsigned cur_stages[PERF_STAGE_COUNT];
static unsigned disp_stages[PERF_STAGE_COUNT];
static unsigned sc_stages[PERF_STAGE_COUNT];
static bool sc_pending_vram;
static const char *sc_mode = "INIT";

/* Page 2 asset/memory telemetry (debug build only) */
#define PERF_MAX_SPR_LOG 4u

typedef struct {
    uint8_t   slot;
    uint8_t   is_face;
    uint16_t  spr_id;
    uint8_t   vol;
    uint16_t  off;
    uint16_t  len;
    uintptr_t addr;
} perf_spr_log_t;

static uint8_t  dbg_chunk_id;
static uint8_t  dbg_bg_id;
static uint8_t  dbg_pal_kind;
static uint8_t  dbg_pal_id;
static uint16_t dbg_pc_off;
static uint8_t  dbg_bg_vol;
static uint16_t dbg_bg_off;
static uint16_t dbg_bg_len;
static uintptr_t dbg_bg_addr;
static bool     dbg_bg_ram;
static perf_spr_log_t dbg_sprs[PERF_MAX_SPR_LOG];
static uint8_t  dbg_spr_count;

/* Unified 128x65 box = 8320 B (fits inside 8400 B pixelShadow). */
enum {
    OV_X = 188,
    OV_Y = 3,
    OV_W = 128,
    OV_H = 65
};

#ifdef __TICE__
#define PERF_BG_SAVE ((uint8_t *)0xD031F6)
#else
static uint8_t perf_bg_save_host[8400];
#define PERF_BG_SAVE perf_bg_save_host
#endif

static bool bg_saved;
static uint8_t last_paper = COL_BLACK;
static uint8_t ink = COL_WHITE;
static uint8_t *ov_fb;

static __attribute__((noinline)) unsigned stage_ticks_ms(unsigned ticks)
{
    return ((ticks >> 15) * 1000u) +
           (((ticks & 0x7FFFu) * 125u + 2048u) >> 12);
}

void perf_overlay_set_mode(const char *mode)
{
    if (mode != NULL) {
        sc_mode = mode;
    }
}

void perf_overlay_set_vm(uint8_t chunk_id, uint16_t pc_off, uint8_t bg_id)
{
    dbg_chunk_id = chunk_id;
    dbg_pc_off = pc_off;
    dbg_bg_id = bg_id;
}

void perf_overlay_set_pal(uint8_t pal_kind, uint8_t pal_id)
{
    dbg_pal_kind = pal_kind;
    dbg_pal_id = pal_id;
}

void perf_overlay_set_bg(uint8_t bg_id, uint8_t vol, uint16_t off, uint16_t len,
                         uintptr_t addr, bool is_ram)
{
    dbg_bg_id = bg_id;
    dbg_bg_vol = vol;
    dbg_bg_off = off;
    dbg_bg_len = len;
    dbg_bg_addr = addr;
    dbg_bg_ram = is_ram;
}

void perf_overlay_clear_sprites(void)
{
    dbg_spr_count = 0;
}

void perf_overlay_record_sprite(uint8_t slot, bool is_face, uint16_t spr_id,
                                uint8_t vol, uint16_t off, uint16_t len,
                                uintptr_t addr)
{
    if (dbg_spr_count >= PERF_MAX_SPR_LOG) {
        return;
    }
    perf_spr_log_t *e = &dbg_sprs[dbg_spr_count++];
    e->slot = slot;
    e->is_face = is_face ? 1u : 0u;
    e->spr_id = spr_id;
    e->vol = vol;
    e->off = off;
    e->len = len;
    e->addr = addr;
}

void perf_overlay_shutdown(void)
{
    measuring = false;
    if (owns_timer) {
        timer_Disable(3);
        timer_Set(3, saved_count);
        timer_Control = (timer_Control & ~TIMER3_BITS) | saved_control;
        owns_timer = false;
    }
    enabled = false;
}

bool perf_overlay_enabled(void)
{
    return enabled;
}

void perf_overlay_toggle_page(void)
{
    ov_page ^= 1u;
    page_dirty = true;
}

bool perf_overlay_page_dirty(void)
{
    bool d = page_dirty;
    page_dirty = false;
    return d;
}

void perf_overlay_toggle(void)
{
    if (enabled) {
        perf_overlay_shutdown();
        return;
    }

    enabled = true;
    ov_page = 0;
    page_dirty = true;
    memset(&history, 0, sizeof(history));
    memset(cur_stages, 0, sizeof(cur_stages));
    memset(disp_stages, 0, sizeof(disp_stages));
    memset(sc_stages, 0, sizeof(sc_stages));
    sc_pending_vram = false;
    if (!(timer_Control & (1u << 6))) {
        saved_control = timer_Control & TIMER3_BITS;
        saved_count = timer_Get(3);
        timer_Set(3, 0);
        timer_Enable(3, TIMER_32K, TIMER_NOINT, TIMER_UP);
        owns_timer = true;
    }
}

static void reset_cur_stages(void)
{
    for (uint8_t i = 0; i < PERF_STAGE_VRAM; ++i) {
        cur_stages[i] = 0;
    }
}

void perf_overlay_begin(void)
{
    rendered = false;
    measuring = enabled && owns_timer;
    if (measuring) {
        reset_cur_stages();
        started = timer_Get(3);
    }
}

void perf_overlay_frame_start(void)
{
    if (enabled && owns_timer && !measuring) {
        measuring = true;
        rendered = true;
        reset_cur_stages();
        started = timer_Get(3);
    }
}

void perf_overlay_mark_rendered(void)
{
    if (measuring) {
        rendered = true;
    }
}

void perf_overlay_stage_begin(uint8_t stage)
{
    if (enabled && owns_timer && stage < PERF_STAGE_COUNT) {
        stage_start[stage] = (unsigned)timer_Get(3);
    }
}

void perf_overlay_stage_end(uint8_t stage)
{
    if (enabled && owns_timer && stage < PERF_STAGE_COUNT) {
        cur_stages[stage] += (unsigned)timer_Get(3) - stage_start[stage];
    }
}

void perf_overlay_end(void)
{
    if (!measuring) {
        return;
    }
    uint32_t elapsed = timer_Get(3) - started;
    measuring = false;
    if (!rendered) {
        return;
    }
    perf_history_push(&history, elapsed);

    unsigned scene_work = cur_stages[PERF_STAGE_BG] +
                          cur_stages[PERF_STAGE_ACT0] +
                          cur_stages[PERF_STAGE_ACT1] +
                          cur_stages[PERF_STAGE_ACT2] +
                          cur_stages[PERF_STAGE_ACT3];
    memcpy(disp_stages, cur_stages, sizeof(unsigned) * PERF_STAGE_VRAM);
    if (scene_work > 0) {
        memcpy(sc_stages, cur_stages, sizeof(unsigned) * PERF_STAGE_VRAM);
        sc_pending_vram = true;
    }
}

/* 3x5 glyphs in 15 bits, row-major. No fontlib/graphx text state mutations. */
static uint16_t glyph(char c)
{
    static const uint16_t digits[10] = {
        31599, 11415, 29671, 29647, 23497,
        31183, 31215, 29257, 31727, 31695
    };
    static const uint16_t letters[26] = {
        11245, 27566, 31015, 27502, 31143, 31140, 31087, 23533, 29847,  4682,
        23469, 18727, 24557, 24573, 31599, 27556, 31607, 27565, 31183, 29842,
        23407, 23402, 23549, 23213, 23186, 29351
    };

    if (c >= '0' && c <= '9') {
        return digits[c - '0'];
    }
    if (c >= 'A' && c <= 'Z') {
        return letters[c - 'A'];
    }
    if (c >= 'a' && c <= 'z') {
        return letters[c - 'a'];
    }
    switch (c) {
    case '+': return 1488;
    case '@': return 31663;
    case ':': return 1040;
    case '.': return 2;
    case '/': return 4772;
    case '-': return 448;
    default:  return 0;
    }
}

static __attribute__((noinline)) void text(uint8_t *fb, const char *s,
                                           unsigned x, unsigned y)
{
    uint8_t *base = fb + y * 320u + x;
    for (; *s; ++s, base += 4) {
        uint16_t bits = glyph(*s);
        if (!bits) {
            continue;
        }
        uint8_t *rowp = base;
        for (unsigned row = 0; row < 5; ++row, rowp += 320) {
            uint8_t mask = (uint8_t)((bits >> (12u - row * 3u)) & 7u);
            if (mask & 4u) {
                rowp[0] = ink;
            }
            if (mask & 2u) {
                rowp[1] = ink;
            }
            if (mask & 1u) {
                rowp[2] = ink;
            }
        }
    }
}

static __attribute__((noinline)) void ov_row(unsigned dy, const char *fmt, ...)
{
    char line[36];
    va_list ap;
    va_start(ap, fmt);
    ov_vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);
    text(ov_fb, line, OV_X + 2u, OV_Y + dy);
}

static void fill_box_rows(uint8_t *fb, unsigned y, unsigned h, uint8_t color)
{
    for (unsigned r = 0; r < h; ++r) {
        memset(fb + (y + r) * 320u + OV_X, color, OV_W);
    }
}

static unsigned probe_largest_heap(void)
{
    unsigned lo = 0;
    unsigned hi = 49152u;
    while (hi - lo > 256u) {
        unsigned mid = (lo + hi) >> 1;
        void *p = malloc(mid);
        if (p) {
            free(p);
            lo = mid;
        } else {
            hi = mid;
        }
    }
    return lo;
}

static __attribute__((noinline)) void draw_stage_rows(uint8_t *fb)
{
    ov_fb = fb;
    unsigned sc_act = sc_stages[PERF_STAGE_ACT0] +
                      sc_stages[PERF_STAGE_ACT1] +
                      sc_stages[PERF_STAGE_ACT2] +
                      sc_stages[PERF_STAGE_ACT3];
    ov_row(14u, "BG:%u AC:%u BX:%u VR:%u",
           stage_ticks_ms(sc_stages[PERF_STAGE_BG]),
           stage_ticks_ms(sc_act),
           stage_ticks_ms(sc_stages[PERF_STAGE_BOX]),
           stage_ticks_ms(sc_stages[PERF_STAGE_VRAM]));
    ov_row(20u, "A0:%u A1:%u A2:%u A3:%u",
           stage_ticks_ms(sc_stages[PERF_STAGE_ACT0]),
           stage_ticks_ms(sc_stages[PERF_STAGE_ACT1]),
           stage_ticks_ms(sc_stages[PERF_STAGE_ACT2]),
           stage_ticks_ms(sc_stages[PERF_STAGE_ACT3]));
    ov_row(26u, "CUR BX:%u VR:%u %s",
           stage_ticks_ms(disp_stages[PERF_STAGE_BOX]),
           stage_ticks_ms(disp_stages[PERF_STAGE_VRAM]),
           sc_mode);
}

void perf_overlay_vram_begin(void)
{
    perf_overlay_stage_begin(PERF_STAGE_VRAM);
}

void perf_overlay_vram_end(void)
{
    if (!enabled || !owns_timer) {
        return;
    }
    unsigned dt = (unsigned)timer_Get(3) - stage_start[PERF_STAGE_VRAM];
    disp_stages[PERF_STAGE_VRAM] = dt;
    if (sc_pending_vram) {
        sc_stages[PERF_STAGE_VRAM] = dt;
        sc_pending_vram = false;
    }
    if (ov_page == 0) {
        uint8_t old_draw = gfx_GetDraw();
        gfx_SetDrawScreen();
        uint8_t *fb = (uint8_t *)gfx_vbuffer;
        fill_box_rows(fb, OV_Y + 14u, 18u, last_paper);
        draw_stage_rows(fb);
        gfx_SetDraw(old_draw);
    }
}

static __attribute__((noinline)) void perf_overlay_render_to(uint8_t *fb)
{
    const uint16_t *pal = render_active_palette();
    unsigned darkest = ~0u;
    unsigned brightest = 0;
    uint8_t paper = COL_BLACK;
    ink = COL_WHITE;

    for (unsigned i = 1; i < 256; ++i) {
        unsigned c = pal[i];
        unsigned light = ((c >> 10) & 31u) * 3u +
                         ((c >> 5) & 31u) * 6u +
                         (c & 31u);
        if (light < darkest) {
            darkest = light;
            paper = (uint8_t)i;
        }
        if (light > brightest) {
            brightest = light;
            ink = (uint8_t)i;
        }
    }
    if (brightest == darkest) {
        paper = COL_BLACK;
        ink = COL_WHITE;
    }
    last_paper = paper;
    ov_fb = fb;

    fill_box_rows(fb, OV_Y, OV_H, paper);

    void *unused;
    unsigned os_free = (unsigned)os_MemChk(&unused);
    perf_memory_t mem = perf_memory_snapshot();

    if (ov_page == 0) {
        unsigned bss_sz = (unsigned)((uintptr_t)__bss_high - (uintptr_t)__bss_low);
        ov_row(2u, "P1 BSS:%u OS:%u", bss_sz, os_free);
        ov_row(8u, "HP:%u%s AL:%u GR:P2",
               (unsigned)mem.bytes, mem.complete ? "" : "P", mem.count);
        draw_stage_rows(fb);

        for (uint8_t age = 0; age < 5; ++age) {
            unsigned dy = 33u + (unsigned)age * 6u;
            if (age < history.count) {
                ov_row(dy, "%u %u MS",
                       (unsigned)(age + 1u),
                       (unsigned)perf_history_ms(&history, age));
            } else {
                ov_row(dy, "%u -", (unsigned)(age + 1u));
            }
        }
        return;
    }

    /* Page 2: Asset / DPK / Memory Inspector (10 compact rows, 6px step) */
    if (dbg_pal_kind == PERF_PAL_CG) {
        ov_row(2u, "P2 CH%u PC%04X SC%u PAL:CG%u",
               dbg_chunk_id, dbg_pc_off, dbg_bg_id, dbg_pal_id);
    } else {
        const char *pn = (dbg_pal_kind == PERF_PAL_TITLE) ? "TTL"
                       : (dbg_pal_kind == PERF_PAL_MOTION) ? "MOT"
                       : "GAME";
        ov_row(2u, "P2 CH%u PC%04X SC%u PAL:%s",
               dbg_chunk_id, dbg_pc_off, dbg_bg_id, pn);
    }
    ov_row(8u, "BG S%u D%u+%u %uB",
           dbg_bg_id, dbg_bg_vol, dbg_bg_off, dbg_bg_len);
    ov_row(14u, "BG@%06X %s",
           (unsigned)dbg_bg_addr, dbg_bg_ram ? "RAM" : "FLS");

    for (uint8_t i = 0; i < PERF_MAX_SPR_LOG; ++i) {
        unsigned dy = 20u + (unsigned)i * 6u;
        if (i < dbg_spr_count) {
            const perf_spr_log_t *e = &dbg_sprs[i];
            ov_row(dy, "%s%u S%u D%u+%u %u @%06X",
                   e->is_face ? "F" : "B", e->slot, e->spr_id,
                   e->vol, e->off, e->len, (unsigned)e->addr);
        } else {
            ov_row(dy, "L%u -", (unsigned)i);
        }
    }

    unsigned maxblk = probe_largest_heap();
    ov_row(44u, "OS %uB MAXB %uB", os_free, maxblk);

    unsigned arc_k = 0;
#ifdef __TICE__
    os_ArcChk();
    arc_k = (unsigned)(os_TempFreeArc >> 10);
#endif
    ov_row(50u, "ARC %uK HEAP %uB", arc_k, (unsigned)mem.bytes);
    text(fb, "GRAPH:P1/P2", OV_X + 2u, OV_Y + 56u);
}

void perf_overlay_draw(void)
{
    if (!enabled) {
        return;
    }
    gfx_Wait();
    uint8_t old_draw = gfx_GetDraw();
    gfx_SetDrawScreen();
    perf_overlay_render_to((uint8_t *)gfx_vbuffer);
    gfx_SetDraw(old_draw);
}

void perf_overlay_draw_buffer(void)
{
    if (!enabled) {
        return;
    }
    perf_overlay_render_to((uint8_t *)gfx_vbuffer);
}

static __attribute__((noinline)) void ov_blit_bg(bool save)
{
    uint8_t *fb = (uint8_t *)gfx_vbuffer;
    for (unsigned r = 0; r < OV_H; ++r) {
        uint8_t *f = fb + (OV_Y + r) * 320u + OV_X;
        uint8_t *s = PERF_BG_SAVE + r * OV_W;
        if (save) {
            memcpy(s, f, OV_W);
        } else {
            memcpy(f, s, OV_W);
        }
    }
}

void perf_overlay_present_begin(void)
{
    if (!enabled) {
        return;
    }
    ov_blit_bg(true);
    bg_saved = true;
    perf_overlay_render_to((uint8_t *)gfx_vbuffer);
}

void perf_overlay_present_end(void)
{
    if (!bg_saved) {
        return;
    }
    bg_saved = false;
    ov_blit_bg(false);
}

#endif
