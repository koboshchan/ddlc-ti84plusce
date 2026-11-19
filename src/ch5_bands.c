#include "ch5_bands.h"
#ifdef __TICE__
#include "fast_ops.h"
#endif
#include <compression.h>
#include <fileioc.h>
#include <string.h>
#include <stdlib.h>
#include "perf_memory.h"


/* Layer-local caches keep decoded rows valid while another layer is sampled.
 * On-calc (__TICE__), ch5_band_scratch points into the 19,200B bottom scratch
 * region of gfx_vbuffer during ch5_motion_draw(), saving 6,272 B of .bss. */
#ifdef __TICE__
uint8_t *ch5_band_scratch;
#else
static uint8_t host_band_scratch[2 * 320 * 8 + 2 * 72 * 8];
static uint8_t *ch5_band_scratch = host_band_scratch;
#endif
static uint8_t cached_band[4] = {255,255,255,255};
static struct { const uint8_t *data[4]; uint16_t sizes[4]; uint8_t flags[15]; } frame_cache;
#define layer_data frame_cache.data
#define ready frame_cache.flags
#define layer_size frame_cache.sizes

uint8_t *resident;


static uint16_t u16(const uint8_t *p) {
    return p[0] | ((uint16_t)p[1] << 8);
}
void ch5_band_begin(void) {
    memset(&frame_cache,0,sizeof frame_cache);
    frame_cache.flags[0]=1;
}
void ch5_band_end(void) {
    /* Archive GC and RAM-variable relocation outside a draw must never leave
     * a stale pointer. No variable mutation is permitted between begin/end. */
    frame_cache.flags[0]=0;
}
void ch5_band_invalidate(void) {
    memset(cached_band,255,sizeof cached_band);
    memset(layer_data,0,sizeof layer_data);
}
const uint8_t *ch5_band_row(uint8_t layer,uint16_t row) {
    uint8_t band,handle;
    uint16_t width,size,offset,next;
    const uint8_t *data;
    char name[]="DMOT0";
    width=layer<2?320:72;
    /* Validate before a cache hit, including the short final band. */
    if(layer>3 || row>=(layer<2?180:263))return 0;
    band=row>>3;

    if (!ch5_band_scratch) return 0;
    uint8_t *pixels = layer < 2
        ? (ch5_band_scratch + (size_t)layer * (320u * 8u))
        : (ch5_band_scratch + 2u * 320u * 8u + (size_t)(layer - 2u) * (72u * 8u));
    bool keep=layer==0 && (uint8_t)(band-3)<12 && resident;
    if(keep)pixels=resident+(band-3)*2560;
    if((keep && ready[band]) || (!keep && cached_band[layer]==band))return pixels+(row&7u)*width;
    data=frame_cache.flags[0]?layer_data[layer]:0;
    if(data) {
        size=layer_size[layer];
    } else {
        name[4]=48+layer;
        handle=ti_Open(name,"r");
        if(!handle)return 0;
        size=ti_GetSize(handle);
        data=ti_GetDataPtr(handle);
        if(size<12 || memcmp(data,"DMT1",4) ||
           u16(data+4)!=width || u16(data+6)!=(layer<2?180:263) ||
           data[8]!=8 || data[9]!=(layer<2?23:33) ||
           size<10+2*data[9]) {
            ti_Close(handle);return 0;
        }
        ti_Close(handle);
        if(frame_cache.flags[0]) {
            layer_data[layer]=data;
            layer_size[layer]=size;
        }
    }
    offset=u16(data+10+2*band);
    next=band+1<data[9]?u16(data+12+2*band):size;
    if(offset<10+2*data[9] || offset>=next || next>size)return 0;
    /* Trusted packaged ZX0 streams are checked by the asset generator/audit. */
#ifdef __TICE__
    fast_zx0_decompress(pixels,data+offset);
#else
    zx0_Decompress(pixels,data+offset);
#endif
    if(keep)ready[band]=1;else cached_band[layer]=band;
    return pixels+(row&7u)*width;
}
