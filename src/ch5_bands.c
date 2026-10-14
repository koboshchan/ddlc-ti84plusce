#include "ch5_bands.h"
#include <compression.h>
#include <fileioc.h>
#include <string.h>

/* Layer-local caches keep decoded rows valid while another layer is sampled.
 * No framebuffer scratch: textbox drawing and graphx swaps overwrite it. */
static uint8_t pixels_bg[2][320 * 8];
static uint8_t pixels_fg[2][72 * 8];
static uint8_t cached_band[4] = {255,255,255,255};
static const uint8_t *layer_data[4];
static uint16_t layer_size[4];
static bool frame_active;

static uint16_t u16(const uint8_t *p) {
    return p[0] | ((uint16_t)p[1] << 8);
}
void ch5_band_begin(void) {
    memset(layer_data,0,sizeof layer_data);
    frame_active=true;
}
void ch5_band_end(void) {
    /* Archive GC and RAM-variable relocation outside a draw must never leave
     * a stale pointer. No variable mutation is permitted between begin/end. */
    memset(layer_data,0,sizeof layer_data);
    frame_active=false;
}
void ch5_band_invalidate(void) {
    memset(cached_band,255,sizeof cached_band);
    memset(layer_data,0,sizeof layer_data);
}
const uint8_t *ch5_band_row(uint8_t layer,uint16_t row) {
    uint8_t band,handle;
    uint16_t width,height,size,offset,next;
    const uint8_t *data;
    char name[]="DMOT0";
    if(layer>3)return 0;
    width=layer<2?320:72;
    height=layer<2?180:263;
    /* Validate before a cache hit, including the short final band. */
    if(row>=height)return 0;
    band=row>>3;
    uint8_t *pixels=layer<2?pixels_bg[layer]:pixels_fg[layer-2];
    if(cached_band[layer]==band)return pixels+(row&7u)*width;
    data=frame_active?layer_data[layer]:0;
    if(data) {
        size=layer_size[layer];
    } else {
        name[4]=48+layer;
        handle=ti_Open(name,"r");
        if(!handle)return 0;
        size=ti_GetSize(handle);
        data=ti_GetDataPtr(handle);
        if(size<12 || memcmp(data,"DMT1",4) ||
           u16(data+4)!=width || u16(data+6)!=height ||
           data[8]!=8 || data[9]!=(height+7)/8 ||
           size<10+2*data[9]) {
            ti_Close(handle);return 0;
        }
        ti_Close(handle);
        if(frame_active) {
            layer_data[layer]=data;
            layer_size[layer]=size;
        }
    }
    offset=u16(data+10+2*band);
    next=band+1<data[9]?u16(data+12+2*band):size;
    if(offset<10+2*data[9] || offset>=next || next>size)return 0;
    /* Trusted packaged ZX0 streams are checked by the asset generator/audit. */
    zx0_Decompress(pixels,data+offset);
    cached_band[layer]=band;
    return pixels+(row&7u)*width;
}
