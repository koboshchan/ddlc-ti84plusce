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
    for (; *s; ++s, x += 4) {
        uint16_t bits = glyph(*s);
        for (unsigned row=0; row<5; ++row)
            for (unsigned col=0; col<3; ++col)
                if (bits & (1u << (14u-row*3u-col)))
                    fb[(y+row)*320u+x+col] = ink;
    }
}
void perf_overlay_draw(void) {
    if (!enabled) return;
    /* gfx_Wait only fences pending blits. It does not disable interrupts. */
    gfx_Wait();
    uint8_t old_draw = gfx_GetDraw();
    gfx_SetDrawScreen();
    uint8_t *fb = (uint8_t *)gfx_vbuffer;
    /* Motion palettes need not reserve the normal UI colour indices. */
    unsigned darkest=~0u, brightest=0; uint8_t paper=0; ink=0;
    for (unsigned i=0;i<256;i++) {
        unsigned c=gfx_palette[i];
        unsigned light=((c>>10)&31u)*3u+((c>>5)&31u)*6u+(c&31u);
        if(light<darkest){darkest=light;paper=i;}
        if(light>brightest){brightest=light;ink=i;}
    }
    enum { X=210, Y=3, W=107, H=81 };
    for (unsigned y=Y; y<Y+H; ++y) memset(fb+y*320u+X, paper, W);
    char line[27];
    void *unused;
    sprintf(line, "BSS %u B", (unsigned)((uintptr_t)__bss_high-(uintptr_t)__bss_low));
    text(fb,line,X+3,Y+3);
    sprintf(line, "OS FREE %u B", (unsigned)os_MemChk(&unused));
    text(fb,line,X+3,Y+10);
    perf_memory_t mem=perf_memory_snapshot();
    sprintf(line, "HEAP %u B%s", (unsigned)mem.bytes, mem.complete ? "" : " PART");
    text(fb,line,X+3,Y+17);
    sprintf(line, "ALLOCS %u", mem.count);
    text(fb,line,X+3,Y+24);
    text(fb,"VRAM RESERVED 150 KIB",X+3,Y+31);
    text(fb,owns_timer ? "FRAME MS NEW FIRST" : "TIMER BUSY",X+3,Y+38);
    for (uint8_t age=0; age<5; ++age) {
        if (age<history.count) sprintf(line,"%u %lu MS",age+1,(unsigned long)perf_history_ms(&history,age));
        else sprintf(line,"%u -",age+1);
        text(fb,line,X+3,Y+45+age*7u);
    }
    gfx_SetDraw(old_draw);
}
#endif
