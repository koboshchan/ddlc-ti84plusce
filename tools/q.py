
import sys
sys.path.insert(0,"/home/kat/ddlc-opt/tools")
from pp_lib import *
from rpyc_ast import *
m,top=load_rpyc("/home/kat/ddlc-opt/assets/raw/script-ch5.rpyc")
for n in flatten(top):
    l=getattr(n,"linenumber",0)
    if 205<=l<=310:
        k=kind(n)
        if getattr(n,"atl",None) is not None: print(l,k,n.imspec[0]); atl(n.atl,"   ")
        if k=="Say": print(l,"SAY",n.who,repr(n.what))
from PIL import Image
R="/home/kat/ddlc-opt/assets/raw/"
for f in ["images/bg/noise1.jpg","images/bg/vignette.png","images/bg/splash-glitch.png","images/bg/residential.png","gui/menu_bg.png","gui/menu_art_m.png","gui/menu_art_n.png","gui/menu_art_y.png","gui/textbox.png","gui/namebox.png"]:
    im=Image.open(R+f); print(f,im.size,im.mode)
