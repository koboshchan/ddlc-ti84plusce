from pathlib import Path
import shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
HARNESS='\n#include <stdint.h>\n#include <stdbool.h>\n#include <stdlib.h>\n#include <string.h>\n#include <time.h>\n#include <assert.h>\nstatic clock_t authored;\nstatic unsigned begins,ends;static bool band_active;static int fail_row=-1;\nstatic clock_t fixture_clock(void){return authored;}\nstatic uint8_t storage[76800+64], expected[57600];\nuint8_t *gfx_vbuffer=storage;\nstatic uint8_t record[18]={1,4,0,1,1,5,2,2,3,6};\nstatic uint16_t palette[256];\nstatic uint8_t nearest[32768],layers[4][57600];\nstatic uint8_t qlut[12560];static unsigned lut_available=1,lut_size=12560;static unsigned opened[5];\nstatic uint32_t seed=17;\nstatic uint32_t random32(void){seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return seed;}\n\nuint8_t ti_Open(const char*n,const char*m){\n (void)m;unsigned h=!strcmp(n,"DMOTMAP")?1:!strcmp(n,"DMOTPAL")?2:!strcmp(n,"DMOTRGB")?3:!strcmp(n,"DLUTQ")&&lut_available?4:0;\n if(h){assert(!opened[h]);opened[h]=1;}return h;\n}\nvoid ti_Close(uint8_t h){assert(!band_active && opened[h]);opened[h]=0;}\nunsigned ti_GetSize(uint8_t h){return h==1?18:h==2?512:h==4?lut_size:32768;}\nvoid *ti_GetDataPtr(uint8_t h){assert(opened[h]);return h==1?(void*)record:h==2?(void*)palette:h==4?(void*)qlut:(void*)nearest;}\nunsigned ti_Read(void*out,unsigned a,unsigned b,uint8_t h){memcpy(out,ti_GetDataPtr(h),a*b);return b;}\nvoid gfx_SetPalette(const uint16_t*p,unsigned n,unsigned off){(void)p;(void)n;(void)off;}\nvoid ch5_band_invalidate(void){}\nvoid ch5_band_begin(void){assert(!band_active);band_active=true;begins++;}\nvoid ch5_band_end(void){assert(band_active);band_active=false;ends++;}\nconst uint8_t *ch5_band_row(uint8_t l,uint16_t y){unsigned w=l<2?320:72;assert(band_active);assert(l<4 && y<(l<2?180:263));if((int)y==fail_row)return NULL;return layers[l]+y*w;}\nvoid ch5_motion_opaque_fast(uint8_t*d,const uint8_t*s,const int16_t*m,size_t n){\n for(size_t i=0;i<n;i++){assert(m[i]>=0);uint8_t c=s[m[i]];if(c!=255)d[i]=c;}\n}\n\n#include "ch5_motion_fast.h"\nsize_t ch5_motion_cached_fast(uint8_t*d,const uint8_t*s,const int16_t*m,size_t n,const uint16_t*t,const uint8_t*c){\n size_t i;for(i=0;i<n;i++){uint8_t f=s[m[i]];if(f==255)continue;uint16_t p=((uint16_t)f<<8)|d[i];unsigned slot=(((unsigned)f<<5)-(unsigned)f+d[i])&4095;if(t[slot]!=p)break;d[i]=c[slot];}return i;}\n\n\nvoid ch5_motion_lut_fast(uint8_t*d,const uint8_t*s,const int16_t*m,size_t n,const uint8_t*g,const uint8_t*t){\n for(size_t i=0;i<n;i++){uint8_t c=s[m[i]];if(c!=255)d[i]=t[(unsigned)c*32+g[d[i]]];}\n}\n\nvoid ch5_motion_quant_fast(uint8_t*d,const uint8_t*s,const int16_t*m,unsigned n,const uint8_t*g,const uint8_t*t){\n for(unsigned i=0;i<n;i++){assert(m[i]>=0);uint8_t c=s[m[i]];if(c!=255){unsigned group=(uint8_t)(g[c]-(uint8_t)((uintptr_t)t>>8));assert(group<16);d[i]=t[(group<<8)|d[i]];}}\n}\n'
HARNESS+='\nstatic void direct_quant(uint8_t*d,const uint8_t*s,unsigned n,unsigned repeat,const uint8_t*g,const uint8_t*t){\n uint8_t base=(uint8_t)((uintptr_t)t>>8);\n for(unsigned i=0;i<n;i++)for(unsigned j=0;j<repeat;j++){\n  uint8_t c=s[i];if(c!=255){unsigned group=(uint8_t)(g[c]-base);assert(group<16);d[i*repeat+j]=t[group*256+d[i*repeat+j]];}\n }\n}\nvoid ch5_motion_quant_direct1(uint8_t*d,const uint8_t*s,unsigned n,const uint8_t*g,const uint8_t*t){direct_quant(d,s,n,1,g,t);}\nvoid ch5_motion_quant_direct2(uint8_t*d,const uint8_t*s,unsigned n,const uint8_t*g,const uint8_t*t){direct_quant(d,s,n,2,g,t);}\nvoid ch5_motion_opaque_direct1(uint8_t*d,const uint8_t*s,unsigned n){for(unsigned i=0;i<n;i++)if(s[i]!=255)d[i]=s[i];}\nvoid ch5_motion_opaque_direct2(uint8_t*d,const uint8_t*s,unsigned n){for(unsigned i=0;i<n;i++)if(s[i]!=255)d[2*i]=d[2*i+1]=s[i];}\n'
HARNESS+='\nvoid ch5_motion_quant_solid1(uint8_t*d,const uint8_t*s,unsigned n,const uint8_t*g,const uint8_t*t){direct_quant(d,s,n,1,g,t);}\nvoid ch5_motion_quant_solid2(uint8_t*d,const uint8_t*s,unsigned n,const uint8_t*g,const uint8_t*t){direct_quant(d,s,n,2,g,t);}\nvoid ch5_motion_opaque_solid1(uint8_t*d,const uint8_t*s,unsigned n){memcpy(d,s,n);}\nvoid ch5_motion_opaque_solid2(uint8_t*d,const uint8_t*s,unsigned n){for(unsigned i=0;i<n;i++)d[2*i]=d[2*i+1]=s[i];}\n'
MAIN='\nstatic void reference(unsigned ms){\n memset(expected,0,sizeof expected);\n for(unsigned l=0;l<4;l++){\n  ch5_motion_t p=ch5_motion(ms,record[3+2*l],l<2?1280:286,l<2?720:1050);\n  int16_t xm[320],ym[180];\n  ch5_axis_map(xm,320,p.x,p.zoom_q10,l<2?320:72);\n  ch5_axis_map(ym,180,p.y,p.zoom_q10,l<2?180:263);\n  for(unsigned y=0;y<180;y++)for(unsigned x=0;x<320;x++){\n   if(xm[x]<0||ym[y]<0)continue;\n   unsigned at=y*320+x;uint8_t c=layers[l][ym[y]*(l<2?320:72)+xm[x]];\n   if(c==255)continue;\n   if(p.alpha_q10==1024){expected[at]=c;continue;}\n   unsigned level=p.alpha_q10<352?0:p.alpha_q10<544?1:2;\n   expected[at]=qlut[272+level*4096+qlut[c]*256+expected[at]];\n\n  }\n }\n}\nint main(void){\n for(unsigned i=0;i<256;i++)palette[i]=random32()&32767;\n for(unsigned i=0;i<32768;i++)nearest[i]=random32()&255;\n for(unsigned l=0;l<4;l++)for(unsigned i=0;i<57600;i++)layers[l][i]=random32()&255;\n for(unsigned i=0;i<256;i++)qlut[i]=random32()&15;\n for(unsigned i=272;i<12560;i++)qlut[i]=random32()&255;\n authored=0;assert(ch5_motion_sync_clock(1));\n memset(storage+76800,0xA7,64);\n for(unsigned ms=0;ms<=6000;ms+=125){\n  authored=(uint64_t)ms*CLOCKS_PER_SEC/1000;\n  assert(ch5_motion_draw(1));assert(!band_active && begins==ends);reference(ms);for(unsigned k=1;k<5;k++)assert(!opened[k]);\n  assert(!memcmp(expected,storage,57600));\n  for(unsigned i=76800;i<sizeof storage;i++)assert(storage[i]==0xA7);\n }\n for(unsigned l=0;l<2;l++)for(unsigned k=0;k<57600;k++)if(layers[l][k]==255)layers[l][k]=254;\n for(unsigned ms=0;ms<=6000;ms+=125){authored=(uint64_t)ms*CLOCKS_PER_SEC/1000;assert(ch5_motion_draw(1));reference(ms);assert(!memcmp(expected,storage,57600));}\n fail_row=0;assert(!ch5_motion_draw(1));assert(!band_active && begins==ends);\n fail_row=1;assert(!ch5_motion_draw(1));assert(!band_active && begins==ends);\n record[1]=9;assert(!ch5_motion_draw(1));assert(!band_active && begins==ends);\n fail_row=-1;record[1]=4;\n lut_available=0;assert(!ch5_motion_draw(1));\n lut_available=1;lut_size=12559;assert(!ch5_motion_draw(1));\n lut_size=12560;uint8_t before=qlut[0];qlut[0]=16;assert(!ch5_motion_draw(1));qlut[0]=before;\n for(unsigned k=1;k<5;k++)assert(!opened[k]);\n return 0;\n}\n'
class MotionQuant(unittest.TestCase):
 @unittest.skipUnless(shutil.which("cc"),"host C compiler unavailable")
 def test_randomized_quant_reference_guards_failures_and_handles(self):
  with tempfile.TemporaryDirectory() as directory:
   tmp=Path(directory)
   (tmp/"fileioc.h").write_text("#include <stdint.h>\nuint8_t ti_Open(const char*,const char*);void ti_Close(uint8_t);unsigned ti_GetSize(uint8_t);void *ti_GetDataPtr(uint8_t);unsigned ti_Read(void*,unsigned,unsigned,uint8_t);\n")
   (tmp/"graphx.h").write_text("#include <stdint.h>\nextern uint8_t *gfx_vbuffer;void gfx_SetPalette(const uint16_t*,unsigned,unsigned);\n")
   source=(ROOT/"src/ch5_motion_render.c").read_text().replace("clock()","fixture_clock()")
   (tmp/"test.c").write_text(HARNESS+source+MAIN)
   subprocess.run(["cc","-O2","-fsanitize=address,undefined","-I"+str(tmp),"-I"+str(ROOT/"src"),str(tmp/"test.c"),"-o",str(tmp/"test")],check=True)
   subprocess.run([str(tmp/"test")],check=True)

