
import sys
sys.path.insert(0,"/home/kat/ddlc-opt/tools")
from rpyc_ast import *
m,top=load_rpyc("/home/kat/ddlc-opt/assets/raw/cgs.rpyc")
for n in flatten(top):
    if kind(n)=="Transform" and n.varname=="dizzy":
        p=n.parameters; print(p.__dict__ if hasattr(p,'__dict__') else p)
    if kind(n)=="Transform" and n.varname=="s_kill_bg_start": print("ok")
