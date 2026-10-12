import sys,tempfile,unittest
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from image_resolve import ImageResolver,ImageDef,CG_SIZE,CG_FULL_SIZE

class OverlayTests(unittest.TestCase):
 def fixture(self):
  t=tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup)
  r=ImageResolver(Path(t.name)/"raw",Path(t.name)/"build")
  r.scenes=[{"file":"base.png"},{"file":"overlay.png"}]
  Image.new("RGBA",CG_FULL_SIZE,(200,100,50,255)).save(r.cgpack_src_dir/"base.png")
  return r
 def test_vignette_keeps_base_and_numbering(self):
  r=self.fixture();r.table[("vignette",)]=ImageDef(("vignette",),"solid",color=(0,0,0))
  self.assertEqual(r.ch5_overlay_scene(1,0,"vignette"),1)
  with Image.open(r.img_dir/"overlay.png") as im:
   self.assertEqual(im.size,CG_SIZE)
   self.assertTrue(45<=im.getpixel((80,45))[0]<=55)
  self.assertEqual(len(r.scenes),2)
  self.assertEqual(r.scenes[1]["composition"]["base_scene"],0)
 def test_menu_layer_keeps_background_and_clips_bounds(self):
  r=self.fixture();r.table[("splash_glitch_y",)]=ImageDef(("splash_glitch_y",),"solid",color=(0,0,255))
  r.ch5_overlay_scene(1,0,"splash_glitch_y")
  with Image.open(r.img_dir/"overlay.png") as im:
   self.assertEqual(im.size,CG_SIZE)
   self.assertEqual(im.getpixel((0,0)),(200,100,50,255))
 def test_vignette_stays_above_later_menu_and_all_girls(self):
  r=self.fixture()
  for tag in ("vignette","splash_glitch2","splash_glitch_m","splash_glitch_n","splash_glitch_y"):
   r.table[(tag,)]=ImageDef((tag,),"solid",color=(0,0,0) if tag=="vignette" else (255,255,255))
  r.ch5_overlay_scene(1,0,"vignette")
  for i,tag in enumerate(("splash_glitch2","splash_glitch_m","splash_glitch_n","splash_glitch_y"),2):
   r.scenes.append({"file":"stage%d.png"%i})
   r.ch5_overlay_scene(i,i-1,tag,vignette_top=True)
   with Image.open(r.cgpack_src_dir/r.scenes[i]["file"]) as im:
    self.assertTrue(62<=im.getpixel((10,10))[0]<=65)
   self.assertEqual(r.scenes[i]["composition"]["semantic_z_order"],[2,3])
  self.assertEqual(len(r._ch5_underlays),5)
 def test_missing_overlay_is_error(self):
  r=self.fixture();r.table[("vignette",)]=ImageDef(("vignette",),"unsupported")
  with self.assertRaises(ValueError):r.ch5_overlay_scene(1,0,"vignette")
if __name__=="__main__":unittest.main()
