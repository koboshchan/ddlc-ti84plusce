from pathlib import Path
import shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class ComposedBackground(unittest.TestCase):
 @unittest.skipUnless(shutil.which("cc"),"host compiler unavailable")
 def test_scalar_reference_guards_phases_and_fallbacks(self):
  for variant in ("adversarial","adversarial-allphases"):
   with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
    tmp=Path(directory)
    (tmp/"fileioc.h").write_text("#include <stdint.h>\nuint8_t ti_Open(const char*,const char*);void ti_Close(uint8_t);unsigned ti_GetSize(uint8_t);void *ti_GetDataPtr(uint8_t);unsigned ti_Read(void*,unsigned,unsigned,uint8_t);\n")
    (tmp/"graphx.h").write_text("#include <stdint.h>\nextern uint8_t *gfx_vbuffer;void gfx_SetPalette(const uint16_t*,unsigned,unsigned);\n")
    src=(ROOT/"src/ch5_motion_render.c").read_text().replace("clock()","fixture_clock()")
    (tmp/"test.c").write_text((ROOT/"tests"/(variant+"-preamble.h")).read_text()+src+"\n"+(ROOT/"tests"/(variant+"-cases.h")).read_text())
    subprocess.run(["cc","-O2","-fsanitize=address,undefined","-I"+str(tmp),"-I"+str(ROOT/"src"),str(tmp/"test.c"),"-o",str(tmp/"test")],check=True)
    subprocess.run([str(tmp/"test")],check=True)
