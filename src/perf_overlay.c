/* Debug-only telemetry. Dev menu toggles; off at startup. Timer 3 is borrowed
 * ONLY if already disabled. Never touches CPU interrupt state or shadow regs.
 * RAM labels deliberately distinguish static BSS from OS user-RAM free.
 * VRAM is graphx's two 76800-byte allocations, NOT extra scratch on top.
 * Direct screen-only ink after presentation never contaminates the canonical
 * draw buffer, so no backup allocation and no overlay restoration cost. */
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
#include <string.h>
#include <stdio.h>
#include <stddef.h>
extern unsigned char __bss_low[], __bss_high[];
static perf_history_t history;
static bool enabled, owns_timer, measuring;
static bool rendered;
static uint16_t saved_control;
static uint32_t saved_count, started;
#define TIMER3_BITS ((7u << 6) | (1u << 11))
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
void perf_overlay_toggle(void) {
    if (enabled) perf_overlay_shutdown();
    else {
        enabled = true;
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
    if (c >= '0' && c <= '9') return digits[c-'0'];
    switch (c) {
    case 'A': return 11245;
    case 'B': return 27566;
    case 'E': return 31143;
    case 'F': return 31140;
    case 'I': return 29847;
    case 'K': return 23469;
    case 'M': return 24557;
    case 'N': return 24573;
    case 'C': return 31015;
    case 'D': return 27502;
    case 'H': return 23533;
    case 'L': return 18727;
    case 'P': return 27556;
    case ' ': return 0;
    case 'O': return 31599;
    case 'R': return 27565;
    case 'S': return 31183;
    case 'T': return 29842;
    case 'U': return 23407;
    case 'V': return 23402;
    case 'W': return 23549;
    case 'Y': return 23186;
    case '/': return 4772;
    case '-': return 448;
    default: return 0;
    }
}
static uint8_t ink;
static void text(uint8_t *fb, const char *s, unsigned x, unsigned y) {
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
enum { OV_X = 210, OV_Y = 3, OV_W = 104, OV_H = 80 };
#ifdef __TICE__
/* Use TI-OS pixelShadow (0xD031F6..0xD052C5, 8400 B strictly below BSSHEAP_LOW)
 * for off-screen atomic overlay staging: 104*80 = 8320 B, zero BSS/heap cost. */
#define PERF_BG_SAVE ((uint8_t *)0xD031F6)
#else
static uint8_t perf_bg_save_host[OV_W * OV_H];
#define PERF_BG_SAVE perf_bg_save_host
#endif
static bool bg_saved;

static void perf_overlay_render_to(uint8_t *fb) {
    /* Skip index 0 (transparent/pink key in game/title palettes). Use the
     * target palette (fade_saved during a fade) so off-screen draws while
     * gfx_palette is blacked out still pick the destination's dark/light pair. */
    const uint16_t *pal = render_active_palette();
    unsigned darkest=~0u, brightest=0; uint8_t paper=COL_BLACK; ink=COL_WHITE;
    for (unsigned i=1;i<256;i++) {
        unsigned c=pal[i];
        unsigned light=((c>>10)&31u)*3u+((c>>5)&31u)*6u+(c&31u);
        if(light<darkest){darkest=light;paper=i;}
        if(light>brightest){brightest=light;ink=i;}
    }
    if (brightest == darkest) { paper = COL_BLACK; ink = COL_WHITE; }
    for (unsigned y=OV_Y; y<OV_Y+OV_H; ++y) memset(fb+y*320u+OV_X, paper, OV_W);
    char line[27];
    void *unused;
    sprintf(line, "BSS %u B", (unsigned)((uintptr_t)__bss_high-(uintptr_t)__bss_low));
    text(fb,line,OV_X+3,OV_Y+3);
    sprintf(line, "OS FREE %u B", (unsigned)os_MemChk(&unused));
    text(fb,line,OV_X+3,OV_Y+10);
    perf_memory_t mem=perf_memory_snapshot();
    sprintf(line, "HEAP %u B%s", (unsigned)mem.bytes, mem.complete ? "" : " PART");
    text(fb,line,OV_X+3,OV_Y+17);
    sprintf(line, "ALLOCS %u", mem.count);
    text(fb,line,OV_X+3,OV_Y+24);
    text(fb,"VRAM RESERVED 150 KIB",OV_X+3,OV_Y+31);
    text(fb,owns_timer ? "FRAME MS NEW FIRST" : "TIMER BUSY",OV_X+3,OV_Y+38);
    for (uint8_t age=0; age<5; ++age) {
        if (age<history.count) sprintf(line,"%u %u MS",age+1,(unsigned)perf_history_ms(&history,age));
        else sprintf(line,"%u -",age+1);
        text(fb,line,OV_X+3,OV_Y+45+age*7u);
    }
}
void perf_overlay_draw(void) {
    if (!enabled) return;
    /* gfx_Wait only fences pending blits. It does not disable interrupts. */
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
    uint8_t *fb = (uint8_t *)gfx_vbuffer;
    for (unsigned r = 0; r < OV_H; ++r) {
        memcpy(PERF_BG_SAVE + r * OV_W, fb + (OV_Y + r) * 320u + OV_X, OV_W);
    }
    bg_saved = true;
    perf_overlay_render_to(fb);
}
void perf_overlay_present_end(void) {
    if (!bg_saved) return;
    bg_saved = false;
    uint8_t *fb = (uint8_t *)gfx_vbuffer;
    for (unsigned r = 0; r < OV_H; ++r) {
        memcpy(fb + (OV_Y + r) * 320u + OV_X, PERF_BG_SAVE + r * OV_W, OV_W);
    }
}
#endif
