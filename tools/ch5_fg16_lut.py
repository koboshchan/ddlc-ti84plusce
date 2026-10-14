"""Small foreground-only blend LUT. Full background palette and texture detail retained."""
import struct
def generate_fg16_lut(palette,nearest,textures):
 if len(palette)!=256 or len(nearest)!=32768 or len(textures)!=4:
  raise ValueError("Expected fixed motion palette, nearest table and four textures")
 if any(v<0 or v>32767 for v in palette) or any(v>254 for v in nearest):
  raise ValueError("Invalid motion palette or nearest index")
 rgb=[((v>>10)&31,(v>>5)&31,v&31) for v in palette]
 counts=[0]*255
 for layer in (1,3):
  for c in textures[layer]:
   if c!=255:counts[c]+=1
 def dist(a,b):return sum((x-y)**2 for x,y in zip(a,b))
 reps=[max(range(255),key=lambda i:counts[i])]
 for _ in range(15):
  reps.append(max(range(255),key=lambda i:min(dist(rgb[i],rgb[r]) for r in reps)**2*(counts[i]+1)))
 groups=bytes([min(range(16),key=lambda g:dist(rgb[i],rgb[reps[g]])) for i in range(255)]+[0])
 tables=bytearray()
 for alpha in (256,448,640):
  for fg in reps:
   for bg in range(256):
    r,g,b=[(rgb[fg][k]*alpha+rgb[bg][k]*(1024-alpha)+512)>>10 for k in range(3)]
    tables.append(nearest[(r<<10)|(g<<5)|b])
 return groups+bytes(reps)+tables
