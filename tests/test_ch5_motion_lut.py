"""Approximate motion scanlines, missing-asset fallback and pointer lifetime."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from test_ch5_motion_cache import HARNESS, MAIN, ROOT

LUT_STUB = r"""
void ch5_motion_lut_fast(uint8_t*d,const uint8_t*s,const int16_t*m,size_t n,const uint8_t*g,const uint8_t*t){
 for(size_t i=0;i<n;i++){assert(m[i]>=0);uint8_t c=s[m[i]];if(c!=255)d[i]=t[(unsigned)c*16+g[d[i]]];}
}
"""
class MotionLut(unittest.TestCase):
 @unittest.skipUnless(shutil.which("cc"), "host C compiler unavailable")
 def test_lut_and_fallback_lifetimes(self):
  harness=HARNESS.replace("static uint32_t seed=17;", """
static uint8_t groups[256],tables[8][4096];
static unsigned availability,table_level;
static unsigned opened[6];
static uint32_t seed=17;""")
  start=harness.index("uint8_t ti_Open(")
  end=harness.index("unsigned ti_Read(",start)
  harness=harness[:start]+r"""
uint8_t ti_Open(const char*n,const char*m){
 (void)m;unsigned h=!strcmp(n,"DMOTMAP")?1:!strcmp(n,"DMOTPAL")?2:!strcmp(n,"DMOTRGB")?3:0;
 if(!strcmp(n,"DMOTBGM") && availability!=0)h=4;
 if(!strncmp(n,"DMLUT",5) && availability!=3 && availability!=0){
  assert(strlen(n)==7);table_level=(n[5]-'0')*10+n[6]-'0';assert(table_level<8);h=5;
 }
 if(h){assert(!opened[h]);opened[h]=1;}
 return h;
}
void ti_Close(uint8_t h){assert(opened[h]);assert(!band_active || h==5);opened[h]=0;}
unsigned ti_GetSize(uint8_t h){return h==1?18:h==2?512:h==3?32768:h==4?(availability==2?255:256):(availability==4?4095:4096);}
void *ti_GetDataPtr(uint8_t h){assert(opened[h]);return h==1?(void*)record:h==2?(void*)palette:h==3?(void*)nearest:h==4?(void*)groups:(void*)tables[table_level];}
""" +harness[end:]
  main=MAIN.replace("unsigned fg", "unsigned fg") # unchanged exact fallback
  main=main.replace("unsigned f=palette[c]", """
   if(availability==1){
    unsigned level=(p.alpha_q10*7u+512u)/1024u;
    expected[at]=tables[level][(unsigned)c*16+groups[expected[at]]];continue;
   }
   unsigned f=palette[c]""")
  main=main.replace("memset(storage+76800", """
 for(unsigned i=0;i<256;i++)groups[i]=random32()&15;
 for(unsigned l=0;l<8;l++)for(unsigned i=0;i<4096;i++)tables[l][i]=random32()%255;
 for(availability=0;availability<5;availability++){
  last_scene=255;memset(previous_mode,255,sizeof previous_mode);
  authored=0;assert(ch5_motion_sync_clock(1));
 memset(storage+76800""")
  main=main.replace("reference(ms);", "reference(ms);for(unsigned i=1;i<6;i++)assert(!opened[i]);")
  main=main.replace(" fail_row=0;", " }\n fail_row=0;")
  with tempfile.TemporaryDirectory() as tmp:
   tmp=Path(tmp)
   (tmp/"fileioc.h").write_text("#include <stdint.h>\nuint8_t ti_Open(const char*,const char*);void ti_Close(uint8_t);unsigned ti_GetSize(uint8_t);void *ti_GetDataPtr(uint8_t);unsigned ti_Read(void*,unsigned,unsigned,uint8_t);\n")
   (tmp/"graphx.h").write_text("#include <stdint.h>\nextern uint8_t *gfx_vbuffer;void gfx_SetPalette(const uint16_t*,unsigned,unsigned);\n")
   source=(ROOT/"src/ch5_motion_render.c").read_text().replace("clock()", "fixture_clock()")
   (tmp/"test.c").write_text(harness+source+main)
   subprocess.run(["cc","-O2","-fsanitize=undefined","-I"+str(tmp),"-I"+str(ROOT/"src"),str(tmp/"test.c"),"-o",str(tmp/"test")],check=True)
   subprocess.run([str(tmp/"test")],check=True)
if __name__=="__main__":unittest.main()
