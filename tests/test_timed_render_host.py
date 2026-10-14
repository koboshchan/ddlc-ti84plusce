"""Actual timed renderer + scheduler + noise_tick control flow with faulting FAT.
Host development proof only, not CE USB/ASM/ABI/performance proof.
"""
import subprocess,tempfile,unittest
from pathlib import Path
from export_cgpack import export_timed_frame,export_timed_schedule
ROOT=Path(__file__).resolve().parents[1]
class IntegratedTimed(unittest.TestCase):
 def test_transactional_fallback_and_order(self):
  reader=(ROOT/'src/cgpack.c').read_text();reader=reader[reader.index('static uint32_t timed_u32'):]
  render=(ROOT/'src/render.c').read_text();render=render[render.index('bool render_timed_cg('):render.index('/* The fast path:')]
  main=(ROOT/'src/main.c').read_text();tick=main[main.index('static void noise_tick('):main.index('static void wait_for_advance(')]
  prefix=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
typedef unsigned long clock_t;
#define CLOCKS_PER_SEC 32768UL
typedef struct {uint8_t background;} vn_scene_t;
typedef struct {vn_scene_t scene;bool noise_enabled;} vn_vm_t;
typedef struct {unsigned position;bool schedule;} fat_file_t;
static uint8_t gfx_vbuffer[76800],front[76800],frame[58880],schedule[512];
static uint16_t gfx_palette[256],original[256];
static int fat,pack_ok=1,invalidated,unmounted,read_calls,fail_call,motion_calls,noise_calls,draw_calls;
static bool restored,staging,noise_applied;
static unsigned motion_time;
static clock_t ticks,noise_started,noise_next_frame;
static bool noise_was_enabled;
#define PACK_DIR "/DDLC"
#define FAT_SUCCESS 0
#define CGPACK_READY 3
static clock_t clock(void){return ticks;}
static int cgpack_status(void){return pack_ok?CGPACK_READY:1;}
static int fat_OpenFile(int *unused,const char*p,int flags,fat_file_t*f){
 (void)unused;(void)flags;f->position=0;f->schedule=strstr(p,"SCHED")!=NULL;read_calls=0;return 0;
}
static unsigned fat_GetFileSize(fat_file_t*f){return f->schedule?512:58880;}
static unsigned fat_ReadFile(fat_file_t*f,unsigned n,void*out){
 assert(restored);unsigned actual=n;
 if(!f->schedule && ++read_calls==(unsigned)fail_call)actual=n-1;
 memcpy(out,(f->schedule?schedule:frame)+f->position,actual*512);
 f->position+=actual*512;return actual;
}
static void fat_CloseFile(fat_file_t*f){(void)f;}
static void unmount(void){unmounted++;pack_ok=0;}
static void render_invalidate_scene(void){invalidated++;}
static void plate_free(void){}
static void noise_restore(void){restored=true;noise_applied=false;}
static void noise_release(void){noise_restore();}
static bool ch5_motion_active(uint8_t id){return id==35;}
static bool ch5_motion_sync_clock(uint8_t id){(void)id;return true;}
static uint32_t ch5_motion_elapsed_ms(uint8_t id){(void)id;return 100;}
static const uint16_t *assets_scene_palette(uint8_t id){(void)id;return original;}
static void render_apply_palette(const uint16_t*p){memcpy(gfx_palette,p,512);}
static void render_scene(const vn_scene_t*s){
 (void)s;assert(restored);motion_calls++;motion_time++;
 for(unsigned i=0;i<57600;i++)gfx_vbuffer[i]=(uint8_t)(i+motion_time);
 render_apply_palette(original);
}
static void render_scene_lazy(const vn_scene_t*s){if(!motion_calls)render_scene(s);}
static bool have_drawn_once;
static bool noise_begin(uint8_t id){(void)id;assert(restored);return true;}
static void noise_frame(unsigned age){(void)age;noise_calls++;noise_applied=true;gfx_vbuffer[0]^=255;}
static void noise_end(void){}
static void draw_dialogue_box(vn_vm_t*vm,const vn_scene_t*s,size_t visible){
 (void)vm;(void)s;(void)visible;assert(noise_applied);draw_calls++;
 for(unsigned i=57600;i<76800;i++)assert(gfx_vbuffer[i]==0); /* full-screen failure cleanup */
 memset(gfx_vbuffer+57600,77,19200);
}
static void gfx_BlitBuffer(void){assert(noise_applied);memcpy(front,gfx_vbuffer,76800);}
static void load(const char*p,void*b,unsigned n){FILE*f=fopen(p,"rb");assert(f);assert(fread(b,1,n,f)==n);fclose(f);}
'''
  tests=r'''
