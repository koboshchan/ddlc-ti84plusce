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
 for(unsigned wi=0;wi<5;wi++){
  unsigned dw=widths[wi],sw=dw>20?(dw*3)/4:(dw?1:0),sx=0,acc=0;
  for(unsigned j=0;j<320;j++)src[j]=(uint8_t)((j*37+1)&0xFF);
  memset(dst,255,sizeof dst);
  fast_stretch_row(dst+1,src,dw,sw);
  ok=true;
  if(sw>0&&dw>0){
   for(unsigned x=0;x<dw;x++){
    if(dst[x+1]!=src[sx])ok=false;
    acc+=sw;while(acc>=dw){acc-=dw;sx++;}
   }
  }
  if(dst[0]!=255||dst[dw+1]!=255)ok=false;
  fail+=!ok;n++;
 }
 {
  gfx_SetDrawScreen();
  uint8_t *bsrc=(uint8_t *)gfx_vbuffer;
  gfx_SetDrawBuffer();
  uint8_t *bdst=(uint8_t *)gfx_vbuffer;
  for(unsigned y=0;y<180;y++)for(unsigned x=0;x<320;x++){
   bsrc[y*320+x]=(uint8_t)((x+y)%250+1);bdst[y*320+x]=0;
  }
  fast_bayer_dissolve(bdst,bsrc,1);ok=true;
  for(unsigned x=0;x<320;x++){
   if((x&1)==0&&bdst[x]!=bsrc[x])ok=false;
   if((x&1)!=0&&bdst[x]!=0)ok=false;
   if((x&1)!=0&&bdst[320+x]!=bsrc[320+x])ok=false;
   if((x&1)==0&&bdst[320+x]!=0)ok=false;
  }
  fail+=!ok;n++;
  {
   static const uint8_t zx0_solid[8]={0x81,0x0a,0x11,0x55,0x55,0x75,0x55,0x58};
   memset(bdst,0xEE,76800);
   fast_zx0_decompress(bdst,zx0_solid);
   ok=true;
   for(unsigned i=0;i<76800;i++)if(bdst[i]!=0x0a){ok=false;break;}
   fail+=!ok;n++;
  }
  {
   static const uint8_t zx0_mix[132]={
    0x00,0x18,0x0d,0x32,0x57,0x7c,0xa1,0xc6,0xeb,0x10,0x35,0x5a,0x7f,0xa4,0xc9,0xee,
    0x13,0x38,0x5d,0x82,0xa7,0xcc,0xf1,0x16,0x3b,0x60,0x85,0xaa,0xcf,0xf4,0x19,0x3e,
    0x63,0x88,0xad,0xd2,0xf7,0x1c,0x41,0x66,0x8b,0xb0,0xd5,0xfa,0x1f,0x44,0x69,0x8e,
    0xb3,0xd8,0xfd,0x22,0x47,0x6c,0x91,0xb6,0xdb,0x00,0x25,0x4a,0x6f,0x94,0xb9,0xde,
    0x03,0x28,0xab,0x15,0xe5,0x44,0x08,0x00,0xdc,0x05,0x18,0x2b,0x3e,0x51,0x64,0x77,
    0x8a,0x9d,0xb0,0xc3,0xd6,0xe9,0xfc,0x0f,0x22,0x35,0x48,0x5b,0x6e,0x81,0x94,0xa7,
    0xba,0xcd,0xe0,0xf3,0x06,0x19,0x2c,0x3f,0x52,0x96,0x05,0x9f,0x01,0x02,0x03,0xfa,
    0x40,0x95,0xd5,0xc8,0xc9,0xca,0xcb,0xcc,0xcd,0xce,0xcf,0xd0,0xd1,0xd2,0xd3,0xd4,
    0xd5,0xd6,0x55,0x60
   };
   unsigned p=0;
   for(unsigned i=0;i<64;i++)src[p++]=(uint8_t)((i*37+13)&0xFF);
   for(unsigned i=0;i<40;i++)src[p++]=0xAB;
   for(unsigned i=0;i<45;i++)src[p++]=src[10+i];
   for(unsigned i=0;i<32;i++)src[p++]=(uint8_t)((i*19+5)&0xFF);
   for(unsigned i=0;i<100;i++)src[p++]=src[i];
   for(unsigned i=0;i<60;i++)src[p++]=(uint8_t)((i%3)+1);
   for(unsigned i=0;i<15;i++)src[p++]=(uint8_t)(200+i);
   memset(dst,0xEE,sizeof dst);
   fast_zx0_decompress(dst+1,zx0_mix);
   ok=(dst[0]==0xEE&&dst[p+1]==0xEE&&memcmp(dst+1,src,p)==0);
   fail+=!ok;n++;
  }
  gfx_SetDrawScreen();
 }
 gfx_FillScreen(0);gfx_SetTextFGColor(1);char msg[60];
 sprintf(msg,"%u checks, %u failures",n,fail);gfx_PrintStringXY(msg,10,20);

 while(1) gfx_Wait();
}
