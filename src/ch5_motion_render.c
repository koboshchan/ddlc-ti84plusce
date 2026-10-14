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
static int16_t xmap[320],ymap[180];
static uint8_t last_scene=255;
static uint8_t quant_groups[256];
void ch5_motion_quant_fast(uint8_t*,const uint8_t*,const int16_t*,unsigned,const uint8_t*,const uint8_t*);
static clock_t started;
static clock_t layer_start[8];
static uint8_t previous_mode[8]={255,255,255,255,255,255,255,255};
/* Zoomed motion modes are exactly 1x/2x, so the million-unit scale
 * cancels before flooring. Keep general authored zooms on the wide path. */
static void motion_axis_map(int16_t *map,unsigned count,int32_t origin,uint32_t scale,unsigned extent){
 if(scale==1000000u || scale==2000000u){
   unsigned shift=scale==1000000u?10:11;
   int32_t start=(-origin)>>shift;
   unsigned parity=((uint32_t)(-origin)>>10)&1u;
   if(scale==1000000u){
    for(unsigned j=0;j<count;j++){
     int32_t source=start+j;
     map[j]=(source<0||(uint32_t)source>=extent)?-1:(int16_t)source;
    }
   }else{
    for(unsigned j=0;j<count;j++){
     int32_t source=start+((j+parity)>>1);
     map[j]=(source<0||(uint32_t)source>=extent)?-1:(int16_t)source;
    }
   }
   return;
 }
 int64_t den=(int64_t)scale*1024,num=-(int64_t)origin*1000000;
 int32_t source=ch5_floor_div(num,den);
 int64_t rem=num-(int64_t)source*den;
 int64_t step=1024LL*1000000;
 int32_t delta=step/den;
 int64_t dr=step-(int64_t)delta*den;
 for(unsigned j=0;j<count;j++){
  map[j]=(source<0||(uint32_t)source>=extent)?-1:(int16_t)source;
  source+=delta;rem+=dr;
  if(rem>=den){rem-=den;source++;}
 }
}
bool ch5_motion_active(uint8_t scene){
 char name[]="DMOTMAP"; uint8_t h=ti_Open(name,"r");bool active=false;
 if(h){const uint8_t *p=ti_GetDataPtr(h);unsigned n=ti_GetSize(h),i;
 for(i=0;i+18<=n;i+=18)if(p[i]==scene){active=true;break;}ti_Close(h);}
 return active;
}
/* Clock lifetime is independent of framebuffer drawing. A successful USB
 * frame must advance the same scene/layer clocks as the on-calc renderer. */
