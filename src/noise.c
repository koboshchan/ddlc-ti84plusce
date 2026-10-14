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
static uint16_t cached_palette[256];
static bool palette_valid;
/* Proven over all 65535 nonzero LFSR starting states: <=4988 selected
 * blocks in 19200 draws with coverage<=4. Four original bytes/block. */
#define NOISE_UNDO_BYTES 19952u
static uint8_t *undo;
static unsigned undo_size,undo_coverage,undo_rows;
static uint16_t undo_seed;
static uint8_t *undo_buffer;
/* Native eZ80 unsigned is 24-bit. Keeping the recurrence native avoids
 * 16-bit helper calls, and forced inlining overrides -fno-inline-functions. */
static __attribute__((always_inline)) inline unsigned noise_step_native(unsigned s) {
    return (s >> 1) ^ ((0u - (s & 1u)) & 0xB400u);
}
#ifndef NOISE_HOST_GOLDEN
/* CEdev unsigned/pointer fields are 3-byte native ADL words. ASM preserves
 * IX/IY and uses only bounded 160-column, 10-block-row stripes. */
typedef struct { uint8_t *fb,*saved; unsigned seed,coverage; const uint8_t *mask,*colors; unsigned rows; } noise_asm_context;
_Static_assert(sizeof(noise_asm_context)==21, "ADL context packing");
extern void noise_draw_asm(noise_asm_context *ctx);
extern void noise_restore_asm(uint8_t *fb,uint8_t *saved,unsigned seed,unsigned coverage,unsigned rows);
#endif
void noise_restore(void){
    if(!undo_size)return;
#ifndef NOISE_HOST_GOLDEN
    noise_restore_asm(undo_buffer,undo,undo_seed,undo_coverage,undo_rows/2u);
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
}


void noise_end(void) {
    unsigned i; for(i=0;i<1;i++) { if(h[i]) ti_Close(h[i]); h[i]=0; }
    meta=0;
}
bool noise_begin(uint8_t scene) {
    unsigned i,k; const char *names[1]={"DNFX"};
    noise_end();
    for(i=0;i<1;i++) {
        h[i]=ti_Open(names[i],"r");
        if(!h[i] || ti_GetSize(h[i]) != 448) { noise_end(); return false; }
    }
    meta=ti_GetDataPtr(h[0]);
    (void)scene; /* explicit VM lifetime flag, never inferred from scene */
    if (palette_valid && !memcmp(cached_palette, gfx_palette, sizeof cached_palette)) return true;
    memcpy(cached_palette, gfx_palette, sizeof cached_palette);
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
    unsigned row,coverage=elapsed_ms>=3000?4u:(uint32_t)elapsed_ms*4u/3000u;
    unsigned seed=(0xDD1Cu^(elapsed_ms/100u)*40503u)&65535u;
    if(!seed)seed=1;
    noise_restore();
    if(!coverage)return;
    if(!undo)undo=malloc(NOISE_UNDO_BYTES);
    if(!undo)return;
    undo_seed=seed;undo_coverage=coverage;undo_rows=frame_rows;
    undo_buffer=(uint8_t*)gfx_vbuffer;
    unsigned saved=0;
    uint8_t mask_columns[160];
#ifndef NOISE_HOST_GOLDEN
    noise_asm_context ctx={undo_buffer,undo,seed,coverage,mask_columns,colors,10};
    /* One stripe is 20 display rows, so each authored mask row is expanded
     * once and the LFSR carries continuously across all 19200 blocks. */
    for(row=0;row<frame_rows;row+=20){
        const uint8_t *mask=meta+256+(row/20u)*16u;
        for(unsigned band=0;band<16;band++)memset(mask_columns+band*10u,mask[band],10);
        ctx.fb=undo_buffer+row*320u;
        noise_draw_asm(&ctx);
    }
    saved=(unsigned)(ctx.saved-undo);
#else
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
#endif
    undo_size=saved;
}

/* Full-frame API retained for standalone/golden callers. The scene-only API
 * is valid ONLY when the caller unconditionally repaints rows 180..239 before
 * presentation. Restore remembers each frame's extent, even across API changes. */
void noise_frame(unsigned elapsed_ms) { noise_frame_rows(elapsed_ms,240u); }
void noise_frame_scene(unsigned elapsed_ms) { noise_frame_rows(elapsed_ms,180u); }
