import os,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class FadePaletteTests(unittest.TestCase):
 def test_actual_renderer_holds_motion_palette_black(self):
  source=(ROOT/"src/render.c").read_text()
  palette=source[source.index("static uint16_t current_palette[256];"):source.index("void render_pause_box(")]
  pre="""#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <assert.h>
static uint16_t gfx_palette[256];
#define FADE_STEPS 16
#define FADE_STEP_MS 25
#define FADE_HOLD_MS 250
static void msleep(unsigned ms){(void)ms;}
static uint16_t gfx_Darken(uint16_t c,uint8_t x){return x==255?c:0;}
"""
  body="""static void black(void){for(unsigned i=0;i<256;i++)assert(gfx_palette[i]==0);}
int main(void){
 uint16_t game[256],motion[256],title[256];
 for(unsigned i=0;i<256;i++){game[i]=i;motion[i]=1000+i;title[i]=2000+i;}
 for(unsigned i=0;i<50;i++)if(i!=8&&i!=9)motion[i]=game[i];
 render_apply_palette(game);assert(!memcmp(gfx_palette,game,512));
 render_fade_out();black();render_fade_retarget(game);black();
 render_apply_palette(motion);black();
 assert(!memcmp(current_palette,motion,512)&&!memcmp(fade_saved,motion,512));
 black();render_fade_in();
 assert(!memcmp(gfx_palette,motion,512)&&!fade_active);
 for(unsigned i=0;i<50;i++)if(i!=8&&i!=9)assert(gfx_palette[i]==game[i]);
 render_apply_palette(title);assert(!memcmp(gfx_palette,title,512));
 render_apply_palette(game);assert(!memcmp(gfx_palette,game,512));
 render_fade_out();render_fade_retarget(title);black();render_fade_in();
 assert(!memcmp(gfx_palette,title,512));
}"""
  with tempfile.TemporaryDirectory() as d:
   c=Path(d)/"test.c";exe=Path(d)/"test";c.write_text(pre+palette+body)
   subprocess.run([os.environ.get("CC","cc"),"-O2","-fsanitize=address,undefined",str(c),"-o",str(exe)],check=True)
   subprocess.run([str(exe)],check=True)
if __name__=="__main__":unittest.main()
