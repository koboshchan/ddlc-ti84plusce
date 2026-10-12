#include <graphx.h>
#include <string.h>
#include <stdio.h>
#include <stdint.h>
#include "fast_ops.h"
static uint8_t src[700],dst[704],saved[320],scratch[320],sp[64];
int main(void) {
 gfx_Begin();gfx_SetDefaultPalette(gfx_8bpp);gfx_palette[1]=0xFFFF;
 gfx_SetDrawScreen();
 gfx_FillScreen(0);gfx_SetTextFGColor(1);gfx_PrintStringXY("start",10,10);
 unsigned fail=0,n=0;
 const unsigned widths[]={0,1,255,256,320};
 for(unsigned wi=0;wi<5;wi++){
  unsigned w=widths[wi];
  for(unsigned mode=0;mode<3;mode++){
   unsigned step=mode==0?7:mode==1?320:640,div=320,acc=117;
   for(unsigned j=0;j<700;j++)src[j]=(j%3==0)?0:(j%249+1);
   memset(dst,255,sizeof dst);
   fast_scale_row_trans(dst+1,src,w,acc,step,div);
   unsigned sx=0;bool ok=true;
   for(unsigned x=0;x<w;x++){
    if(dst[x+1]!=(src[sx]?src[sx]:255))ok=false;
    acc+=step;while(acc>=div){acc-=div;sx++;}
   }
   if(dst[0]!=255||dst[w+1]!=255)ok=false;
   fail+=!ok;n++;
  }
 }
 gfx_PrintStringXY("row done",10,30);
 for(unsigned wi=0;wi<5;wi++){
  unsigned w=widths[wi],sx=0,ax=0;memset(dst,255,sizeof dst);
  fast_zoom_row(dst+1,src,w);bool ok=true;
  for(unsigned x=0;x<w;x++){if(dst[x+1]!=src[sx])ok=false;ax+=20;if(ax>=21){ax-=21;sx++;}}
  if(dst[0]!=255||dst[w+1]!=255)ok=false;fail+=!ok;n++;
 }
 gfx_PrintStringXY("zoom done",10,50);
 for(unsigned off=0;off<320;off++){
  for(unsigned i=0;i<320;i++)saved[i]=(uint8_t)i;
  memcpy(dst+1,saved,320);dst[0]=255;dst[321]=255;
  fast_row_shift(dst+1,scratch,off);bool ok=true;
  for(unsigned i=0;i<320;i++)if(dst[i+1]!=saved[(i+320-off)%320])ok=false;
  if(dst[0]!=255||dst[321]!=255)ok=false;fail+=!ok;n++;
 }
 gfx_PrintStringXY("shift done",10,70);
 memset(dst,255,sizeof dst);fast_rect_blit(dst+1,8,src,7,0,4);fast_rect_blit(dst+1,8,src,7,7,0);
 fail+=dst[1]!=255;n++;
 fast_rect_blit(dst+1,2,src,1,1,256);bool ok=true;
 for(unsigned y=0;y<256;y++)if(dst[1+y*2]!=src[y]||dst[2+y*2]!=255)ok=false;
 fail+=!ok;n++;
 gfx_PrintStringXY("rect done",10,90);
 for(unsigned mode=0;mode<2;mode++){
  unsigned sw=7,sh=5,dw=mode?10:3,dh=mode?8:2;
  for(unsigned i=0;i<35;i++)sp[i]=i%3?i+1:0;
  memset(dst,255,sizeof dst);fast_scale_sprite_trans(dst+1,12,sp,sw,sh,dw,dh);ok=true;
  for(unsigned y=0;y<dh;y++)for(unsigned x=0;x<dw;x++){
   unsigned sx=(x*sw+dw/2)/dw,sy=(y*sh+dh/2)/dh;
   uint8_t v=sp[sy*sw+sx];if(dst[1+y*12+x]!=(v?v:255))ok=false;
  }
  if(dst[0]!=255)ok=false;fail+=!ok;n++;
 }
 gfx_FillScreen(0);gfx_SetTextFGColor(1);char msg[60];
 sprintf(msg,"%u checks, %u failures",n,fail);gfx_PrintStringXY(msg,10,20);

 while(1) gfx_Wait();
}
