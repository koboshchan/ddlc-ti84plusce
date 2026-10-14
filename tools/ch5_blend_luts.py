"""Offline bounded approximate palette blend tables, pure Python."""
import struct
def generate_blend_luts(palette,nearest):
 channels=[((v>>10)&31,(v>>5)&31,v&31) for v in palette]
 def dist(a,b):return sum((x-y)**2 for x,y in zip(a,b))
 centers=[channels[0]]
 for _ in range(31):
  centers.append(max(channels[:255],key=lambda c:min(dist(c,p) for p in centers)))
 for _ in range(12):
  groups=[min(range(32),key=lambda j:dist(c,centers[j])) for c in channels]
  new=[]
  for j in range(32):
   members=[c for c,g in zip(channels[:255],groups[:255]) if g==j]
   new.append(tuple((sum(c[k] for c in members)+len(members)//2)//len(members) for k in range(3)) if members else centers[j])
  if new==centers:break
  centers=new
 groups=bytes(min(range(32),key=lambda j:dist(c,centers[j])) for c in channels)
 tables=[]
 for level in range(16):
  alpha=(level*1024+7)//15
  out=bytearray()
  for fg in channels:
   for bg in centers:
    r,g,b=[(f*alpha+d*(1024-alpha)+512)>>10 for f,d in zip(fg,bg)]
    out.append(nearest[(r<<10)|(g<<5)|b])
  tables.append(bytes(out))
 return groups,tables
