#ifndef PERF_OVERLAY_H
#define PERF_OVERLAY_H
#include <stdbool.h>
#include <stdint.h>

enum {
    PERF_PAL_GAME = 0,
    PERF_PAL_TITLE = 1,
    PERF_PAL_MOTION = 2,
    PERF_PAL_CG = 3
};

#ifdef DDLC_PERF_OVERLAY
void perf_overlay_toggle(void);
bool perf_overlay_enabled(void);
void perf_overlay_toggle_page(void);
bool perf_overlay_page_dirty(void);
void perf_overlay_begin(void);
void perf_overlay_frame_start(void);
void perf_overlay_mark_rendered(void);
void perf_overlay_end(void);
void perf_overlay_draw(void);
void perf_overlay_draw_buffer(void);
void perf_overlay_present_begin(void);
void perf_overlay_present_end(void);
void perf_overlay_shutdown(void);
void perf_overlay_set_vm(uint8_t chunk_id, uint16_t pc_off, uint8_t bg_id);
void perf_overlay_set_pal(uint8_t pal_kind, uint8_t pal_id);
void perf_overlay_set_bg(uint8_t bg_id, uint8_t vol, uint16_t off, uint16_t len,
                         uintptr_t addr, bool is_ram);
void perf_overlay_clear_sprites(void);
void perf_overlay_record_sprite(uint8_t slot, bool is_face, uint16_t spr_id,
                                uint8_t vol, uint16_t off, uint16_t len,
                                uintptr_t addr);
#else
#define perf_overlay_toggle() ((void)0)
#define perf_overlay_enabled() (false)
#define perf_overlay_toggle_page() ((void)0)
#define perf_overlay_page_dirty() (false)
#define perf_overlay_begin() ((void)0)
#define perf_overlay_frame_start() ((void)0)
#define perf_overlay_mark_rendered() ((void)0)
#define perf_overlay_end() ((void)0)
#define perf_overlay_draw() ((void)0)
#define perf_overlay_draw_buffer() ((void)0)
#define perf_overlay_present_begin() ((void)0)
#define perf_overlay_present_end() ((void)0)
#define perf_overlay_shutdown() ((void)0)
#define perf_overlay_set_vm(c,p,b) ((void)0)
#define perf_overlay_set_pal(k,i) ((void)0)
#define perf_overlay_set_bg(b,v,o,l,a,r) ((void)0)
#define perf_overlay_clear_sprites() ((void)0)
#define perf_overlay_record_sprite(sl,f,id,v,o,l,a) ((void)0)
#endif
#endif
