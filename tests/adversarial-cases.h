static void reference(unsigned ms){
 memset(expected,0,sizeof expected);
 for(unsigned l=0;l<record[1];l++){
  unsigned texture=record[2+2*l];
 ch5_motion_t p=ch5_motion(ms,record[3+2*l],texture<2?1280:286,texture<2?720:1050);
  int16_t xm[320],ym[180];
  ch5_axis_map(xm,320,p.x,p.zoom_q10,texture<2?320:72);
  ch5_axis_map(ym,180,p.y,p.zoom_q10,texture<2?180:263);
  for(unsigned y=0;y<180;y++)for(unsigned x=0;x<320;x++){
   if(xm[x]<0||ym[y]<0)continue;
   unsigned at=y*320+x;uint8_t c=layers[texture][ym[y]*(texture<2?320:72)+xm[x]];
   if(c==255)continue;
   if(p.alpha_q10==1024){expected[at]=c;continue;}
   unsigned level=p.alpha_q10<352?0:p.alpha_q10<544?1:2;
   expected[at]=qlut[272+level*4096+qlut[c]*256+expected[at]];

  }
 }
}

int main(void){
for(unsigned i=0;i<256;i++)palette[i]=random32()&32767;
for(unsigned i=0;i<32768;i++)nearest[i]=random32()&255;
for(unsigned i=0;i<256;i++)qlut[i]=random32()&15;
for(unsigned i=272;i<12560;i++)qlut[i]=random32()&255;
unsigned cases=0;
for(unsigned scenario=0;scenario<10;scenario++){
uint8_t layout[]={1,5,1,0,0,3,1,5,2,4,3,6};memcpy(record,layout,sizeof layout);
for(unsigned l=0;l<4;l++)for(unsigned i=0;i<57600;i++)layers[l][i]=(uint8_t)(random32()%255);
if(scenario==1)for(unsigned y=0;y<180;y++)layers[0][y*320+150]=255;
if(scenario==2)for(unsigned y=0;y<180;y++)layers[1][y*320+150]=255;
if(scenario==3)record[7]=1;
if(scenario==4)record[5]=1;
if(scenario==5){record[4]=1;record[6]=0;}
if(scenario==6){for(unsigned i=0;i<57600;i++)layers[1][i]=255;}
if(scenario==7){record[3]=3;record[5]=0;}
if(scenario==8){record[9]=2;record[11]=2;}
if(scenario==9){record[1]=3;}
for(unsigned age=0;age<=24000;age+=125){
authored=0;for(unsigned j=0;j<8;j++)previous_mode[j]=255;
assert(ch5_motion_sync_clock(1));authored=(uint64_t)age*CLOCKS_PER_SEC/1000;
memset(storage,0xA7,sizeof storage);
assert(ch5_motion_draw(1));assert(!band_active&&begins==ends);
reference(age);assert(!memcmp(expected,gfx_vbuffer,57600));
for(unsigned i=0;i<32;i++)assert(storage[i]==0xA7);
for(unsigned i=57632;i<sizeof storage;i++)assert(storage[i]==0xA7);
for(unsigned i=1;i<5;i++)assert(!opened[i]);cases++;
}
}
record[1]=9;assert(!ch5_motion_draw(1));record[1]=5;
lut_size=12559;assert(!ch5_motion_draw(1));lut_size=12560;
qlut[0]=16;assert(!ch5_motion_draw(1));qlut[0]=0;
printf("actual renderer adversarial PASS %u cases guarded framebuffer, full scalar reference, draw resets, transparent/fallback/clipping, FG nonleak\n",cases);
return 0;}