class ForegroundLookup(unittest.TestCase):
 def test_generator_layout_and_validation(self):
  import sys
  sys.path.insert(0,str(ROOT/"tools"))
  from ch5_fg16_lut import generate_fg16_lut
  result=generate_fg16_lut([0]*256,bytes(32768),[bytes([255])]*4)
  self.assertEqual(len(result),12560)
  self.assertLess(max(result[:256]),16)
  self.assertEqual(result[272:],bytes(12288))
  with self.assertRaises(ValueError):generate_fg16_lut([0]*255,bytes(32768),[b""]*4)

class SolidBackgroundSafety(unittest.TestCase):
 def test_solid_models_counts_repeats_guards_and_transparency_fallback(self):
  self.assertIn('!memchr(row,255,tw)', (ROOT/'src/ch5_motion_render.c').read_text())
  main=r"""
int main(void){
 uint8_t src[320],dst[672],want[672],table[4096],groups[256];
 for(unsigned i=0;i<4096;i++)table[i]=random32();
 uint8_t base=(uint8_t)((uintptr_t)table>>8);
 for(unsigned i=0;i<256;i++)groups[i]=base+(i&15);
 for(unsigned n=0;n<=320;n++)for(unsigned repeat=1;repeat<=2;repeat++)for(unsigned quant=0;quant<2;quant++){
  for(unsigned k=0;k<320;k++)src[k]=random32()%255;
  for(unsigned k=0;k<672;k++)dst[k]=want[k]=random32();
  for(unsigned k=0;k<n;k++)for(unsigned j=0;j<repeat;j++){unsigned at=16+k*repeat+j;want[at]=quant?table[(src[k]&15)*256+want[at]]:src[k];}
  if(quant){if(repeat==1)ch5_motion_quant_solid1(dst+16,src,n,groups,table);else ch5_motion_quant_solid2(dst+16,src,n,groups,table);}
  else{if(repeat==1)ch5_motion_opaque_solid1(dst+16,src,n);else ch5_motion_opaque_solid2(dst+16,src,n);}
  assert(!memcmp(dst,want,672));
 }
 return 0;
}
"""
  with tempfile.TemporaryDirectory() as directory:
   tmp=Path(directory)
   (tmp/'test.c').write_text(HARNESS+main)
   subprocess.run(['cc','-O2','-fsanitize=address,undefined','-I'+str(ROOT/'src'),str(tmp/'test.c'),'-o',str(tmp/'test')],check=True)
   subprocess.run([str(tmp/'test')],check=True)
