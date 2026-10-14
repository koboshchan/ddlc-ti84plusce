"""Layer cache lifetimes, bounds and file resolution against actual C."""
import subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class Bands(unittest.TestCase):
 def test_cache_and_bounds(self):
  source=(ROOT/'src/ch5_bands.c').read_text()
  fixture=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>
static uint8_t streams[4][256];
static unsigned opens,decodes;
uint8_t ti_Open(const char*n,const char*m){(void)m;assert(!memcmp(n,"DMOT",4));assert(n[4]>='0'&&n[4]<='3');opens++;return n[4]-'0'+1;}
unsigned ti_GetSize(uint8_t h){(void)h;return 256;}
void* ti_GetDataPtr(uint8_t h){return streams[h-1];}
void ti_Close(uint8_t h){(void)h;}
void zx0_Decompress(void*dst,const void*src){const uint8_t*p=src;memset(dst,p[0],p[1]?576:2560);decodes++;}
int main(void){
 for(unsigned l=0;l<4;l++){
  uint8_t*p=streams[l];unsigned w=l<2?320:72,h=l<2?180:263,n=(h+7)/8,off=10+2*n;
  memcpy(p,"DMT1",4);p[4]=w;p[5]=w>>8;p[6]=h;p[7]=h>>8;p[8]=8;p[9]=n;
  for(unsigned b=0;b<n;b++){p[10+2*b]=off+2*b;p[11+2*b]=0;p[off+2*b]=l*40+b;p[off+2*b+1]=l>=2;}
 }
 ch5_band_invalidate();ch5_band_begin();
 const uint8_t*a=ch5_band_row(0,0),*b=ch5_band_row(1,0);
 assert(a[0]==0&&b[0]==40&&a[0]==0);
 assert(opens==2&&decodes==2);
 assert(ch5_band_row(0,7)[319]==0&&opens==2&&decodes==2);
 assert(ch5_band_row(0,8)[0]==1&&opens==2&&decodes==3);
 assert(!ch5_band_row(0,180)&&!ch5_band_row(2,263)&&!ch5_band_row(4,0));
 assert(ch5_band_row(2,262)[71]==112);
 unsigned prev=opens;ch5_band_end();ch5_band_begin();
 assert(ch5_band_row(0,16)[0]==2&&opens==prev+1);
 ch5_band_end();ch5_band_invalidate();prev=opens;
 assert(ch5_band_row(0,0)&&opens==prev+1);
 streams[1][0]='X';assert(!ch5_band_row(1,8));
 puts("actual bands lifetime, layer isolation and bounds PASS");
}
'''
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)
   (p/'fileioc.h').write_text('#include <stdint.h>\nuint8_t ti_Open(const char*,const char*);unsigned ti_GetSize(uint8_t);void*ti_GetDataPtr(uint8_t);void ti_Close(uint8_t);\n')
   (p/'compression.h').write_text('void zx0_Decompress(void*,const void*);\n')
   (p/'test.c').write_text(fixture[:fixture.index('int main(void)')]+source+fixture[fixture.index('int main(void)'):])
   subprocess.run(['cc','-std=c11','-fsanitize=address,undefined','-I'+str(p),'-I'+str(ROOT/'src'),str(p/'test.c'),'-o',str(p/'test')],check=True)
   subprocess.run([str(p/'test')],check=True)
