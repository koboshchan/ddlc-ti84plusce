"""Asset-independent optimization regression tests."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import ImageFont
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import ttf_to_convfont as fonts
import convert_images
import import_game

def glyphs(text):
    records={}
    for block in text.split("Code point: ")[1:]:
        lines=block.splitlines()
        width=int(next(v.split(": ")[1] for v in lines if v.startswith("Width: ")))
        rows=lines[lines.index("Data:")+1:]
        records[int(lines[0])] = (width,rows)
    return records

class Optimizations(unittest.TestCase):
    def test_cardinal_masks_and_advances(self):
        # Pillow's own bundled default font, no extracted game assets.
        with patch.object(fonts.ImageFont,"truetype",return_value=ImageFont.load_default(size=12)):
            fill=glyphs(fonts.build_convfont(Path("unused"),12,"test"))
            outline=glyphs(fonts.build_convfont(Path("unused"),12,"test",outline=True))
        self.assertEqual(len(fill),95)
        for cp,(w,rows) in fill.items():
            ow,orows=outline[cp]
            self.assertEqual(ow-2,w)
            expected=set()
            for y,row in enumerate(rows):
                for x,px in enumerate(row):
                    if px=="#":
                        expected.update((x+1+dx,y+1+dy) for dx,dy in ((0,0),(-1,0),(1,0),(0,-1),(0,1)))
            actual={(x,y) for y,row in enumerate(orows) for x,c in enumerate(row) if c=="#"}
            self.assertEqual(actual,expected)
    def test_poem_is_compressed(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            doc=convert_images.build_yaml({"sprites":[],"backgrounds":[],"scenes":[],"poem_bg":{"file":"poem.png"}},root,root/"out")
            poem=next(x for x in doc["converts"] if x["name"]=="poem_bg")
            self.assertEqual(poem["compress"],"zx0")
            self.assertFalse(poem["width-and-height"])
    def test_converter_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/"img").mkdir()
            with patch.object(import_game.convert_images,"__file__",str(root/"converter.py")):
                p=root/"converter.py";p.write_text("before")
                a=import_game._convimg_cache_key(root,b"{}",8)
                p.write_text("after")
                self.assertNotEqual(a,import_game._convimg_cache_key(root,b"{}",8))
if __name__=="__main__":unittest.main()
