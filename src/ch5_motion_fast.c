#include "ch5_motion_fast.h"
size_t ch5_motion_cached_fast(uint8_t*d,const uint8_t*s,const int16_t*m,size_t n,const uint16_t*t,const uint8_t*c){
 size_t i;for(i=0;i<n;i++){uint8_t f=s[m[i]];if(f==255)continue;uint16_t p=((uint16_t)f<<8)|d[i];unsigned slot=(((unsigned)f<<5)-(unsigned)f+d[i])&4095;if(t[slot]!=p)break;d[i]=c[slot];}return i;}
