#include <stdio.h>

#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <assert.h>
static clock_t authored;
static unsigned begins,ends;static bool band_active;static int fail_row=-1;
static clock_t fixture_clock(void){return authored;}
static uint8_t storage[76800+64], expected[57600];
uint8_t *gfx_vbuffer=storage+32;
static uint8_t record[18]={1,4,0,1,1,5,2,2,3,6};
static uint16_t palette[256];
static uint8_t nearest[32768],layers[4][57600];
static uint8_t qlut[12560];static unsigned lut_available=1,lut_size=12560;static unsigned opened[5];
static uint32_t seed=17;
static uint32_t random32(void){seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return seed;}

uint8_t ti_Open(const char*n,const char*m){
 (void)m;unsigned h=!strcmp(n,"DMOTMAP")?1:!strcmp(n,"DMOTPAL")?2:!strcmp(n,"DMOTRGB")?3:!strcmp(n,"DLUTQ")&&lut_available?4:0;
 if(h){assert(!opened[h]);opened[h]=1;}return h;
}
void ti_Close(uint8_t h){assert(!band_active && opened[h]);opened[h]=0;}
unsigned ti_GetSize(uint8_t h){return h==1?18:h==2?512:h==4?lut_size:32768;}
void *ti_GetDataPtr(uint8_t h){assert(opened[h]);return h==1?(void*)record:h==2?(void*)palette:h==4?(void*)qlut:(void*)nearest;}
unsigned ti_Read(void*out,unsigned a,unsigned b,uint8_t h){memcpy(out,ti_GetDataPtr(h),a*b);return b;}
void gfx_SetPalette(const uint16_t*p,unsigned n,unsigned off){(void)p;(void)n;(void)off;}
void ch5_band_invalidate(void){}
void ch5_band_begin(void){assert(!band_active);band_active=true;begins++;}
void ch5_band_end(void){assert(band_active);band_active=false;ends++;}
const uint8_t *ch5_band_row(uint8_t l,uint16_t y){unsigned w=l<2?320:72;assert(band_active);assert(l<4 && y<(l<2?180:263));if((int)y==fail_row)return NULL;return layers[l]+y*w;}
void ch5_motion_opaque_fast(uint8_t*d,const uint8_t*s,const int16_t*m,size_t n){
 for(size_t i=0;i<n;i++){assert(m[i]>=0);uint8_t c=s[m[i]];if(c!=255)d[i]=c;}
}

#include "ch5_motion_fast.h"
size_t ch5_motion_cached_fast(uint8_t*d,const uint8_t*s,const int16_t*m,size_t n,const uint16_t*t,const uint8_t*c){
 size_t i;for(i=0;i<n;i++){uint8_t f=s[m[i]];if(f==255)continue;uint16_t p=((uint16_t)f<<8)|d[i];unsigned slot=(((unsigned)f<<5)-(unsigned)f+d[i])&4095;if(t[slot]!=p)break;d[i]=c[slot];}return i;}


void ch5_motion_lut_fast(uint8_t*d,const uint8_t*s,const int16_t*m,size_t n,const uint8_t*g,const uint8_t*t){
 for(size_t i=0;i<n;i++){uint8_t c=s[m[i]];if(c!=255)d[i]=t[(unsigned)c*32+g[d[i]]];}
}

void ch5_motion_quant_fast(uint8_t*d,const uint8_t*s,const int16_t*m,unsigned n,const uint8_t*g,const uint8_t*t){
 for(unsigned i=0;i<n;i++){assert(m[i]>=0);uint8_t c=s[m[i]];if(c!=255){unsigned group=(uint8_t)(g[c]-(uint8_t)((uintptr_t)t>>8));assert(group<16);d[i]=t[(group<<8)|d[i]];}}
}

static void direct_quant(uint8_t*d,const uint8_t*s,unsigned n,unsigned repeat,const uint8_t*g,const uint8_t*t){
 uint8_t base=(uint8_t)((uintptr_t)t>>8);
 for(unsigned i=0;i<n;i++)for(unsigned j=0;j<repeat;j++){
  uint8_t c=s[i];if(c!=255){unsigned group=(uint8_t)(g[c]-base);assert(group<16);d[i*repeat+j]=t[group*256+d[i*repeat+j]];}
 }
}
void ch5_motion_quant_direct1(uint8_t*d,const uint8_t*s,unsigned n,const uint8_t*g,const uint8_t*t){direct_quant(d,s,n,1,g,t);}
void ch5_motion_quant_direct2(uint8_t*d,const uint8_t*s,unsigned n,const uint8_t*g,const uint8_t*t){direct_quant(d,s,n,2,g,t);}
void ch5_motion_opaque_direct1(uint8_t*d,const uint8_t*s,unsigned n){for(unsigned i=0;i<n;i++)if(s[i]!=255)d[i]=s[i];}
void ch5_motion_opaque_direct2(uint8_t*d,const uint8_t*s,unsigned n){for(unsigned i=0;i<n;i++)if(s[i]!=255)d[2*i]=d[2*i+1]=s[i];}

void ch5_motion_quant_solid1(uint8_t*d,const uint8_t*s,unsigned n,const uint8_t*g,const uint8_t*t){direct_quant(d,s,n,1,g,t);}
void ch5_motion_quant_solid2(uint8_t*d,const uint8_t*s,unsigned n,const uint8_t*g,const uint8_t*t){direct_quant(d,s,n,2,g,t);}
void ch5_motion_opaque_solid1(uint8_t*d,const uint8_t*s,unsigned n){memcpy(d,s,n);}
void ch5_motion_opaque_solid2(uint8_t*d,const uint8_t*s,unsigned n){for(unsigned i=0;i<n;i++)d[2*i]=d[2*i+1]=s[i];}