int main(int argc,char**argv){
 assert(argc==3);load(argv[1],frame,sizeof frame);load(argv[2],schedule,sizeof schedule);
 for(unsigned i=0;i<256;i++)original[i]=(uint16_t)i;
 vn_vm_t vm={{35},true};
 for(unsigned failure=0;failure<=3;failure++){
  for(unsigned ui_bad=0;ui_bad<=2;ui_bad++){
   load(argv[1],frame,sizeof frame);
   if(ui_bad){
    frame[512]^=1;
    if(ui_bad==2){ /* valid CRC but incompatible reserved UI color */
     uint32_t c=timed_crc32(frame+512,512);
     for(unsigned j=0;j<4;j++)frame[28+j]=(uint8_t)(c>>(j*8));
    }
   }
   pack_ok=1;read_calls=0;fail_call=failure;motion_calls=noise_calls=draw_calls=unmounted=0;motion_time=0;
   restored=false;noise_applied=false;noise_was_enabled=false;ticks=32768;
   memset(gfx_vbuffer,0xee,sizeof gfx_vbuffer);memset(front,0x5a,sizeof front);
   memset(gfx_palette,0xa5,sizeof gfx_palette);
   noise_tick(&vm,&vm.scene,0);
   assert(noise_calls==1 && draw_calls==1);
   if(failure||ui_bad){
    assert(unmounted==(ui_bad==2 && !failure ? 0:1) && motion_calls==1);
    assert(!memcmp(gfx_palette,original,512));
    for(unsigned i=1;i<57600;i++)assert(front[i]==(uint8_t)(i+1));
    assert(front[0]==(uint8_t)(1^255)); /* motion then noise, not stale prefix */
   }else{
    assert(!unmounted && !motion_calls);
    for(unsigned i=1;i<57600;i++)assert(front[i]==(uint8_t)i);
    assert(front[0]==255);
   }
   for(unsigned i=57600;i<76800;i++)assert(front[i]==77);
  }
 }
 /* Scheduler ordinary expiry/absent paths remain motion+noise. */
 pack_ok=0;motion_calls=noise_calls=draw_calls=0;motion_time=0;noise_was_enabled=false;
 memset(gfx_vbuffer+57600,0,19200);noise_tick(&vm,&vm.scene,0);
 assert(motion_calls==1 && noise_calls==1 && draw_calls==1);
 /* Idle scheduler calls before the deadline do not render or present. */
 unsigned before_motion=motion_calls,before_noise=noise_calls,before_draw=draw_calls;
 noise_tick(&vm,&vm.scene,0);
 assert(motion_calls==before_motion && noise_calls==before_noise && draw_calls==before_draw);
 /* Typewriter forces the new prefix through one effect composition. */
 memset(gfx_vbuffer+57600,0,19200);
 noise_next_frame=0;noise_tick(&vm,&vm.scene,1);
 assert(noise_calls==before_noise+1 && draw_calls==before_draw+1);
 puts("actual reader/render/scheduler/noise_tick host fault flow PASS; target proof pending");
}
'''
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);pal=b''.join(i.to_bytes(2,'little') for i in range(256));pix=bytes(range(256))*225
   f=export_timed_frame(p,35,1,100,pix,pal)
   s=export_timed_schedule(p/'schedule',35,100,[(pix,pal)]*3)
   (p/'test.c').write_text(prefix+reader+render+tick+tests)
   subprocess.run(['cc','-std=c11','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(p/'test.c'),'-o',str(p/'test')],check=True)
   subprocess.run([str(p/'test'),str(f),str(s)],check=True)
if __name__=='__main__':unittest.main()

