"""Run the actual portable reader logic against bounded in-memory FAT stubs.
This is NOT a USB-driver, calculator ABI, render/palette or timing test.
"""
import json,os,random,subprocess,tempfile,unittest,zlib
from pathlib import Path
from export_cgpack import export_timed_frame,export_timed_schedule
ROOT=Path(__file__).resolve().parents[1]

class HostReader(unittest.TestCase):
 def test_actual_reader_and_crc(self):
  source=(ROOT/"src/cgpack.c").read_text()
  reader=source[source.index("static uint32_t timed_u32"): ]
  prefix=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
typedef struct {unsigned position;} fat_file_t;
static int fat,pack_ok=1,invalidated,unmounted,fail_read;
static unsigned read_calls,drive_length;
static uint8_t drive[60000];
#define FAT_SUCCESS 0
#define PACK_DIR "/DDLC"
static int fat_OpenFile(int *unused,const char *path,int flags,fat_file_t *f) {
 (void)unused;(void)path;(void)flags;f->position=0;read_calls=0;return FAT_SUCCESS;
}
static unsigned fat_GetFileSize(fat_file_t*f){(void)f;return drive_length;}
static unsigned fat_ReadFile(fat_file_t*f,unsigned n,void*out){
 unsigned actual=n;
 if(++read_calls==(unsigned)fail_read)actual=n-1;
 if(f->position+actual*512>drive_length)return 0;
 memcpy(out,drive+f->position,actual*512);f->position+=actual*512;return actual;
}
static void fat_CloseFile(fat_file_t*f){(void)f;}
static void unmount(void){unmounted++;pack_ok=0;}
static void render_invalidate_scene(void){invalidated++;}
static void load(const char*p){FILE*f=fopen(p,"rb");assert(f);drive_length=fread(drive,1,sizeof drive,f);fclose(f);}
static void reset(void){pack_ok=1;invalidated=unmounted=fail_read=0;}
'''
  main=r'''
int main(int argc,char**argv){
 assert(argc==3);
 struct {uint8_t pre[16],fb[76800],post[16];} destination;
 struct {uint16_t pre[8],palette[256],post[8];} pal;
 memset(&destination,0xa5,sizeof destination);memset(&pal,0xa5,sizeof pal);
 load(argv[1]);reset();
 assert(cgpack_read_timed_frame(35,1,100,destination.fb,pal.palette));
 for(unsigned i=0;i<57600;i++)assert(destination.fb[i]==(uint8_t)i);
 for(unsigned i=0;i<512;i++)assert(((uint8_t*)pal.palette)[i]==(uint8_t)i);
 for(unsigned call=1;call<=3;call++){
  reset();fail_read=call;
  assert(!cgpack_read_timed_frame(35,1,100,destination.fb,pal.palette));
  assert(unmounted==1 && invalidated==1);
 }
 const unsigned offsets[]={0,4,6,8,12,16,20,24,28,32,512,1023,1024,58623};
 for(unsigned i=0;i<sizeof(offsets)/sizeof(*offsets);i++){
  reset();drive[offsets[i]]^=1;
  assert(!cgpack_read_timed_frame(35,1,100,destination.fb,pal.palette));
  drive[offsets[i]]^=1;
 }
 reset();assert(!cgpack_read_timed_frame(35,4096,100,destination.fb,pal.palette));
 reset();unsigned original=drive_length;drive_length--;
 assert(!cgpack_read_timed_frame(35,1,100,destination.fb,pal.palette));
 drive_length=original;
 load(argv[2]);uint16_t index;uint32_t stamp;
 for(unsigned ms=0;ms<300;ms++){
  reset();assert(cgpack_select_timed_frame(35,ms,destination.fb,&index,&stamp));
  assert(index==ms/100 && stamp==index*100);
 }
 reset();assert(!cgpack_select_timed_frame(35,300,destination.fb,&index,&stamp));
 assert(!unmounted); /* ordinary expiry */
 for(unsigned i=0;i<20;i++){
  reset();drive[i]^=1;
  assert(!cgpack_select_timed_frame(35,0,destination.fb,&index,&stamp));
  drive[i]^=1;
 }
 for(unsigned i=0;i<16;i++){
  assert(destination.pre[i]==0xa5 && destination.post[i]==0xa5);
  assert(((uint8_t*)pal.pre)[i]==0xa5 && ((uint8_t*)pal.post)[i]==0xa5);
 }
 puts("portable actual-reader bounds, guards, CRC, short reads and schedule PASS");
 return 0;
}
'''
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);frame=export_timed_frame(p,35,1,100,bytes(range(256))*225,bytes(range(256))*2)
   # Separate frame directory so schedule export cannot overwrite test frame.
   sched=export_timed_schedule(p/"schedule",35,100,[(bytes(range(256))*225,bytes(range(256))*2)]*3)
   (p/"reader.c").write_text(prefix+reader+main)
   subprocess.run(["cc","-std=c11","-O1","-g","-fsanitize=address,undefined","-fno-omit-frame-pointer",str(p/"reader.c"),"-o",str(p/"reader")],check=True)
   result=subprocess.run([str(p/"reader"),str(frame),str(sched)],check=True,text=True,capture_output=True)
   print(result.stdout.strip())
if __name__=="__main__":unittest.main()
