#include "ch5_bands.h"
#include "ch5_motion.h"
#include "ch5_motion_fast.h"
#include <fileioc.h>
#include <graphx.h>
#include <time.h>
#include <string.h>
static int16_t xmap[320],ymap[180];
static uint8_t last_scene=255;
static clock_t started;
static clock_t layer_start[8];
static uint8_t previous_mode[8]={255,255,255,255,255,255,255,255};
/* Zoomed motion modes are exactly 1x/2x, so the million-unit scale
 * cancels before flooring. Keep general authored zooms on the wide path. */
static void motion_axis_map(int16_t *map,unsigned count,int32_t origin,
                            uint32_t scale,unsigned extent){
 if(scale!=1000000UL && scale!=2000000UL){
  ch5_axis_map(map,count,origin,scale,extent);return;
 }
 int32_t denominator=scale==1000000UL?1024:2048;
 int32_t numerator=-origin;
 int32_t source=numerator>=0?numerator/denominator:
                    -((-numerator+denominator-1)/denominator);
 unsigned remainder=numerator-source*denominator;
 for(unsigned j=0;j<count;j++){
  map[j]=(source<0 || (uint32_t)source>=extent)?-1:(int16_t)source;
  remainder+=1024;
  if(remainder>=(unsigned)denominator){remainder-=denominator;source++;}
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
 bool success=false;
 /* No variable mutation/GC inside this scope; clear borrowed pointers
  * before closing the nearest-color AppVar on every exit. */
 ch5_band_begin();
 /* Validate each stream before modifying frame. Existing scene is fallback. */
 for(i=0;i<record[1];i++)if(!ch5_band_row(record[2+i*2],0)){goto motion_done;}
 memset(gfx_vbuffer,0,320*180);
 for(i=0;i<record[1];i++){
  layer=record[2+i*2];mode=record[3+i*2];
  unsigned tw=layer<2?320:72,th=layer<2?180:263;
  unsigned layer_ms=(unsigned)(((uint64_t)(clock()-layer_start[i])*1000ULL)/CLOCKS_PER_SEC);
  ch5_motion_t pose=ch5_motion(layer_ms,mode,layer<2?1280:286,layer<2?720:1050);
  motion_axis_map(xmap,320,pose.x,pose.zoom_q10,tw);
  motion_axis_map(ymap,180,pose.y,pose.zoom_q10,th);
  if(!pose.alpha_q10)continue;
  const uint8_t *blend_lut=0;
  if(have_groups && pose.alpha_q10!=1024){
   unsigned level=(pose.alpha_q10*15u+512u)/1024u;
   char lut_name[]="DMLUT00";
   lut_name[5]='0'+level/10;lut_name[6]='0'+level%10;
   lut_handle=ti_Open(lut_name,"r");
   if(lut_handle){
    if(ti_GetSize(lut_handle)==8192)blend_lut=ti_GetDataPtr(lut_handle);
    else{ti_Close(lut_handle);lut_handle=0;}
   }
  }
  /* Exact channel contributions. 128 bytes bounded stack workspace, not a
   * per-pixel multiplication or a heap allocation at lifecycle peak. */
  uint16_t fg_weight[32],bg_weight[32];
  /* Exact direct-mapped pair cache lives only in the hidden dialogue rows.
   * No heap/BSS cost; tags are invalidated for every alpha/layer. fg 255
   * is transparent, so the all-ones tag is an impossible valid pair. */
  uint8_t *scratch=(uint8_t *)gfx_vbuffer+320u*180u;
  uint16_t *pair_tags=(uint16_t *)scratch;
  uint8_t *pair_colors=scratch+8192;
  if(pose.alpha_q10!=1024 && !blend_lut)memset(pair_tags,255,8192);
  const bool opaque=pose.alpha_q10==1024;
  if(!opaque && !blend_lut)for(unsigned c=0;c<32;c++){
   fg_weight[c]=c*pose.alpha_q10;
   bg_weight[c]=c*(1024u-pose.alpha_q10);
  }
  unsigned first=0,end=320;
  while(first<end && xmap[first]<0)first++;
  while(end>first && xmap[end-1]<0)end--;
  uint16_t previous_pair=0;uint8_t previous_color=0;bool pair_valid=false;
  for(y=0;y<180;y++){
   if(ymap[y]<0)continue;
   const uint8_t *row=ch5_band_row(layer,ymap[y]);if(!row){goto motion_done;}
   uint8_t *dst=(uint8_t *)gfx_vbuffer+y*320;
   if(opaque){
    ch5_motion_opaque_fast(dst+first,row,xmap+first,end-first);
   }else if(blend_lut){
    ch5_motion_lut_fast(dst+first,row,xmap+first,end-first,bg_groups,blend_lut);
   }else{
    for(x=first;x<end;x++){
     uint8_t color=row[xmap[x]];
     if(color==255)continue;
     uint8_t bg_index=dst[x];
     uint16_t pair=((uint16_t)color<<8)|bg_index;
     if(pair_valid && pair==previous_pair){dst[x]=previous_color;continue;}
     unsigned slot=(((unsigned)color<<4)^bg_index)&4095u;
     if(pair_tags[slot]==pair){
      previous_color=pair_colors[slot];
      previous_pair=pair;pair_valid=true;dst[x]=previous_color;continue;
     }
     unsigned fg=palette[color],bg=palette[bg_index];
     unsigned rr=(fg_weight[(fg>>10)&31u]+bg_weight[(bg>>10)&31u]+512u)>>10;
     unsigned gg=(fg_weight[(fg>>5)&31u]+bg_weight[(bg>>5)&31u]+512u)>>10;
     unsigned bb=(fg_weight[fg&31u]+bg_weight[bg&31u]+512u)>>10;
     previous_color=nearest[(rr<<10)|(gg<<5)|bb];
     pair_tags[slot]=pair;pair_colors[slot]=previous_color;
     previous_pair=pair;pair_valid=true;dst[x]=previous_color;
    }
   }
  }
  if(lut_handle){ti_Close(lut_handle);lut_handle=0;}
 }
 success=true;
motion_done:
 ch5_band_end();
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
