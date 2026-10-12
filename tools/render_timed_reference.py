#!/usr/bin/env python3
"""Nonvisual original-source timed motion + vignette renderer.
Never emits screenshots or displays imagery. USB indices are quantized once
from authored RGB composition, keeping UI-reserved colors byte-identical.
Explicit per-layer ages are required; no guessed cross-scene timing.
"""
import argparse, hashlib, json, math, struct
from pathlib import Path
import numpy as np
from PIL import Image
from scipy.spatial import cKDTree
from export_cgpack import export_timed_manifest
NAMES=("s_kill_bg","s_kill_bg2","s_kill","s_kill2")
def sha(b): return hashlib.sha256(b).hexdigest()
def ease(t): return (1-math.cos(math.pi*t))/2
def dizzy(ms,bg):
 t=ms/1000; ends=(0,10,5,-5,-3,-10,0,5,0)
 x=t%6;seg=int(x/.75);phase=(x-seg*.75)/.75
 dx=ends[seg]+(ends[seg+1]-ends[seg])*ease(phase)
 y=t%4
 if y<1:dy=5*ease(y)
 elif y<3:dy=5-10*ease((y-1)/2)
 else:dy=-5+5*(1-math.cos((y-3)*math.pi/2))
 f=.25 if bg else 1
 return dx*f,dy*f
def pose(item,ms,w,h):
 tag=item["tag"];bg="_bg" in tag
 z=1.;xa=ya=0.;dy=0.;alpha=float(item.get("alpha",1))
 if item["transform"]=="s_kill_bg_start":
  z=1.1-.1*min(ms/4000,1);xa=ya=.5
 elif item["transform"]=="s_kill_start":
  q=min(ms/4000,1);z=.8-.05*q;xa=.3+.015*q;ya=.25;dy=10*q
 elif tag.endswith("_zoom"):
  z=2.;xa=.2 if bg else .5;ya=.3 if bg else .05
 ox=(1280-w*z)*xa;oy=(720-h*z)*ya+dy
 if tag.endswith("_zoom"):
  dx,dy=dizzy(ms,bg);ox+=dx;oy+=dy
 if tag in ("s_kill_bg2_zoom","s_kill2_zoom"):
  ends=(.2,.2,.25,.2,.3,.25,.35,.3,.35,.2) if bg else (.3,.3,.4,.3,.5,.4,.6,.5,.6,.4)
  phase=ms%2250/250;seg=int(phase)
  alpha*=ends[seg]+(ends[seg+1]-ends[seg])*(phase-seg)
 return ox,oy,z,alpha
def compose(layers,ages,originals,elapsed,vignette,vignette_age):
 dst=np.zeros((180,320,3),np.float64)
 for i,item in enumerate(layers):
  if item.get("hidden"):continue
  raw=originals[item["tag"].replace("_zoom","")]
  hh,w=raw.shape[:2];ox,oy,z,a=pose(item,ages[i]+elapsed,w,hh)
  xs=np.floor((np.arange(320)*4-ox)/z).astype(np.int32)
  ys=np.floor((np.arange(180)*4-oy)/z).astype(np.int32)
  valid=(ys[:,None]>=0)&(ys[:,None]<hh)&(xs[None,:]>=0)&(xs[None,:]<w)
  sample=raw[np.clip(ys,0,hh-1)[:,None],np.clip(xs,0,w-1)[None,:]]
  aa=sample[:,:,3]/255*a*valid
  dst=sample[:,:,:3]*aa[:,:,None]+dst*(1-aa[:,:,None])
 # source vignette black-overlay displayable alpha ramp, already above motion.
 if vignette_age is not None:
  a=.75*min(max((vignette_age+elapsed)/3000,0),1)
  va=vignette[:,:,3]/255*a
  dst=vignette[:,:,:3]*va[:,:,None]+dst*(1-va[:,:,None])
 return np.clip(np.floor(dst+.5),0,255).astype(np.uint8)
