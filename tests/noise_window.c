
#include "../src/noise_window.h"
#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
int main(void) {
 uint8_t *a=malloc(NOISE_HALF_BYTES),*b=malloc(NOISE_HALF_BYTES);
 uint32_t s=42,t=42; unsigned i,x,y,r,c;
 for(i=0;i<NOISE_HALF_BYTES;i++){a[i]=(i*29u+17u)&255;b[i]=(i*31u+3u)&255;}
 for(i=0;i<100000;i++) assert(noise_next(&s)==noise_next(&t));

 assert(!noise_origin_valid(321,0));assert(!noise_origin_valid(0,481));
 for(y=0;y<=480;y+=1) for(x=0;x<=320;x+=1) {
   assert(noise_origin_valid(x,y));
   for(i=0;i<4;i++) {
     r=y+(i&1?239:0);c=x+(i&2?319:0);
     unsigned row=r<360?r:r-360; const uint8_t *p=r<360?a:b;
     assert(noise_pixel(a,b,c,r)==((p[row*80+c/8]>>(7-c%8))&1));
   }
 }
 for(r=0;r<240;r++) for(c=0;c<320;c++)
   (void)noise_pixel(a,b,320+c,480+r);

 uint8_t *base=malloc(76800),*d1=malloc(76800),*d2=malloc(76800),mask[192]={0},colors[32];
 for(i=0;i<76800;i++) base[i]=(i*7u)&255;
 for(i=0;i<32;i++) colors[i]=i;
 unsigned last=0;
 for(i=0;i<4000;i++){unsigned cov=noise_coverage(i);assert(cov>=last && cov<=4);last=cov;}
 assert(noise_coverage(0)==0 && noise_coverage(3000)==4 && noise_coverage(9000)==4);
 noise_compose(d1,base,a,b,mask,colors,0,0);assert(!memcmp(d1,base,76800));
 noise_compose(d1,base,a,b,mask,colors,3100,31);
 memset(d2,255,76800);noise_compose(d2,base,a,b,mask,colors,1500,15);
 noise_compose(d2,base,a,b,mask,colors,3100,31);
 assert(!memcmp(d1,d2,76800));
 noise_compose(d2,base,a,b,mask,colors,3100,31);assert(!memcmp(d1,d2,76800));
 free(base);free(d1);free(d2);
 puts("PASS frame N independence, zero/ramp endpoint monotonic saturation, deterministic offsets");
 free(a);free(b);puts("PASS every origin, corners, nonaligned x, split row strides, seed determinism");
}
