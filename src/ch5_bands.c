#include "ch5_bands.h"
#include <compression.h>
#include <fileioc.h>
#include <string.h>
static uint8_t pixels[2560];
static uint8_t cached_layer=255,cached_band=255;
static uint16_t cached_width;
static uint16_t u16(const uint8_t *p){return p[0]|((uint16_t)p[1]<<8);}
void ch5_band_invalidate(void){cached_layer=cached_band=255;}
const uint8_t *ch5_band_row(uint8_t layer,uint16_t row){
 uint8_t band,handle;uint16_t width,height,size,offset,next;const uint8_t *data;
 char name[]="DMOT0";
 if(layer>3)return 0;
 band=row/8;
 if(layer==cached_layer&&band==cached_band)return pixels+(row%8)*cached_width;
 name[4]='0'+layer;handle=ti_Open(name,"r");if(!handle)return 0;
 size=ti_GetSize(handle);data=ti_GetDataPtr(handle);
 if(size<12||memcmp(data,"DMT1",4)){ti_Close(handle);return 0;}
 width=u16(data+4);height=u16(data+6);
 if(width!=(layer<2?320:72)||height!=(layer<2?180:263)||
    row>=height||data[8]!=8||data[9]!=(height+7)/8||
    size<10+2*data[9]){ti_Close(handle);return 0;}
 offset=u16(data+10+2*band);
 next=band+1<data[9]?u16(data+12+2*band):size;
 if(offset<10+2*data[9]||offset>=next||next>size){ti_Close(handle);return 0;}
 /* Generator/audit validates decoded band size. All read-only packaged
  * streams are trusted, exactly width*min(8,height-band*8) output. */
 zx0_Decompress(pixels,data+offset);ti_Close(handle);
 cached_layer=layer;cached_band=band;cached_width=width;
 return pixels+(row%8)*width;
}
