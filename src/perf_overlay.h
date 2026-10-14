#ifndef DDLC_PERF_OVERLAY_H
#define DDLC_PERF_OVERLAY_H
#include <stdbool.h>
#ifdef DDLC_PERF_OVERLAY
void perf_overlay_toggle(void);
bool perf_overlay_enabled(void);
void perf_overlay_begin(void);
void perf_overlay_mark_rendered(void);
void perf_overlay_end(void);
void perf_overlay_draw(void);
void perf_overlay_shutdown(void);
#else
#define perf_overlay_toggle() ((void)0)
#define perf_overlay_enabled() false
#define perf_overlay_begin() ((void)0)
#define perf_overlay_mark_rendered() ((void)0)
#define perf_overlay_end() ((void)0)
#define perf_overlay_draw() ((void)0)
#define perf_overlay_shutdown() ((void)0)
#endif
#endif
