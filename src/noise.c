/* Ch5 only, quarter-coverage ordered alpha approximation, with vignette
 * attenuation ABOVE the noise. All archived pointers live only while their
 * handles are open; no file operation occurs between begin and end. */
#include "noise.h"
#include "noise_prng.h"
#include <fileioc.h>
#include <graphx.h>
#include <time.h>
#include <string.h>
#include <stdlib.h>
#include "perf_memory.h"
static ti_var_t h[1];
static const uint8_t *meta;
static uint8_t colors[32];
static uint32_t cached_palette_hash;
static bool palette_valid;
/* Proven over all 65535 nonzero LFSR starting states: <=4988 selected
 * blocks in 19200 draws with coverage<=4. Four original bytes/block. */
#define NOISE_UNDO_BYTES 19952u
static uint8_t *undo;
static unsigned undo_size,undo_coverage,undo_rows;
#ifdef NOISE_HOST_GOLDEN
static uint16_t undo_seed;
#endif
static uint8_t *undo_buffer;
#ifdef NOISE_HOST_GOLDEN
/* Native eZ80 unsigned is 24-bit. Keeping the recurrence native avoids
 * 16-bit helper calls, and forced inlining overrides -fno-inline-functions. */
static __attribute__((always_inline)) inline unsigned noise_step_native(unsigned s) {
    return (s >> 1) ^ ((0u - (s & 1u)) & 0xB400u);
}
#else
/* CEdev unsigned/pointer fields are 3-byte native ADL words. ASM preserves
 * IX/IY and uses only bounded 160-column, 10-block-row stripes. */
typedef struct { uint8_t *fb,*saved; unsigned seed,coverage; const uint8_t *mask,*colors; unsigned rows; } noise_asm_context;
_Static_assert(sizeof(noise_asm_context)==21, "ADL context packing");
extern void noise_draw_asm(noise_asm_context *ctx);
extern void noise_restore_asm(uint8_t *fb,uint8_t *saved,unsigned seed,unsigned coverage,unsigned rows);
#endif
#ifndef NOISE_HOST_GOLDEN
/* On-device fast amortized + precomputed-tile static:
 * 192 cells (16 cols x 12 rows of 20x20 px). Each cell owns a fixed 100-byte
 * undo slice in `undo` (192 * 100 = 19,200 B <= NOISE_UNDO_BYTES), so any
 * 1/5 slice of cells can be restored and refreshed independently in ~1-2 ms. */
#define NOISE_CELL_COLS   16u
#define NOISE_CELL_ROWS   12u
#define NOISE_CELLS       192u
#define NOISE_TILES       8u
#define NOISE_TILE_MAX    25u
#define NOISE_CELL_UNDO   100u
#define NOISE_AMORT_SLOTS 5u

extern void noise_tile_draw_cell(uint8_t *cell_fb, uint8_t *cell_undo,
                                 const uint16_t *entries, uint8_t count,
                                 uint8_t c_dark, uint8_t c_light);
extern void noise_tile_restore_cell(uint8_t *cell_fb, const uint8_t *cell_undo,
                                    const uint16_t *entries, uint8_t count);

static uint16_t noise_tiles[NOISE_TILES][NOISE_TILE_MAX];
static bool tiles_ready;
static uint8_t mask_cache[NOISE_CELLS];
static bool mask_cached;
static uint8_t cell_tile[NOISE_CELLS];
static uint8_t cell_cnt[NOISE_CELLS];
static uint8_t amort_slot;
static uint16_t xorshift_state = 0xACE1u;

static uint16_t noise_xorshift16(void) {
    uint16_t x = xorshift_state;
    x ^= (uint16_t)(x << 7);
    x ^= (uint16_t)(x >> 9);
    x ^= (uint16_t)(x << 8);
    if (!x) x = 0xACE1u;
    xorshift_state = x;
    return x;
}

static void noise_build_tile(uint8_t t) {
    /* Pick NOISE_TILE_MAX distinct 2x2 blocks inside a 10x10 block cell (20x20 px). */
    uint8_t used[100];
    memset(used, 0, sizeof(used));
    for (uint8_t k = 0; k < NOISE_TILE_MAX; k++) {
        uint8_t idx = (uint8_t)(noise_xorshift16() % 100u);
        while (used[idx]) {
            if (++idx >= 100u) idx = 0;
        }
        used[idx] = 1;
        uint8_t by = (uint8_t)(idx / 10u);
        uint8_t bx = (uint8_t)(idx - by * 10u);
        uint16_t off = (uint16_t)((uint16_t)by * 640u + (uint16_t)bx * 2u);
        if (noise_xorshift16() & 1u) off |= 0x8000u;
        noise_tiles[t][k] = off;
    }
}

static void noise_ensure_tiles(void) {
    if (tiles_ready) return;
    for (uint8_t t = 0; t < NOISE_TILES; t++) {
        noise_build_tile(t);
    }
    tiles_ready = true;
}

