/* Debug-only telemetry. Dev menu toggles; off at startup. Timer 3 is borrowed
 * ONLY if already disabled. Never touches CPU interrupt state or shadow regs.
 * Page 1: memory & frame-ms ring (104x80). Page 2: script/scene/asset/DPK
 * inspector (128x65), toggled with the GRAPH key. Both fit inside TI-OS
 * pixelShadow (0xD031F6, 8400 B strictly below BSSHEAP_LOW) for atomic
 * off-screen staging with zero BSS/heap cost. */
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
#include <stdlib.h>
#include <string.h>
#ifdef __TICE__
#include "text.h"
#else
#include <stdio.h>
#endif
#include <stddef.h>
extern unsigned char __bss_low[], __bss_high[];
static perf_history_t history;
static bool enabled, owns_timer, measuring;
static bool rendered;
static uint8_t ov_page;
static bool page_dirty;
static uint16_t saved_control;
static uint32_t saved_count, started;
#define TIMER3_BITS ((7u << 6) | (1u << 11))

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

static uint8_t  dbg_chunk_id, dbg_bg_id, dbg_pal_kind, dbg_pal_id;
static uint16_t dbg_pc_off;
static uint8_t  dbg_bg_vol;
static uint16_t dbg_bg_off, dbg_bg_len;
static uintptr_t dbg_bg_addr;
static bool     dbg_bg_ram;
static perf_spr_log_t dbg_sprs[PERF_MAX_SPR_LOG];
static uint8_t  dbg_spr_count;

void perf_overlay_set_vm(uint8_t chunk_id, uint16_t pc_off, uint8_t bg_id) {
    dbg_chunk_id = chunk_id;
    dbg_pc_off = pc_off;
    dbg_bg_id = bg_id;
}
void perf_overlay_set_pal(uint8_t pal_kind, uint8_t pal_id) {
    dbg_pal_kind = pal_kind;
    dbg_pal_id = pal_id;
}
void perf_overlay_set_bg(uint8_t bg_id, uint8_t vol, uint16_t off, uint16_t len,
                         uintptr_t addr, bool is_ram) {
    dbg_bg_id = bg_id;
    dbg_bg_vol = vol;
    dbg_bg_off = off;
    dbg_bg_len = len;
    dbg_bg_addr = addr;
    dbg_bg_ram = is_ram;
}
void perf_overlay_clear_sprites(void) {
    dbg_spr_count = 0;
}
void perf_overlay_record_sprite(uint8_t slot, bool is_face, uint16_t spr_id,
                                uint8_t vol, uint16_t off, uint16_t len,
                                uintptr_t addr) {
    if (dbg_spr_count >= PERF_MAX_SPR_LOG) return;
    perf_spr_log_t *e = &dbg_sprs[dbg_spr_count++];
    e->slot = slot;
    e->is_face = is_face ? 1u : 0u;
    e->spr_id = spr_id;
    e->vol = vol;
    e->off = off;
    e->len = len;
    e->addr = addr;
}

