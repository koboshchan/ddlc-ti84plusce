"""Four ch5 originals only, independently ZX0-compressed 8-row bands.
No changes to global scenes, CG slots or external CG pack."""
from pathlib import Path
import struct,json,hashlib,subprocess
from PIL import Image
from ch5_blend_luts import generate_blend_luts
NAMES=("s_kill_bg","s_kill_bg2","s_kill","s_kill2")
def generate(raw_dir,build_dir,appvar_dir,write_appvar):
 images=[]
 for name in NAMES:
  im=Image.open(raw_dir/"images/cg"/(name+".png")).convert("RGBA")
  images.append(im.resize((320,180) if "_bg" in name else (72,263),Image.Resampling.NEAREST))
 # Common palette ensures per-layer compositing shares palette indices.
 atlas=Image.new("RGB",(320,886));y=0
 for im in images:atlas.paste(im.convert("RGB"),(0,y));y+=im.height
 common=atlas.quantize(colors=255,method=Image.Quantize.MEDIANCUT)
 palette=common.getpalette()[:768];palette += [0]*(768-len(palette))
 rgb1555=[]
 for i in range(256):
  r,g,b=palette[3*i:3*i+3];rgb1555.append(((r>>3)<<10)|((g>>3)<<5)|(b>>3))
 output=[];reports=[]
 for idx,(name,im) in enumerate(zip(NAMES,images)):
  indexed=bytearray(im.convert("RGB").quantize(palette=common,dither=Image.Dither.NONE).tobytes())
  alpha=im.getchannel("A").tobytes()
  for p,a in enumerate(alpha):
   if a<128:indexed[p]=255
  bands=[]
  for y in range(0,im.height,8):
   data=bytes(indexed[y*im.width:min(y+8,im.height)*im.width])
   src=appvar_dir/"_motion-in.bin";dst=appvar_dir/"_motion-out.bin";src.write_bytes(data)
   subprocess.run(["convbin","-i",str(src),"-j","bin","-c","zx0","-k","bin","-o",str(dst)],check=True,capture_output=True)
   bands.append(dst.read_bytes())
  # u16 offsets relative to payload start, fixed max decoded band 2560B.
  header=struct.pack("<4sHHBB","DMT1".encode(),im.width,im.height,8,len(bands))
  start=len(header)+2*len(bands);offsets=[];payload=b""
  for band in bands:offsets.append(start+len(payload));payload+=band
  data=header+struct.pack("<"+"H"*len(offsets),*offsets)+payload
  assert len(data)<=65503
  output.append(write_appvar(data,"DMOT"+str(idx),appvar_dir))
  reports.append(dict(name=name,payload_bytes=len(data),width=im.width,height=im.height,sha256=hashlib.sha256(data).hexdigest(),bands=len(bands),decoded_band_max=im.width*8))
 
 output.append(write_appvar(struct.pack("<256H",*rgb1555),"DMOTPAL",appvar_dir))
 # Exhaustive RGB1555 -> fixed palette map, generated offline. Lowest index
 # wins equal distances, matching the importer; transparent 255 excluded.
 # Flash-only 32768B table avoids per-pixel palette search or heap caches.
 channels=[((v>>10)&31,(v>>5)&31,v&31) for v in rgb1555[:255]]
 nearest=bytearray(32768)
 for value in range(32768):
  rr,gg,bb=(value>>10)&31,(value>>5)&31,value&31
  best=100000;chosen=0
  for index,(pr,pg,pb) in enumerate(channels):
   distance=(rr-pr)**2+(gg-pg)**2+(bb-pb)**2
   if distance<best:best=distance;chosen=index
  nearest[value]=chosen
 output.append(write_appvar(bytes(nearest),"DMOTRGB",appvar_dir))
 groups,tables=generate_blend_luts(rgb1555,nearest)
 output.append(write_appvar(groups,"DMOTBGM",appvar_dir))
 for level,table in enumerate(tables):
  output.append(write_appvar(table,"DMLUT%02d"%level,appvar_dir))

 manifest=json.loads((build_dir/"manifest.json").read_text())
 entries=manifest["scenes"]
 records=bytearray()
 for sid,entry in enumerate(entries):
  layers=entry.get("original_layers",[])
  if not layers:continue
  pairs=[]
  for item in sorted(layers,key=lambda a:a["zorder"]):
   if item.get("hidden"):continue
   tag=item["tag"];base=tag.replace("_zoom","")
   if base not in NAMES:raise ValueError("unsupported dedicated motion tag "+tag)
   mode=1 if item["transform"]=="s_kill_bg_start" else 2 if item["transform"]=="s_kill_start" else 0
   if tag.endswith("_zoom"):mode=(3,5,4,6)[NAMES.index(base)]
   pairs.extend((NAMES.index(base),mode))
  if len(pairs)>16:raise ValueError("more than eight authored display instances")
  records.extend(bytes([sid,len(pairs)//2]+pairs+[0]*(16-len(pairs))))
 output.append(write_appvar(bytes(records),"DMOTMAP",appvar_dir))

 (build_dir/"motion-band-audit.json").write_text(json.dumps(reports,indent=2))
 for f in (appvar_dir/"_motion-in.bin",appvar_dir/"_motion-out.bin"):f.unlink(missing_ok=True)
 return output