static uint8_t *noise_cell_ptr(uint8_t *fb, uint8_t cell) {
    uint8_t cy = (uint8_t)(cell >> 4);
    uint8_t cx = (uint8_t)(cell & 15u);
    return fb + (size_t)cy * (20u * 320u) + (size_t)cx * 20u;
}
#endif

void noise_restore(void){
    if(!undo_size)return;
#ifndef NOISE_HOST_GOLDEN
    if (undo && undo_buffer) {
        for (uint8_t c = 0; c < NOISE_CELLS; c++) {
            uint8_t cnt = cell_cnt[c];
            if (!cnt) continue;
            noise_tile_restore_cell(noise_cell_ptr(undo_buffer, c),
                                    undo + (size_t)c * NOISE_CELL_UNDO,
                                    noise_tiles[cell_tile[c]], cnt);
            cell_cnt[c] = 0;
        }
    }
#else
    unsigned row,col,n=0;unsigned seed=undo_seed;
    for(row=0;row<undo_rows;row+=2){
        uint8_t *d=undo_buffer+row*320u;
        for(col=0;col<320;col+=2){
            seed=noise_step_native(seed);
            if((seed&15u)>=undo_coverage)continue;
            if(n+4>undo_size){undo_size=0;return;}
            d[col]=undo[n++];d[col+1]=undo[n++];
            d[col+320]=undo[n++];d[col+321]=undo[n++];
        }
    }
#endif
    undo_size=0;
}
void noise_release(void){
    noise_restore();free(undo);undo=0;
#ifndef NOISE_HOST_GOLDEN
    mask_cached = false;
#endif
}


