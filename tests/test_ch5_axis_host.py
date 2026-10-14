"""Actual separable maps agree with the independent per-pixel sampler."""
import subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class Axis(unittest.TestCase):
 def test_maps(self):
  fixture=r"""
#include <assert.h>
#include <stdio.h>
#include "ch5_motion.h"
int main(void){
 int16_t mx[320],my[180];
 for(unsigned mode=1;mode<=6;mode++)
 for(unsigned ms=0;ms<=12000;ms+=125){
  unsigned tw=(mode==1||mode==3||mode==5)?320:72;
  unsigned th=tw==320?180:263;
  ch5_motion_t p=ch5_motion(ms,mode,tw==320?1280:286,tw==320?720:1050);
  ch5_axis_map(mx,320,p.x,p.zoom_q10,tw);
  ch5_axis_map(my,180,p.y,p.zoom_q10,th);
  for(int y=0;y<180;y++)for(int x=0;x<320;x++){
   unsigned sx=0,sy=0;
   int ok=ch5_source_pixel(x,y,p,tw,th,&sx,&sy);
   assert(ok==(mx[x]>=0&&my[y]>=0));
   if(ok)assert(sx==(unsigned)mx[x]&&sy==(unsigned)my[y]);
  }
 }
 puts("actual axis maps vs per-pixel source PASS");
}
"""
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p / "test.c").write_text(fixture)
   subprocess.run(["cc","-O2","-fsanitize=undefined","-I"+str(ROOT / "src"),str(p / "test.c"),"-o",str(p / "test")],check=True)
   subprocess.run([str(p / "test")],check=True)