def quantize(rgb,ui):
 im=Image.fromarray(rgb).quantize(colors=206,method=Image.Quantize.MEDIANCUT,dither=Image.Dither.NONE)
 colors=np.asarray(im.getpalette(),dtype=np.uint8).reshape(-1,3)[:206]>>3
 p=np.frombuffer(ui,dtype="<u2").copy()
 p[50:]=((colors[:,0].astype(np.uint16)<<10)|(colors[:,1].astype(np.uint16)<<5)|colors[:,2])
 # Nearest search in exact hardware RGB1555, deterministic minimum index ties.
 channels=np.column_stack(((p>>10)&31,(p>>5)&31,p&31)).astype(np.int16)
 values=(rgb>>3).reshape(-1,3).astype(np.int16)
 unique,inverse=np.unique(values,axis=0,return_inverse=True)
 nearest=np.empty(len(unique),np.uint8)
 for off in range(0,len(unique),1024):
  distances=((unique[off:off+1024,None,:].astype(np.int32)-channels[None,:,:])**2).sum(axis=2)
  nearest[off:off+1024]=distances.argmin(axis=1).astype(np.uint8)
 return nearest[inverse].tobytes(),p.astype("<u2").tobytes()
def generate(root,out,clocks,duration,period):
 if duration%period or not 1<=duration//period<=4096:raise ValueError("invalid cadence")
 manifest=json.loads((root/"build/manifest.json").read_text())
 specs=json.loads(clocks.read_text())
 if specs.get("version")!=1:raise ValueError("unknown clocks")
 originals={n:np.asarray(Image.open(root/"assets/raw/images/cg"/(n+".png")).convert("RGBA")) for n in NAMES}
 vig=np.asarray(Image.open(root/"assets/raw/images/bg/vignette.png").convert("RGBA").resize((320,180),Image.Resampling.NEAREST))
 out.mkdir(parents=True,exist_ok=True);seqs=[]
 for spec in specs["sequences"]:
  sid=spec["scene"];entry=manifest["scenes"][sid]
  layers=sorted(entry["original_layers"],key=lambda x:x["zorder"])
  ages=spec["layer_ages_ms"]
  if len(ages)!=len(layers) or any(type(a) is not int or a<0 for a in ages):raise ValueError("missing layer clocks")
  ui=(root/"build/gfx"/("pal_cg_%03d.bin"%entry["cg_palette_index"])).read_bytes()
  if len(ui)!=512:raise ValueError("invalid UI palette")
  seq=dict(scene=sid,period_ms=period,frames=[],source_layer_ages_ms=ages,vignette_age_ms=spec.get("vignette_age_ms"))
  for index in range(duration//period):
   at=index*period
   rgb=compose(layers,ages,originals,at,vig,spec.get("vignette_age_ms"))
   pixels,palette=quantize(rgb,ui)
   for i in list(range(8))+list(range(10,50)):
    assert palette[2*i:2*i+2]==ui[2*i:2*i+2]
   prefix="s%03d-f%04d"%(sid,index)
   (out/(prefix+".pixels")).write_bytes(pixels);(out/(prefix+".palette")).write_bytes(palette)
   seq["frames"].append(dict(timestamp_ms=at,pixels=prefix+".pixels",pixels_sha256=sha(pixels),palette=prefix+".palette",palette_sha256=sha(palette),rgb_reference_sha256=sha(rgb.tobytes())))
  seqs.append(seq)
  print(json.dumps(dict(scene=sid,frames=len(seq["frames"]),nonvisual=True)),flush=True)
 data=dict(version=1,sequences=seqs,source_hashes={n:sha((root/"assets/raw/images/cg"/(n+".png")).read_bytes()) for n in NAMES},clocks_sha256=sha(clocks.read_bytes()),scope=specs["scope"],noise="excluded; applied by real ASM after frame")
 (out/"manifest.json").write_text(json.dumps(data,indent=2))
 export_timed_manifest(out/"manifest.json",out/"DDLC",len(manifest["scenes"]))
 return data
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--root",type=Path,required=True);p.add_argument("--output",type=Path,required=True);p.add_argument("--clocks",type=Path,required=True);p.add_argument("--duration-ms",type=int,default=12000);p.add_argument("--period-ms",type=int,default=100);a=p.parse_args()
 generate(a.root,a.output,a.clocks,a.duration_ms,a.period_ms)
