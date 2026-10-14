"""Exact noise contracts and sanitized C lifetime/undo regression."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NoiseContracts(unittest.TestCase):
    def test_asm_row_counter_survives_interrupt_shadow_register_use(self):
        asm = (ROOT / "src/noise_fast.s").read_text()
        draw = asm[asm.index("_noise_draw_asm:"):]
        instructions = [line.split(";")[0].strip().lower() for line in draw.splitlines()]
        self.assertNotIn("exx", instructions)
        self.assertIn("ld (.draw_groups),a", instructions)
        self.assertIn("ld a,(.draw_groups)", instructions)
        self.assertIn(".draw_groups: .byte 0", instructions)

    def test_complete_lfsr_period_and_undo_bound(self):
        ring = []
        state = 1
        while True:
            ring.append(state)
            state = (state >> 1) ^ (0xB400 if state & 1 else 0)
            if state == 1:
                break
        self.assertEqual(len(ring), 65535)
        self.assertEqual(len(set(ring)), 65535)
        selected = [int((state & 15) < 4) for state in ring]
        count = sum(selected[:19200])
        high = count
        for start in range(1, len(ring)):
            count += selected[(start + 19199) % len(ring)] - selected[start - 1]
            high = max(high, count)
        self.assertEqual(high * 4, 19952)

    @unittest.skipUnless(shutil.which("cc"), "host C compiler unavailable")
    def test_host_pixels_and_sequential_undo(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            (work / "fileioc.h").write_text(
                "#include <stdint.h>\ntypedef unsigned ti_var_t;\n"
                "unsigned ti_Open(const char*,const char*);void ti_Close(unsigned);"
                "unsigned ti_GetSize(unsigned);void*ti_GetDataPtr(unsigned);\n")
            (work / "graphx.h").write_text(
                "#include <stdint.h>\nextern uint16_t gfx_palette[256];"
                "extern uint8_t *gfx_vbuffer;\n")
            source = r"""
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
uint16_t gfx_palette[256];
uint8_t guarded[76832], original[76800], metadata[448];
uint8_t *gfx_vbuffer=guarded+16;
unsigned ti_Open(const char*a,const char*b){(void)a;(void)b;return 1;}
void ti_Close(unsigned n){(void)n;}
unsigned ti_GetSize(unsigned n){(void)n;return 448;}
void*ti_GetDataPtr(unsigned n){(void)n;return metadata;}
#include "NOISE_SOURCE"
static void guards(void) {
 for(unsigned i=0;i<16;i++)assert(guarded[i]==0xa5&&guarded[76816+i]==0xa5);
}
int main(void) {
 unsigned times[]={0,1,99,749,750,751,1499,1500,1501,2249,2250,2251,
                   2999,3000,3001,65535,100000,16777215};
 memset(guarded,0xa5,sizeof guarded);
 for(unsigned i=0;i<76800;i++)original[i]=(uint8_t)(i*71u);
 for(unsigned i=0;i<256;i++)gfx_palette[i]=(i&31)*1057u;
 for(unsigned i=0;i<192;i++)metadata[256+i]=i%16;
 assert(noise_begin(2));
 for(unsigned j=0;j<sizeof(times)/sizeof(times[0]);j++){
  unsigned t=times[j],seed=(0xdd1cu^(t/100u)*40503u)&65535u;
  unsigned cov=t>=3000?4u:(uint32_t)t*4u/3000u;
  if(!seed)seed=1;
  unsigned rows=(j&1)?180:240;
  memcpy(gfx_vbuffer,original,sizeof original);
  if(rows==180)noise_frame_scene(t);else noise_frame(t);
  assert(undo_size<=19952u);
  for(unsigned y=0;y<240;y+=2)for(unsigned x=0;x<320;x+=2){
   seed=(seed>>1)^((seed&1)?0xb400u:0);
   uint8_t color=colors[metadata[256+y/20*16+x/20]+((seed>>4)&16)];
   for(unsigned dy=0;dy<2;dy++)for(unsigned dx=0;dx<2;dx++){
    unsigned pos=(y+dy)*320+x+dx;
    assert(gfx_vbuffer[pos]==(y<rows && (seed&15)<cov?color:original[pos]));
   }
  }
  guards();noise_restore();assert(!memcmp(gfx_vbuffer,original,sizeof original));
  noise_frame_scene(t);noise_frame(times[(j+1)%(sizeof(times)/sizeof(times[0]))]);
  noise_restore();assert(!memcmp(gfx_vbuffer,original,sizeof original));guards();
 }
 noise_release();noise_release();noise_end();puts("exact pixels, guards, transitions passed");
}
""".replace("NOISE_SOURCE", (ROOT / "src/noise.c").as_posix())
            (work / "test.c").write_text(source)
            subprocess.run(["cc", "-DNOISE_HOST_GOLDEN", "-I" + str(work),
                            "-fsanitize=address,undefined", "-g", "-O1",
                            str(work / "test.c"), "-o", str(work / "test")], check=True)
            subprocess.run([str(work / "test")], check=True)


if __name__ == "__main__":
    unittest.main()

