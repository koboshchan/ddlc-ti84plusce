"""Asset-independent checks for exact motion palette-pair caching.

The assembly row ABI is additionally checked by the target RAM proof. This
host test uses an equivalent C opaque row and exercises real renderer source.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r"""
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
uint8_t *gfx_vbuffer=storage;
static uint8_t record[18]={1,4,0,1,1,5,2,2,3,6};
static uint16_t palette[256];
static uint8_t nearest[32768],layers[4][57600];
static uint32_t seed=17;
static uint32_t random32(void){seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return seed;}
uint8_t ti_Open(const char*n,const char*m){(void)m;return !strcmp(n,"DMOTMAP")?1:!strcmp(n,"DMOTPAL")?2:!strcmp(n,"DMOTRGB")?3:0;}
void ti_Close(uint8_t h){(void)h;assert(!band_active);}
unsigned ti_GetSize(uint8_t h){return h==1?18:h==2?512:32768;}
void *ti_GetDataPtr(uint8_t h){return h==1?(void*)record:h==2?(void*)palette:(void*)nearest;}
unsigned ti_Read(void*out,unsigned a,unsigned b,uint8_t h){memcpy(out,ti_GetDataPtr(h),a*b);return b;}
void gfx_SetPalette(const uint16_t*p,unsigned n,unsigned off){(void)p;(void)n;(void)off;}
void ch5_band_invalidate(void){}
void ch5_band_begin(void){assert(!band_active);band_active=true;begins++;}
void ch5_band_end(void){assert(band_active);band_active=false;ends++;}
const uint8_t *ch5_band_row(uint8_t l,uint16_t y){unsigned w=l<2?320:72;assert(band_active);assert(l<4 && y<(l<2?180:263));if((int)y==fail_row)return NULL;return layers[l]+y*w;}
void ch5_motion_opaque_fast(uint8_t*d,const uint8_t*s,const int16_t*m,size_t n){
 for(size_t i=0;i<n;i++){assert(m[i]>=0);uint8_t c=s[m[i]];if(c!=255)d[i]=c;}
}
"""
HARNESS += (ROOT / "src/ch5_motion_fast.c").read_text()

HARNESS += r"""
void ch5_motion_lut_fast(uint8_t*d,const uint8_t*s,const int16_t*m,size_t n,const uint8_t*g,const uint8_t*t){
 for(size_t i=0;i<n;i++){uint8_t c=s[m[i]];if(c!=255)d[i]=t[(unsigned)c*32+g[d[i]]];}
}
"""
MAIN = r"""
static void reference(unsigned ms){
 memset(expected,0,sizeof expected);
 for(unsigned l=0;l<4;l++){
  ch5_motion_t p=ch5_motion(ms,record[3+2*l],l<2?1280:286,l<2?720:1050);
  int16_t xm[320],ym[180];
  ch5_axis_map(xm,320,p.x,p.zoom_q10,l<2?320:72);
  ch5_axis_map(ym,180,p.y,p.zoom_q10,l<2?180:263);
  for(unsigned y=0;y<180;y++)for(unsigned x=0;x<320;x++){
   if(xm[x]<0||ym[y]<0)continue;
   unsigned at=y*320+x;uint8_t c=layers[l][ym[y]*(l<2?320:72)+xm[x]];
   if(c==255)continue;
   if(p.alpha_q10==1024){expected[at]=c;continue;}
   unsigned f=palette[c],b=palette[expected[at]],a=p.alpha_q10;
   unsigned r=(((f>>10)&31)*a+((b>>10)&31)*(1024-a)+512)>>10;
   unsigned g=(((f>>5)&31)*a+((b>>5)&31)*(1024-a)+512)>>10;
   unsigned bl=((f&31)*a+(b&31)*(1024-a)+512)>>10;
   expected[at]=nearest[(r<<10)|(g<<5)|bl];
  }
 }
}
int main(void){
 for(unsigned i=0;i<256;i++)palette[i]=random32()&32767;
 for(unsigned i=0;i<32768;i++)nearest[i]=random32()&255;
 for(unsigned l=0;l<4;l++)for(unsigned i=0;i<57600;i++)layers[l][i]=random32()&255;
 memset(storage+76800,0xA7,64);
 for(unsigned ms=0;ms<=6000;ms+=125){
  authored=(uint64_t)ms*CLOCKS_PER_SEC/1000;
  assert(ch5_motion_draw(1));assert(!band_active && begins==ends);reference(ms);
  assert(!memcmp(expected,storage,57600));
  for(unsigned i=76800;i<sizeof storage;i++)assert(storage[i]==0xA7);
 }
 fail_row=0;assert(!ch5_motion_draw(1));assert(!band_active && begins==ends);
 fail_row=1;assert(!ch5_motion_draw(1));assert(!band_active && begins==ends);
 record[1]=9;assert(!ch5_motion_draw(1));assert(!band_active && begins==ends);
 return 0;
}
"""

class MotionCache(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "host C compiler unavailable")
    def test_randomized_exact_alpha_and_scratch_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "fileioc.h").write_text(
                "#include <stdint.h>\n"
                "uint8_t ti_Open(const char*,const char*);void ti_Close(uint8_t);"
                "unsigned ti_GetSize(uint8_t);void *ti_GetDataPtr(uint8_t);"
                "unsigned ti_Read(void*,unsigned,unsigned,uint8_t);\n"
            )
            (tmp / "graphx.h").write_text(
                "#include <stdint.h>\nextern uint8_t *gfx_vbuffer;"
                "void gfx_SetPalette(const uint16_t*,unsigned,unsigned);\n"
            )
            source = (ROOT / "src/ch5_motion_render.c").read_text()
            source = source.replace("clock()", "fixture_clock()")
            (tmp / "test.c").write_text(HARNESS + source + MAIN)
            subprocess.run(
                ["cc", "-O2", "-fsanitize=undefined", "-I"+str(tmp),
                 "-I"+str(ROOT/"src"), str(tmp/"test.c"), "-o", str(tmp/"test")],
                check=True,
            )
            subprocess.run([str(tmp/"test")], check=True)

if __name__ == "__main__":
    unittest.main()