void noise_end(void) {
    unsigned i; for(i=0;i<1;i++) { if(h[i]) ti_Close(h[i]); h[i]=0; }
    meta=0;
}
bool noise_begin(uint8_t scene) {
    unsigned i,k; const char *names[1]={"DNFX"};
    noise_end();
#ifndef NOISE_HOST_GOLDEN
    if (!mask_cached) {
        for(i=0;i<1;i++) {
            h[i]=ti_Open(names[i],"r");
            if(!h[i] || ti_GetSize(h[i]) != 448) { noise_end(); return false; }
        }
        meta=ti_GetDataPtr(h[0]);
        memcpy(mask_cache, meta + 256, NOISE_CELLS);
        mask_cached = true;
        noise_end();
    }
#else
    for(i=0;i<1;i++) {
        h[i]=ti_Open(names[i],"r");
        if(!h[i] || ti_GetSize(h[i]) != 448) { noise_end(); return false; }
    }
    meta=ti_GetDataPtr(h[0]);
#endif
    (void)scene; /* explicit VM lifetime flag, never inferred from scene */
    uint32_t hash = 0;
    for (unsigned p = 0; p < 256; p++) {
        hash = ((hash << 5) - hash) ^ gfx_palette[p];
    }
    if (palette_valid && hash == cached_palette_hash) return true;
    cached_palette_hash = hash;
    palette_valid=true;
    for(k=0;k<32;k++) {
        unsigned target = k<16?0:31-(k-16)*23/15;
        unsigned best=65535;
        for(i=0;i<256;i++) {
            unsigned p=gfx_palette[i],r=(p>>10)&31,g=(p>>5)&31,bl=p&31;
            int dr=(int)r-target,dg=(int)g-target,db=(int)bl-target;
            unsigned d=dr*dr+dg*dg+db*db;
            if(d<best){best=d;colors[k]=i;}
        }
    }
    return true;
}
static void noise_frame_rows(unsigned elapsed_ms,unsigned frame_rows) {
    unsigned coverage=elapsed_ms>=3000?4u:(uint32_t)elapsed_ms*4u/3000u;
#ifndef NOISE_HOST_GOLDEN
    if(!coverage){ noise_restore(); return; }
    if(!undo)undo=malloc(NOISE_UNDO_BYTES);
    if(!undo)return;
    noise_ensure_tiles();

    uint8_t *fb = (uint8_t*)gfx_vbuffer;
    uint8_t active_cells = (frame_rows >= 240u) ? NOISE_CELLS : (uint8_t)(16u * 9u);
    static const uint8_t cov_counts[5] = { 0, 6, 12, 19, 25 };
    uint8_t cnt = cov_counts[coverage];

    /* If framebuffer pointer changed, restore the previous buffer first. */
    if (undo_size && undo_buffer != fb) {
        noise_restore();
    }
    /* If shrinking from 240 -> 180 rows, restore bottom dialogue cells cleanly. */
    if (undo_size && active_cells < NOISE_CELLS) {
        for (uint8_t c = active_cells; c < NOISE_CELLS; c++) {
            if (!cell_cnt[c]) continue;
            noise_tile_restore_cell(noise_cell_ptr(fb, c),
                                    undo + (size_t)c * NOISE_CELL_UNDO,
                                    noise_tiles[cell_tile[c]], cell_cnt[c]);
            cell_cnt[c] = 0;
        }
    }

    undo_buffer = fb;
    undo_coverage = coverage;
    undo_rows = frame_rows;

    if (!undo_size) {
        /* Fresh background frame: stamp all active cells with precomputed tiles
         * selected by fast xorshift (no LFSR rejection loop). */
        memset(cell_cnt, 0, sizeof(cell_cnt));
        for (uint8_t c = 0; c < active_cells; c++) {
            uint8_t t = (uint8_t)(noise_xorshift16() & (NOISE_TILES - 1u));
            uint8_t mv = mask_cache[c];
            noise_tile_draw_cell(noise_cell_ptr(fb, c),
                                 undo + (size_t)c * NOISE_CELL_UNDO,
                                 noise_tiles[t], cnt,
                                 colors[mv], colors[mv + 16u]);
            cell_tile[c] = t;
            cell_cnt[c] = cnt;
        }
        undo_size = (unsigned)active_cells * NOISE_CELL_UNDO;
    } else {
        /* Persisted frame: amortize static updates 1/5 per frame across 5
         * interleaved slices so the whole screen shimmers at high FPS. */
        uint8_t slot = amort_slot;
        if (++amort_slot >= NOISE_AMORT_SLOTS) amort_slot = 0;
        for (uint8_t c = slot; c < active_cells; c += NOISE_AMORT_SLOTS) {
            uint8_t *cfb = noise_cell_ptr(fb, c);
            uint8_t *cu  = undo + (size_t)c * NOISE_CELL_UNDO;
            if (cell_cnt[c]) {
                noise_tile_restore_cell(cfb, cu, noise_tiles[cell_tile[c]], cell_cnt[c]);
            }
            uint8_t t = (uint8_t)(noise_xorshift16() & (NOISE_TILES - 1u));
            if (cell_cnt[c] && t == cell_tile[c]) {
                t = (uint8_t)((t + 1u) & (NOISE_TILES - 1u));
            }
            uint8_t mv = mask_cache[c];
            noise_tile_draw_cell(cfb, cu, noise_tiles[t], cnt,
                                 colors[mv], colors[mv + 16u]);
            cell_tile[c] = t;
            cell_cnt[c] = cnt;
        }
        /* Fill any newly exposed bottom cells if expanding 180 -> 240 rows. */
        if (active_cells == NOISE_CELLS) {
            for (uint8_t c = 144u; c < NOISE_CELLS; c++) {
                if (cell_cnt[c]) continue;
                uint8_t t = (uint8_t)(noise_xorshift16() & (NOISE_TILES - 1u));
                uint8_t mv = mask_cache[c];
                noise_tile_draw_cell(noise_cell_ptr(fb, c),
                                     undo + (size_t)c * NOISE_CELL_UNDO,
                                     noise_tiles[t], cnt,
                                     colors[mv], colors[mv + 16u]);
                cell_tile[c] = t;
                cell_cnt[c] = cnt;
            }
        }
        undo_size = (unsigned)active_cells * NOISE_CELL_UNDO;
    }
#else
    unsigned row,seed=(0xDD1Cu^(elapsed_ms/100u)*40503u)&65535u;
    if(!seed)seed=1;
    noise_restore();
    if(!coverage)return;
    if(!undo)undo=malloc(NOISE_UNDO_BYTES);
    if(!undo)return;
    undo_seed=seed;undo_coverage=coverage;undo_rows=frame_rows;
    undo_buffer=(uint8_t*)gfx_vbuffer;
    unsigned saved=0;
    uint8_t mask_columns[160];
    unsigned col,mask_row=255;
    for(row=0;row<frame_rows;row+=2){
        uint8_t *d=(uint8_t*)gfx_vbuffer+row*320u;
        unsigned current_mask_row=row/20u;
        if(current_mask_row!=mask_row){
            mask_row=current_mask_row;
            const uint8_t *mask=meta+256+mask_row*16u;
            for(unsigned band=0;band<16;band++)
                memset(mask_columns+band*10u,mask[band],10);
        }
        for(col=0;col<320;col+=2){
            seed=noise_step_native(seed);
            if((seed&15u)>=coverage)continue;
            uint8_t color=colors[mask_columns[col>>1]+((seed>>4)&16u)];
            if(saved+4>NOISE_UNDO_BYTES){undo_size=saved;noise_restore();return;}
            undo[saved++]=d[col];undo[saved++]=d[col+1];
            undo[saved++]=d[col+320];undo[saved++]=d[col+321];
            d[col]=d[col+1]=d[col+320]=d[col+321]=color;
        }
    }
    undo_size=saved;
#endif
}

/* Full-frame API retained for standalone/golden callers. The scene-only API
 * is valid ONLY when the caller unconditionally repaints rows 180..239 before
 * presentation. Restore remembers each frame's extent, even across API changes. */
void noise_frame(unsigned elapsed_ms) { noise_frame_rows(elapsed_ms,240u); }
void noise_frame_scene(unsigned elapsed_ms) { noise_frame_rows(elapsed_ms,180u); }
bool noise_is_fullscreen(void) { return undo_size != 0 && undo_rows == 240u; }
