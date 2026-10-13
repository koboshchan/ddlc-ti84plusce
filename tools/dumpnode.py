
import sys
sys.path.insert(0,'/home/kat/ddlc-opt/tools')
from rpyc_ast import *
def show(o,d=0,seen=None):
    p='  '*d
    if d>9: return
    if hasattr(o,'__dict__') and not isinstance(o,type):
        st=o.__dict__
        print(p+type(o).__name__, {k:v for k,v in st.items() if isinstance(v,(str,int,float,tuple,type(None))) and k not in('filename',)} if d>0 else '')
        for k,v in st.items():
            if isinstance(v,(list,tuple)) or hasattr(v,'__dict__'):
                if k in('location','parameters'): continue
                print(p+' .'+k); show(v,d+1)
    elif isinstance(o,(list,tuple)):
        for x in o: show(x,d+1) if not isinstance(x,(str,int,float,type(None))) else print(p+'  '+repr(x))
f,lo,hi=sys.argv[1],int(sys.argv[2]),int(sys.argv[3])
m,top=load_rpyc('/home/kat/ddlc-opt/assets/raw/'+f)
for n in top:
    if lo<=getattr(n,'linenumber',0)<=hi: show(n,1)