uint8_t *resident;

void ch5_fused_bg_0(uint8_t*d,const uint8_t*a,const uint8_t*b,const uint8_t*g,const uint8_t*t){for(unsigned x=0;x<320;x++){uint8_t pg=(uint8_t)(g[b[(x+0)/2]]-(uint8_t)((uintptr_t)t>>8));d[x]=t[((unsigned)pg<<8)|a[(x+0)/2]];}}

void ch5_fused_bg_1(uint8_t*d,const uint8_t*a,const uint8_t*b,const uint8_t*g,const uint8_t*t){for(unsigned x=0;x<320;x++){uint8_t pg=(uint8_t)(g[b[(x+0)/2]]-(uint8_t)((uintptr_t)t>>8));d[x]=t[((unsigned)pg<<8)|a[(x+1)/2]];}}

void ch5_fused_bg_2(uint8_t*d,const uint8_t*a,const uint8_t*b,const uint8_t*g,const uint8_t*t){for(unsigned x=0;x<320;x++){uint8_t pg=(uint8_t)(g[b[(x+1)/2]]-(uint8_t)((uintptr_t)t>>8));d[x]=t[((unsigned)pg<<8)|a[(x+0)/2]];}}

void ch5_fused_bg_3(uint8_t*d,const uint8_t*a,const uint8_t*b,const uint8_t*g,const uint8_t*t){for(unsigned x=0;x<320;x++){uint8_t pg=(uint8_t)(g[b[(x+1)/2]]-(uint8_t)((uintptr_t)t>>8));d[x]=t[((unsigned)pg<<8)|a[(x+1)/2]];}}
#include <time.h>


#include <stdint.h>
void ch5_motion_quant_solid1(uint8_t*,const uint8_t*,unsigned,const uint8_t*,const uint8_t*);
void ch5_motion_quant_solid2(uint8_t*,const uint8_t*,unsigned,const uint8_t*,const uint8_t*);
void ch5_motion_opaque_solid1(uint8_t*,const uint8_t*,unsigned);
void ch5_motion_opaque_solid2(uint8_t*,const uint8_t*,unsigned);
#include <stdint.h>
#include <time.h>

void ch5_motion_expand_fast(uint8_t*,const uint8_t*,unsigned);
#include "ch5_bands.h"
#include "ch5_motion.h"
#include "ch5_motion_fast.h"
#include <fileioc.h>
#include <graphx.h>
#include <time.h>
#include <string.h>
#include <stdlib.h>
#include "perf_memory.h"

void ch5_motion_quant_direct1(uint8_t*,const uint8_t*,unsigned,const uint8_t*,const uint8_t*);
void ch5_motion_quant_direct2(uint8_t*,const uint8_t*,unsigned,const uint8_t*,const uint8_t*);
void ch5_motion_opaque_direct1(uint8_t*,const uint8_t*,unsigned);
void ch5_motion_opaque_direct2(uint8_t*,const uint8_t*,unsigned);
void ch5_fused_bg_0(uint8_t*,const uint8_t*,const uint8_t*,const uint8_t*,const uint8_t*);
void ch5_fused_bg_1(uint8_t*,const uint8_t*,const uint8_t*,const uint8_t*,const uint8_t*);
void ch5_fused_bg_2(uint8_t*,const uint8_t*,const uint8_t*,const uint8_t*,const uint8_t*);
void ch5_fused_bg_3(uint8_t*,const uint8_t*,const uint8_t*,const uint8_t*,const uint8_t*);
static int16_t pair_x[320],pair_y[180];
#include <time.h>


#include <stdint.h>
void ch5_motion_quant_solid1(uint8_t*,const uint8_t*,unsigned,const uint8_t*,const uint8_t*);
void ch5_motion_quant_solid2(uint8_t*,const uint8_t*,unsigned,const uint8_t*,const uint8_t*);
void ch5_motion_opaque_solid2(uint8_t*,const uint8_t*,unsigned);
#include <stdint.h>
#include <time.h>

#include "ch5_bands.h"
#include "ch5_motion.h"
#include "ch5_motion_fast.h"
#include <fileioc.h>
#include <graphx.h>
#include <time.h>
#include <string.h>
#include <stdlib.h>
#include "perf_memory.h"

void ch5_motion_quant_direct1(uint8_t*,const uint8_t*,unsigned,const uint8_t*,const uint8_t*);
void ch5_motion_quant_direct2(uint8_t*,const uint8_t*,unsigned,const uint8_t*,const uint8_t*);
void ch5_motion_opaque_direct1(uint8_t*,const uint8_t*,unsigned);
void ch5_motion_opaque_direct2(uint8_t*,const uint8_t*,unsigned);
void ch5_fused_bg_0(uint8_t*,const uint8_t*,const uint8_t*,const uint8_t*,const uint8_t*);
void ch5_fused_bg_1(uint8_t*,const uint8_t*,const uint8_t*,const uint8_t*,const uint8_t*);
void ch5_fused_bg_2(uint8_t*,const uint8_t*,const uint8_t*,const uint8_t*,const uint8_t*);
void ch5_fused_bg_3(uint8_t*,const uint8_t*,const uint8_t*,const uint8_t*,const uint8_t*);
static int16_t pair_x[320],pair_y[180];
