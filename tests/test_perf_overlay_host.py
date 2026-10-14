from pathlib import Path
import subprocess, tempfile, unittest
ROOT = Path(__file__).resolve().parents[1]
class OverlayHistory(unittest.TestCase):
    def test_ring_and_timer_conversion(self):
        source = r'''
#include "perf_overlay_history.h"
#include <assert.h>
int main(void) {
    perf_history_t h = {0};
    assert(h.count == 0);
    for (unsigned i=1; i<=7; ++i) perf_history_push(&h, i*32768u);
    assert(h.count == 5 && h.next == 2);
    for (unsigned age=0; age<5; ++age) assert(perf_history_ms(&h,age)==(7-age)*1000u);
    perf_history_push(&h,(uint32_t)(16u-0xfffffff0u));
    assert(perf_history_ms(&h,0)==1);
    perf_history_push(&h,3277);
    assert(perf_history_ms(&h,0)==100);
}
'''
        with tempfile.TemporaryDirectory() as d:
            path=Path(d); (path/"test.c").write_text(source)
            subprocess.run(["cc","-Wall","-Wextra","-Werror","-fsanitize=undefined",
                            "-I",str(ROOT/"src"),str(path/"test.c"),"-o",str(path/"test")],check=True)
            subprocess.run([str(path/"test")],check=True)
    def test_release_stubs_need_no_device_headers(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d); (path/"test.c").write_text('#include "perf_overlay.h"\nint main(void){perf_overlay_toggle();perf_overlay_begin();perf_overlay_end();perf_overlay_draw();perf_overlay_shutdown();}')
            subprocess.run(["cc","-Wall","-Wextra","-Werror","-I",str(ROOT/"src"),
                            str(path/"test.c"),"-o",str(path/"test")],check=True)

    def test_actual_device_module_lifecycle_and_clean_buffer(self):
        harness = r"""
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>
#include <assert.h>
uint16_t timer_Control;
uint32_t counts[3];
static unsigned timer_reads, waits;
static uint8_t draw=1, buffers[2][76800];
static uint8_t *active(void){return buffers[draw];}
static uint32_t fake_get(unsigned n){timer_reads++;return counts[n-1];}
static void fake_enable(void){timer_Control=(timer_Control&~(0x1c0|0x800))|0xc0|0x800;}
uint8_t gfx_GetDraw(void){return draw;}
void gfx_SetDraw(uint8_t d){draw=d;}
void gfx_SetDrawScreen(void){draw=0;}
void gfx_Wait(void){waits++;}
size_t os_MemChk(void **p){*p=0;return 12345;}
unsigned char __bss_low[1],__bss_high[1];
#define DDLC_PERF_OVERLAY 1
#define DDLC_PERF_MEMORY_IMPL
#include "perf_memory.h"
perf_memory_t perf_memory_snapshot(void){perf_memory_t m={100,200,2,true};return m;}
#include "perf_overlay.c"
int main(void){
    memset(buffers,77,sizeof buffers);
    for(unsigned i=0;i<256;i++)gfx_palette[i]=0x4000;
    gfx_palette[1]=0;gfx_palette[2]=0x7fff;
    timer_Control=0x2003;counts[2]=1234;
    perf_overlay_begin();perf_overlay_end();perf_overlay_draw();
    assert(timer_reads==0 && waits==0 && timer_Control==0x2003);
    perf_overlay_toggle();
    assert(owns_timer && enabled && counts[2]==0);
    counts[2]=0xfffffff0u;perf_overlay_begin();
    counts[2]=16;perf_overlay_mark_rendered();perf_overlay_end();
    assert(history.count==1 && perf_history_ms(&history,0)==1);
    perf_overlay_end();assert(history.count==1);
    /* An admitted effect tick with lazy/no-op scene composition is not a
     * new rendered ch5 frame and must not append to the ring. */
    counts[2]=100;perf_overlay_begin();counts[2]=500;perf_overlay_end();
    assert(history.count==1);
    perf_overlay_mark_rendered();perf_overlay_end();assert(history.count==1);
    counts[2]=700;perf_overlay_begin();perf_overlay_mark_rendered();
    counts[2]=32768+700;perf_overlay_end();
    assert(history.count==2 && perf_history_ms(&history,0)==1000);
    perf_overlay_draw();
    assert(draw==1 && waits==1);
    for(unsigned i=0;i<76800;i++)assert(buffers[1][i]==77);
    assert(buffers[0][3*320+210]==COL_BLACK);
    assert(buffers[0][0]==77);
    timer_Control ^= 1u<<3; /* unrelated timer changed while overlay active */
    perf_overlay_toggle();
    assert(!enabled && !owns_timer && counts[2]==1234);
    assert(timer_Control==(0x2003^(1u<<3)));
    timer_Control |= 1u<<6; /* timer 3 belongs to another subsystem */
    uint16_t busy=timer_Control; uint32_t busycount=counts[2];
    perf_overlay_toggle();assert(enabled && !owns_timer);
    perf_overlay_begin();perf_overlay_end();perf_overlay_shutdown();
    assert(timer_Control==busy && counts[2]==busycount);
}
"""
        with tempfile.TemporaryDirectory() as d:
            path=Path(d); (path/"sys").mkdir(); (path/"ti").mkdir()
            stubs={
                "graphx.h":'uint16_t gfx_palette[256];\n#define gfx_vbuffer active()\nuint8_t gfx_GetDraw(void); void gfx_SetDraw(uint8_t); void gfx_SetDrawScreen(void); void gfx_Wait(void);',
                "sys/lcd.h":"",
                "ti/vars.h":"size_t os_MemChk(void **);",
                "sys/timers.h":"""#define timer_Get(n) fake_get(n)
#define timer_Set(n,v) (counts[(n)-1]=(v))
#define timer_Disable(n) (timer_Control &= ~(1u << (3u*((n)-1))))
#define timer_Enable(n,r,i,d) fake_enable()
#define TIMER_32K 1
#define TIMER_NOINT 0
#define TIMER_UP 1
""",
            }
            for name,body in stubs.items():(path/name).write_text(body)
            # render.h imports graphx/vn dependencies; remove ONLY this include
            # from the tested copy and use its actual two palette constants.
            actual=(ROOT/"src/perf_overlay.c").read_text().replace('#include "render.h"',
                        '#define COL_BLACK 1\n#define COL_WHITE 2')
            (path/"perf_overlay.c").write_text(actual)
            (path/"test.c").write_text(harness)
            subprocess.run(["cc","-Wall","-Wextra","-Werror","-fsanitize=undefined",
                            "-I",str(path),"-I",str(ROOT/"src"),str(path/"test.c"),
                            "-o",str(path/"test")],check=True)
            subprocess.run([str(path/"test")],check=True)
if __name__ == "__main__": unittest.main()
