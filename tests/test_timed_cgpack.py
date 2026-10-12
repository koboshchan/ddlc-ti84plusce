"""Synthetic format tests only, never evidence of USB driver execution."""
import hashlib,json,struct,tempfile,unittest,zlib
from pathlib import Path
from export_cgpack import export_timed_frame,export_timed_schedule,export_timed_manifest

def validate_frame(b,scene,index,time):
    if len(b)!=58880: raise ValueError("length")
    if struct.unpack_from("<4sBBHIIHHII",b)!=(b"DTV1",scene,0,index,time,57600,320,180,512,1024):
        raise ValueError("header")
    if struct.unpack_from("<II",b,28)!=(zlib.crc32(b[512:1024]),zlib.crc32(b[1024:58624])):
        raise ValueError("CRC")
    return b[1024:58624],b[512:1024]

class TimedPack(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.pixels=bytes(range(256))*225;self.palette=bytes(range(256))*2
    def test_roundtrip_and_corruption(self):
        b=export_timed_frame(self.root,35,1,100,self.pixels,self.palette).read_bytes()
        self.assertEqual(validate_frame(b,35,1,100),(self.pixels,self.palette))
        for off in [0,4,6,8,12,16,20,24,28,32,512,1023,1024,58623]:
            bad=bytearray(b);bad[off]^=1
            with self.assertRaises(ValueError): validate_frame(bad,35,1,100)
        for n in [0,512,1024,58624,58879]:
            with self.assertRaises(ValueError):validate_frame(b[:n],35,1,100)
    def test_schedule(self):
        p=export_timed_schedule(self.root,35,100,[(self.pixels,self.palette)]*3)
        b=p.read_bytes()
        self.assertEqual(len(b),512)
        self.assertEqual(struct.unpack_from("<4sBBHII",b),(b"DTM1",35,0,3,100,300))
        self.assertEqual(struct.unpack_from("<I",b,16)[0],zlib.crc32(b[:16]))
        for i in range(3):
            validate_frame((p.parent/f"F{i:04d}.BIN").read_bytes(),35,i,i*100)
        for scene,period,frames in [(-1,100,[(self.pixels,self.palette)]),(35,0,[(self.pixels,self.palette)]),(35,60001,[(self.pixels,self.palette)]),(35,100,[]),(35,100,[(b"",self.palette)])]:
            with self.assertRaises(ValueError):export_timed_schedule(self.root,scene,period,frames)
    def test_reference_manifest(self):
        (self.root/"p.raw").write_bytes(self.pixels);(self.root/"c.raw").write_bytes(self.palette)
        frame=dict(timestamp_ms=0,pixels="p.raw",palette="c.raw",pixels_sha256=hashlib.sha256(self.pixels).hexdigest(),palette_sha256=hashlib.sha256(self.palette).hexdigest())
        m=dict(version=1,sequences=[dict(scene=35,period_ms=100,frames=[frame])])
        path=self.root/"manifest.json";path.write_text(json.dumps(m))
        self.assertEqual(export_timed_manifest(path,self.root/"out",36),1)
        for key,value in [("timestamp_ms",1),("pixels_sha256","0"*64),("pixels","../escape.raw")]:
            bad=json.loads(json.dumps(m));bad["sequences"][0]["frames"][0][key]=value;path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):export_timed_manifest(path,self.root/"badout",36)
        m["sequences"].append(m["sequences"][0]);path.write_text(json.dumps(m))
        with self.assertRaises(ValueError):export_timed_manifest(path,self.root/"badout",36)
if __name__=="__main__":unittest.main()
