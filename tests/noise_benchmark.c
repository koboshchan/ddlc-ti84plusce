#include <tice.h>
#include <graphx.h>
#include <fileioc.h>
#include <time.h>
#include <stdint.h>
#include <string.h>
#include "../src/noise.c"
typedef struct {uint32_t t,begin_ticks,frame_ticks,restore_ticks,undo_n,errors;} result_t;
static result_t results[41];
int main(void){
 gfx_Begin();gfx_SetDrawBuffer();
 for(unsigned i=0;i<256;i++)gfx_palette[i]=(i&31)*1057u;
 for(unsigned j=0;j<41;j++){
  uint8_t*fb=(uint8_t*)gfx_vbuffer;
  for(unsigned i=0;i<76800;i++)fb[i]=(uint8_t)(i*71u);
  result_t*r=&results[j];r->t=j*100u;
  clock_t a=clock();if(!noise_begin(2)){gfx_End();return 1;}
  clock_t b=clock();noise_frame(r->t);clock_t c=clock();
  r->undo_n=undo_size;
  unsigned seed=(0xDD1Cu^(r->t/100u)*40503u)&65535u;if(!seed)seed=1;
  unsigned cov=r->t>=3000?4u:(uint32_t)r->t*4u/3000u;
  for(unsigned yy=0;yy<240;yy+=2)for(unsigned xx=0;xx<320;xx+=2){
   seed=(seed>>1)^((0u-(seed&1u))&0xB400u);
   unsigned color=colors[meta[256+(yy/20u)*16u+xx/20u]+((seed>>4)&16u)];
   for(unsigned dy=0;dy<2;dy++)for(unsigned dx=0;dx<2;dx++){
    unsigned pos=(yy+dy)*320u+xx+dx;
    uint8_t expected=(seed&15u)<cov?color:(uint8_t)(pos*71u);
    if(fb[pos]!=expected)r->errors++;
   }
  }
  clock_t restore_a=clock();noise_restore();clock_t e=clock();noise_end();
  r->begin_ticks=b-a;r->frame_ticks=c-b;r->restore_ticks=e-restore_a;
  for(unsigned i=0;i<76800;i++)if(fb[i]!=(uint8_t)(i*71u))r->errors++;
 }
 noise_release();gfx_End();
 uint8_t f=ti_Open("NPRESULT","w");if(f){ti_Write(results,sizeof(results),1,f);ti_Close(f);}
 os_ClrHome();os_PutStrFull("Noise48 benchmark done");
 while(!os_GetCSC());return 0;
}
