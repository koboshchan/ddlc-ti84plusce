import struct,sys,unittest
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from ch5_motion_assets import motion_palette,UI_INDICES,MOTION_INDICES
class MotionPaletteTests(unittest.TestCase):
 def fixture(self):
  p=[0]*256
  for i in UI_INDICES:p[i]=((i*997)&32767)|(32768 if i%2 else 0)
  p[1]=0;p[2]=65535
  im=Image.new("RGBA",(32,32))
  im.putdata([(x*7%256,x*13%256,x*19%256,255) for x in range(1024)])
  return p,[im]*4
 def test_fixed_ui_words_preserved_exactly(self):
  p,images=self.fixture();common,words=motion_palette(images,struct.pack("<256H",*p))
  for i in UI_INDICES:self.assertEqual(words[i],p[i])
  self.assertEqual(len(MOTION_INDICES),207)
  self.assertEqual(words[255],words[1])
  rgb=common.getpalette()
  for i in UI_INDICES:self.assertEqual(rgb[3*i+1],((p[i]>>5)&31)*8+((p[i]>>15)&1)*4)
 def test_opaque_black_and_transparent_alpha_are_distinct(self):
  p,images=self.fixture();common,_=motion_palette(images,struct.pack("<256H",*p))
  im=Image.new("RGBA",(2,1));im.putdata([(0,0,0,255),(0,0,0,0)])
  data=bytearray(im.convert("RGB").quantize(palette=common,dither=Image.Dither.NONE).tobytes())
  data=bytearray(1 if c==255 else c for c in data)
  for x,a in enumerate(im.getchannel("A").tobytes()):
   if a<128:data[x]=255
  self.assertNotEqual(data[0],255);self.assertEqual(data[1],255)
 def test_short_palette_rejected(self):
  with self.assertRaises(ValueError):motion_palette([Image.new("RGB",(1,1))],bytes(511))
if __name__=="__main__":unittest.main()