bool ch5_motion_sync_clock(uint8_t scene){
 uint8_t h=ti_Open("DMOTMAP","r");if(!h)return false;
 const uint8_t *p=ti_GetDataPtr(h);unsigned n=ti_GetSize(h),i,j;
 clock_t now=clock();bool found=false;
 for(i=0;i+18<=n;i+=18)if(p[i]==scene){
  if(last_scene!=scene){last_scene=scene;started=now;ch5_band_invalidate();}
  for(j=0;j<p[i+1] && j<8;j++){
   uint8_t mode=p[i+3+j*2];
   if(previous_mode[j]!=mode){previous_mode[j]=mode;layer_start[j]=now;}
  }
  found=true;break;
 }
 ti_Close(h);return found;
}
bool ch5_motion_draw(uint8_t scene){
 uint8_t record[18],h,blend_handle,lut_handle=0;unsigned i,n,layer,mode,x,y;uint16_t palette[256];bool found=false;
 h=ti_Open("DMOTMAP","r");if(!h)return false;
 const uint8_t *p=ti_GetDataPtr(h);n=ti_GetSize(h);
 for(i=0;i+18<=n;i+=18)if(p[i]==scene){memcpy(record,p+i,18);found=true;break;}
 ti_Close(h);if(!found){last_scene=255;return false;}
 if(record[1]>8)return false;
 h=ti_Open("DMOTPAL","r");if(!h)return false;
 if(ti_GetSize(h)!=512){ti_Close(h);return false;}
 ti_Read(palette,1,512,h);ti_Close(h);
 if(!ch5_motion_sync_clock(scene))return false;

 blend_handle=ti_Open("DMOTRGB","r");
 if(!blend_handle)return false;
 if(ti_GetSize(blend_handle)!=32768u){ti_Close(blend_handle);return false;}
 const uint8_t *nearest=ti_GetDataPtr(blend_handle);
 gfx_SetPalette(palette,512,0);
 uint8_t qh=ti_Open("DLUTQ","r");if(!qh){ti_Close(blend_handle);return false;}
 if(ti_GetSize(qh)!=12560){ti_Close(qh);ti_Close(blend_handle);return false;}
 const uint8_t *qdata=ti_GetDataPtr(qh);memcpy(quant_groups,qdata,256);
 for(unsigned q=0;q<256;q++)if(quant_groups[q]>=16){ti_Close(qh);ti_Close(blend_handle);return false;}

 ti_Close(qh);

 uint8_t bg_groups[256];bool have_groups=false;
 /* Benchmark-only opt-in; installed LUTs cannot affect default output. */
#ifdef DDLC_EXPERIMENTAL_MOTION_LUT
 h=ti_Open("DMOTBGM","r");
 if(h){
  if(ti_GetSize(h)==256){
   ti_Read(bg_groups,1,256,h);have_groups=true;
   for(unsigned g=0;g<256;g++)if(bg_groups[g]>=32){have_groups=false;break;}
  }
  ti_Close(h);
 }
#endif
 uint8_t *lookup_alloc=malloc(4096+256+4095);
 if(!lookup_alloc){ti_Close(blend_handle);return false;}
 uint8_t *lookup_aligned=(uint8_t*)(((uintptr_t)lookup_alloc+4095u)&~(uintptr_t)4095u);
 bool success=false;
 /* No variable mutation/GC inside this scope; clear borrowed pointers
  * before closing the nearest-color AppVar on every exit. */
 ch5_band_begin();
 /* Validate each stream before modifying frame. Existing scene is fallback. */
 for(i=0;i<record[1];i++)if(!ch5_band_row(record[2+i*2],0)){goto motion_done;}
 
 unsigned first_layer=0;
 /* Authored bg_zoom has 2x coverage for every permitted phase. Never
  * skip for other modes or assets containing transparent pixels. */
 if(record[1]>1 && record[4]==0 && record[5]==3){
  bool solid=true;
  for(unsigned row=26;row<=116;row++){
   const uint8_t *pixels=ch5_band_row(0,row);
   if(!pixels){goto motion_done;}
   if(memchr(pixels+31,255,161)){solid=false;break;}
  }
  if(solid)first_layer=1;
 }

 /* Proven full-screen opaque mode-3 coverage overwrites every pixel. */
 if(!first_layer)memset(gfx_vbuffer,0,320*180);
 for(i=first_layer;i<record[1];i++){
  layer=record[2+i*2];mode=record[3+i*2];
  unsigned tw=layer<2?320:72,th=layer<2?180:263;
  unsigned layer_ms=(unsigned)(((uint64_t)(clock()-layer_start[i])*1000ULL)/CLOCKS_PER_SEC);
  ch5_motion_t pose=ch5_motion(layer_ms,mode,layer<2?1280:286,layer<2?720:1050);
  motion_axis_map(xmap,320,pose.x,pose.zoom_q10,tw);
  motion_axis_map(ymap,180,pose.y,pose.zoom_q10,th);
  if(!pose.alpha_q10)continue;
  const bool opaque=pose.alpha_q10==1024;
  uint8_t *ramlut=lookup_aligned;
  uint8_t *pages=ramlut+4096;
  uint8_t page_base=(uint8_t)((uintptr_t)ramlut>>8);
  if(!opaque)for(unsigned pg=0;pg<256;pg++)pages[pg]=page_base+quant_groups[pg];

  if(!opaque)memcpy(ramlut,qdata+272+(pose.alpha_q10<352?0:pose.alpha_q10<544?4096:8192),4096);
  unsigned first=0,end=320;
  while(first<end && xmap[first]<0)first++;
  while(end>first && xmap[end-1]<0)end--;
  unsigned span=end-first;if(!span)continue;bool linear=true,doubled=true,odd_doubled=true;
  if(pose.zoom_q10==1000000u){linear=true;doubled=false;odd_doubled=false;}
  else if(pose.zoom_q10==2000000u){
   linear=false;
   doubled=xmap[first+(span>1)]==xmap[first];
   odd_doubled=!doubled;
  }else{
  for(unsigned k=0;k<span;k++){if(xmap[first+k]!=xmap[first]+k)linear=false;if(xmap[first+k]!=xmap[first]+k/2)doubled=false;if(xmap[first+k]!=xmap[first]+(k+1)/2)odd_doubled=false;}
  }
  int16_t prior_source=-1;const uint8_t *prior_row=NULL;
  for(y=0;y<180;y++){
   if(ymap[y]<0)continue;
   const uint8_t *row=ymap[y]==prior_source?prior_row:ch5_band_row(layer,ymap[y]);if(!row){goto motion_done;}
   prior_source=ymap[y];prior_row=row;
   uint8_t *dst=(uint8_t *)gfx_vbuffer+y*320;
   /* Never assume packaged backgrounds are opaque. Scan the decoded row;
    * unusual transparent assets retain the original guarded kernels. */
   if(layer<2 && !memchr(row,255,tw)){
   if(opaque){
    if(linear)memcpy(dst+first,row+xmap[first],span);
    else if(odd_doubled && !doubled && span>=3){
     ch5_motion_opaque_solid1(dst+first,row+xmap[first],1);
     unsigned remaining=span-1,even=remaining&~1u;
     ch5_motion_opaque_solid2(dst+first+1,row+xmap[first+1],even/2);
     if(remaining!=even)ch5_motion_opaque_solid1(dst+first+1+even,row+xmap[first+1+even],1);
    }else if(doubled && span>=2){
     unsigned even=span&~1u;
     ch5_motion_opaque_solid2(dst+first,row+xmap[first],even/2);
     if(span!=even)ch5_motion_opaque_solid1(dst+first+even,row+xmap[first+even],1);
    }else ch5_motion_opaque_fast(dst+first,row,xmap+first,span);
   }else{
    if(linear)ch5_motion_quant_solid1(dst+first,row+xmap[first],span,pages,ramlut);
    else if(odd_doubled && !doubled && span>=3){
     ch5_motion_quant_solid1(dst+first,row+xmap[first],1,pages,ramlut);
     unsigned remaining=span-1,even=remaining&~1u;
     ch5_motion_quant_solid2(dst+first+1,row+xmap[first+1],even/2,pages,ramlut);
     if(remaining!=even)ch5_motion_quant_solid1(dst+first+1+even,row+xmap[first+1+even],1,pages,ramlut);
    }else if(doubled && span>=2){
     unsigned even=span&~1u;
     ch5_motion_quant_solid2(dst+first,row+xmap[first],even/2,pages,ramlut);
     if(span!=even)ch5_motion_quant_fast(dst+first+even,row,xmap+first+even,1,pages,ramlut);
    }else ch5_motion_quant_fast(dst+first,row,xmap+first,span,pages,ramlut);
   }
   }else{
   if(opaque){
    if(linear)ch5_motion_opaque_direct1(dst+first,row+xmap[first],span);
    else if(odd_doubled && !doubled && span>=3){
     ch5_motion_opaque_direct1(dst+first,row+xmap[first],1);
     unsigned remaining=span-1,even=remaining&~1u;
     ch5_motion_opaque_direct2(dst+first+1,row+xmap[first+1],even/2);
     if(remaining!=even)ch5_motion_opaque_direct1(dst+first+1+even,row+xmap[first+1+even],1);
    }else if(doubled && span>=2){
     unsigned even=span&~1u;
     ch5_motion_opaque_direct2(dst+first,row+xmap[first],even/2);
     if(span!=even)ch5_motion_opaque_direct1(dst+first+even,row+xmap[first+even],1);
    }else ch5_motion_opaque_fast(dst+first,row,xmap+first,span);
   }else{
    if(linear)ch5_motion_quant_direct1(dst+first,row+xmap[first],span,pages,ramlut);
    else if(odd_doubled && !doubled && span>=3){
     ch5_motion_quant_direct1(dst+first,row+xmap[first],1,pages,ramlut);
     unsigned remaining=span-1,even=remaining&~1u;
     ch5_motion_quant_direct2(dst+first+1,row+xmap[first+1],even/2,pages,ramlut);
     if(remaining!=even)ch5_motion_quant_direct1(dst+first+1+even,row+xmap[first+1+even],1,pages,ramlut);
    }else if(doubled && span>=2){
     unsigned even=span&~1u;
     ch5_motion_quant_direct2(dst+first,row+xmap[first],even/2,pages,ramlut);
     if(span!=even)ch5_motion_quant_fast(dst+first+even,row,xmap+first+even,1,pages,ramlut);
    }else ch5_motion_quant_fast(dst+first,row,xmap+first,span,pages,ramlut);
   }
   }
  }
  
 }
 success=true;
motion_done:
 ch5_band_end();
 free(lookup_alloc);
 if(lut_handle)ti_Close(lut_handle);
 ti_Close(blend_handle);
 /* Bottom rows are transient work space, not scene pixels. Every caller
  * must compose the dialogue/letterbox after this draw and before present. */
 return success;
}

uint32_t ch5_motion_elapsed_ms(uint8_t scene) {
 if(last_scene!=scene)return 0;
 return (uint32_t)(((uint64_t)(clock()-started)*1000ULL)/CLOCKS_PER_SEC);
}
