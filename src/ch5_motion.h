#ifndef CH5_MOTION_H
#define CH5_MOTION_H
#include <stdint.h>
/* Authored viewport pixels in Q8; source cgs.rpy 231-311.
 * Sampling quantized to 250ms (4fps), no accumulated delta or frame assets. */
typedef struct { int32_t x,y; uint32_t zoom_q10; uint16_t alpha_q10; } ch5_motion_t;
/* Authored RenPy cosine warpers evaluated at the permitted 250ms steps.
 * Each offset rounded ONCE to Q8, background amplitude before rounding.
 * Generator/proof under ti/opt/independent-motion; no coarse ease lookup. */
static const int16_t ch5_x_fg[24]={0,640,1920,2560,2240,1600,1280,640,-640,-1280,-1152,-896,-768,-1216,-2112,-2560,-1920,-640,0,320,960,1280,960,320};
static const int16_t ch5_x_bg[24]={0,160,480,640,560,400,320,160,-160,-320,-288,-224,-192,-304,-528,-640,-480,-160,0,80,240,320,240,80};
static const int16_t ch5_y_fg[16]={0,187,640,1093,1280,1183,905,490,0,-490,-905,-1183,-1280,-790,-375,-97};
static const int16_t ch5_y_bg[16]={0,47,160,273,320,296,226,122,0,-122,-226,-296,-320,-198,-94,-24};
static ch5_motion_t ch5_motion(unsigned ms,unsigned mode,unsigned width,unsigned height){
 static const uint8_t bg_alpha[10]={20,20,25,20,30,25,35,30,35,20};
 static const uint8_t fg_alpha[10]={30,30,40,30,50,40,60,50,60,40};
 ch5_motion_t p; unsigned t=(ms/250u)*250u;
 unsigned xa=0,ya=0; p.zoom_q10=1000000;p.alpha_q10=1024;p.x=p.y=0;
 /* modes 1 bg_start, 2 fg_start, 3 bg_zoom, 4 fg_zoom,
  * 5 bg2_zoom, 6 fg2_zoom. Align uses viewport minus scaled dimensions. */
 if(mode==1){unsigned q=t>4000?4000:t;p.zoom_q10=1100000-25*q;xa=ya=500000;}
 if(mode==2){unsigned q=t>4000?4000:t;p.zoom_q10=800000-(uint64_t)50000*q/4000;xa=300000+(uint64_t)15000*q/4000;ya=250000;p.y=(int32_t)10*256*q/4000;}
 if(mode>=3){p.zoom_q10=2000000;xa=(mode==3||mode==5)?200000:500000;ya=(mode==3||mode==5)?300000:50000;}
 p.x=((int64_t)1280*1000000-(int64_t)width*p.zoom_q10)*xa*256/1000000000000LL;
 p.y+=((int64_t)720*1000000-(int64_t)height*p.zoom_q10)*ya*256/1000000000000LL;
 if(mode>=3){
 unsigned bg=mode==3||mode==5;
 int32_t dx=(bg?ch5_x_bg:ch5_x_fg)[(t%6000u)/250u];
 int32_t dy=(bg?ch5_y_bg:ch5_y_fg)[(t%4000u)/250u];
 p.x+=dx;p.y+=dy;
 }
 if(mode==5||mode==6){
 unsigned at=ms%2250u,seg=at/250u,phase=at%250u;
 const uint8_t *a=mode==5?bg_alpha:fg_alpha;
 /* Exact authored decimal endpoints and linear phase, rounded once to Q10.
  * 25000 denominator is 250ms * 100 percent, never native-24 overflow. */
 uint32_t numerator=(uint32_t)a[seg]*(250u-phase)+(uint32_t)a[seg+1]*phase;
 p.alpha_q10=(numerator*1024UL+12500UL)/25000UL;
 }
 return p;
}
/* Source viewport Q8 -> quarter-resolution texture. Signed division
 * floors explicitly; C truncation at the left/top edge must not admit -1. */
static int32_t ch5_floor_div(int64_t n,int32_t d){
 return n>=0?n/d:-((-n+d-1)/d);
}
static int ch5_source_pixel(int dx,int dy,ch5_motion_t p,unsigned tw,unsigned th,
                           unsigned *sx,unsigned *sy){
 if(!p.zoom_q10)return 0;
 int64_t nx=((int64_t)dx*4*256-p.x)*1000000;
 int64_t ny=((int64_t)dy*4*256-p.y)*1000000;
 int32_t x=ch5_floor_div(nx,(int32_t)p.zoom_q10*1024);
 int32_t y=ch5_floor_div(ny,(int32_t)p.zoom_q10*1024);
 if(x<0||y<0||(uint32_t)x>=tw||(uint32_t)y>=th)return 0;
 *sx=(unsigned)x;*sy=(unsigned)y;return 1;
}

/* No division in rendering loop. Two 320/180-entry int16 lookups (1000B).
 * Wide source math occurs only while constructing these per-frame tables.
 * -1 denotes transparent/outside; clipping precedes all texture reads. */
static void ch5_axis_map(int16_t *map,unsigned count,int32_t origin,
                         uint32_t scale,unsigned texture_extent){
 unsigned i;
 int64_t denominator=(int64_t)scale*1024;
 int64_t numerator=-(int64_t)origin*1000000;
 int64_t step=1024LL*1000000;
 int32_t source=ch5_floor_div(numerator,denominator);
 int64_t remainder=numerator-(int64_t)source*denominator;
 int32_t delta=step/denominator;
 int64_t delta_remainder=step-(int64_t)delta*denominator;
 for(i=0;i<count;i++){
   map[i]=(source<0||(uint32_t)source>=texture_extent)?-1:(int16_t)source;
   source+=delta;remainder+=delta_remainder;
   if(remainder>=denominator){remainder-=denominator;source++;}
 }
}
#endif