void perf_overlay_shutdown(void) {
    measuring = false;
    if (owns_timer) {
        timer_Disable(3);
        timer_Set(3, saved_count);
        timer_Control = (timer_Control & ~TIMER3_BITS) | saved_control;
        owns_timer = false;
    }
    enabled = false;
}
bool perf_overlay_enabled(void) { return enabled; }
void perf_overlay_toggle_page(void) {
    ov_page ^= 1u;
    page_dirty = true;
}
bool perf_overlay_page_dirty(void) {
    bool d = page_dirty;
    page_dirty = false;
    return d;
}
void perf_overlay_toggle(void) {
    if (enabled) perf_overlay_shutdown();
    else {
        enabled = true;
        ov_page = 0;
        page_dirty = true;
        memset(&history, 0, sizeof history);
        if (!(timer_Control & (1u << 6))) {
            saved_control = timer_Control & TIMER3_BITS;
            saved_count = timer_Get(3);
            timer_Set(3, 0);
            timer_Enable(3, TIMER_32K, TIMER_NOINT, TIMER_UP);
            owns_timer = true;
        }
    }
}
void perf_overlay_begin(void) {
    rendered = false;
    measuring = enabled && owns_timer;
    if (measuring) started = timer_Get(3);
}
void perf_overlay_frame_start(void) {
    if (enabled && owns_timer && !measuring) {
        measuring = true;
        rendered = true;
        started = timer_Get(3);
    }
}
void perf_overlay_mark_rendered(void) {
    if (measuring) rendered = true;
}
void perf_overlay_end(void) {
    if (measuring) {
        uint32_t elapsed = timer_Get(3) - started;
        measuring = false;
        if (rendered) perf_history_push(&history, elapsed);
    }
}
/* 3x5 glyphs in 15 bits, row-major. No fontlib/graphx text state mutations. */
static uint16_t glyph(char c) {
    static const uint16_t digits[10] = {
        31599,11415,29671,29647,23497,31183,31215,29257,31727,31695
    };
    static const uint16_t letters[26] = {
        11245,27566,31015,27502,31143,31140,31087,23533,29847, 4682,
        23469,18727,24557,24573,31599,27556,31607,27565,31183,29842,
        23407,23402,23549,23213,23186,29351
    };
    if (c >= '0' && c <= '9') return digits[c - '0'];
    if (c >= 'A' && c <= 'Z') return letters[c - 'A'];
    if (c >= 'a' && c <= 'z') return letters[c - 'a'];
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
static uint8_t ink;
static __attribute__((noinline)) void text(uint8_t *fb, const char *s, unsigned x, unsigned y) {
    uint8_t *base = fb + y * 320u + x;
    for (; *s; ++s, base += 4) {
        uint16_t bits = glyph(*s);
        if (!bits) continue;
        uint8_t *rowp = base;
        for (unsigned row=0; row<5; ++row, rowp += 320) {
            uint8_t mask = (uint8_t)((bits >> (12u - row * 3u)) & 7u);
            if (mask & 4u) rowp[0] = ink;
            if (mask & 2u) rowp[1] = ink;
            if (mask & 1u) rowp[2] = ink;
        }
    }
}

static unsigned probe_largest_heap(void) {
    unsigned lo = 0, hi = 49152u;
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

/* Page 1: 104x80 = 8320 B. Page 2: 128x65 = 8320 B. Both fit in pixelShadow (8400 B). */
enum {
    OV1_X = 210, OV1_Y = 3, OV1_W = 104, OV1_H = 80,
    OV2_X = 188, OV2_Y = 3, OV2_W = 128, OV2_H = 65
};
#ifdef __TICE__
#define PERF_BG_SAVE ((uint8_t *)0xD031F6)
#else
static uint8_t perf_bg_save_host[8400];
#define PERF_BG_SAVE perf_bg_save_host
#endif
static bool bg_saved;
static uint16_t saved_x, saved_y, saved_w, saved_h;

static void active_box(uint16_t *x, uint16_t *y, uint16_t *w, uint16_t *h) {
    if (ov_page == 0) { *x = OV1_X; *y = OV1_Y; *w = OV1_W; *h = OV1_H; }
    else              { *x = OV2_X; *y = OV2_Y; *w = OV2_W; *h = OV2_H; }
}

static __attribute__((noinline)) void perf_overlay_render_to(uint8_t *fb) {
    const uint16_t *pal = render_active_palette();
    unsigned darkest=~0u, brightest=0; uint8_t paper=COL_BLACK; ink=COL_WHITE;
    for (unsigned i=1;i<256;i++) {
        unsigned c=pal[i];
        unsigned light=((c>>10)&31u)*3u+((c>>5)&31u)*6u+(c&31u);
        if(light<darkest){darkest=light;paper=i;}
        if(light>brightest){brightest=light;ink=i;}
    }
    if (brightest == darkest) { paper = COL_BLACK; ink = COL_WHITE; }

    uint16_t bx, by, bw, bh;
    active_box(&bx, &by, &bw, &bh);
    for (unsigned y = by; y < (unsigned)(by + bh); ++y) {
        memset(fb + y * 320u + bx, paper, bw);
    }

    char line[36];
    void *unused;
    unsigned os_free = (unsigned)os_MemChk(&unused);
    perf_memory_t mem = perf_memory_snapshot();

    if (ov_page == 0) {
        sprintf(line, "BSS %u B", (unsigned)((uintptr_t)__bss_high-(uintptr_t)__bss_low));
        text(fb, line, bx + 3, by + 3);
        sprintf(line, "OS FREE %u B", os_free);
        text(fb, line, bx + 3, by + 10);
        sprintf(line, "HEAP %u B%s", (unsigned)mem.bytes, mem.complete ? "" : " PART");
        text(fb, line, bx + 3, by + 17);
        sprintf(line, "ALLOCS %u", mem.count);
        text(fb, line, bx + 3, by + 24);
        text(fb, "VRAM 150K  P1 GRAPH", bx + 3, by + 31);
        text(fb, owns_timer ? "FRAME MS NEW FIRST" : "TIMER BUSY", bx + 3, by + 38);
        for (uint8_t age=0; age<5; ++age) {
            if (age<history.count) sprintf(line, "%u %u MS", age+1, (unsigned)perf_history_ms(&history, age));
            else sprintf(line, "%u -", age+1);
            text(fb, line, bx + 3, by + 45 + age * 7u);
        }
        return;
    }

    /* Page 2: Asset / DPK / Memory Inspector (10 compact rows, 6px step) */
    char pal_str[8];
    if (dbg_pal_kind == PERF_PAL_CG) sprintf(pal_str, "CG%u", dbg_pal_id);
    else if (dbg_pal_kind == PERF_PAL_TITLE) strcpy(pal_str, "TTL");
    else if (dbg_pal_kind == PERF_PAL_MOTION) strcpy(pal_str, "MOT");
    else strcpy(pal_str, "GAME");

    sprintf(line, "P2 CH%u PC%04X SC%u PAL:%s", dbg_chunk_id, dbg_pc_off, dbg_bg_id, pal_str);
    text(fb, line, bx + 2, by + 2);

    sprintf(line, "BG S%u D%u+%u %uB", dbg_bg_id, dbg_bg_vol, dbg_bg_off, dbg_bg_len);
    text(fb, line, bx + 2, by + 8);

    sprintf(line, "BG@%06X %s", (unsigned)dbg_bg_addr, dbg_bg_ram ? "RAM" : "FLS");
    text(fb, line, bx + 2, by + 14);

    for (uint8_t i = 0; i < PERF_MAX_SPR_LOG; ++i) {
        if (i < dbg_spr_count) {
            const perf_spr_log_t *e = &dbg_sprs[i];
            sprintf(line, "%s%u S%u D%u+%u %u @%06X",
                    e->is_face ? "F" : "B", e->slot, e->spr_id,
                    e->vol, e->off, e->len, (unsigned)e->addr);
        } else {
            sprintf(line, "L%u -", i);
        }
        text(fb, line, bx + 2, by + 20 + i * 6u);
    }

    unsigned maxblk = probe_largest_heap();
    sprintf(line, "OS %uB MAXB %uB", os_free, maxblk);
    text(fb, line, bx + 2, by + 44);

    unsigned arc_k = 0;
#ifdef __TICE__
    os_ArcChk();
    arc_k = (unsigned)(os_TempFreeArc >> 10);
#endif
    sprintf(line, "ARC %uK HEAP %uB", arc_k, (unsigned)mem.bytes);
    text(fb, line, bx + 2, by + 50);

    text(fb, "GRAPH:P1/P2", bx + 2, by + 56);
}

void perf_overlay_draw(void) {
    if (!enabled) return;
    gfx_Wait();
    uint8_t old_draw = gfx_GetDraw();
    gfx_SetDrawScreen();
    perf_overlay_render_to((uint8_t *)gfx_vbuffer);
    gfx_SetDraw(old_draw);
}
void perf_overlay_draw_buffer(void) {
    if (!enabled) return;
    perf_overlay_render_to((uint8_t *)gfx_vbuffer);
}
void perf_overlay_present_begin(void) {
    if (!enabled) return;
    active_box(&saved_x, &saved_y, &saved_w, &saved_h);
    uint8_t *fb = (uint8_t *)gfx_vbuffer;
    for (unsigned r = 0; r < saved_h; ++r) {
        memcpy(PERF_BG_SAVE + r * saved_w, fb + (saved_y + r) * 320u + saved_x, saved_w);
    }
    bg_saved = true;
    perf_overlay_render_to(fb);
}
void perf_overlay_present_end(void) {
    if (!bg_saved) return;
    bg_saved = false;
    uint8_t *fb = (uint8_t *)gfx_vbuffer;
    for (unsigned r = 0; r < saved_h; ++r) {
        memcpy(fb + (saved_y + r) * 320u + saved_x, PERF_BG_SAVE + r * saved_w, saved_w);
    }
}
#endif
