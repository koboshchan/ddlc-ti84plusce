
import sys
sys.path.insert(0,'/home/kat/ddlc-opt/tools')
from rpyc_ast import *
def atl(b,ind):
    for s in b.statements:
        k=type(s).__name__
        if k=='RawMultipurpose':
            parts=[]
            if s.warper: parts+= [s.warper,str(s.duration)]
            elif s.warp_function: parts+=['warp',str(s.warp_function),str(s.duration)]
            parts+=[str(e[0]) + ((' with '+str(e[1])) if e[1] else '') for e in s.expressions]
            for kv in s.properties: parts+=[str(kv[0]),str(kv[1])]
            for sp in s.splines: parts+=['spline',str(sp)]
            print(ind+' '.join(parts))
        elif k=='RawParallel':
            for i,bl in enumerate(s.blocks): print(ind+'parallel#%d:'%i); atl(bl,ind+'    ')
        elif k=='RawChoice':
            for ch in s.choices: print(ind+'choice w=%s:'%ch[0]); atl(ch[1],ind+'    ')
        elif k=='RawRepeat': print(ind+'repeat '+str(s.repeats))
        elif k=='RawTime': print(ind+'time '+str(s.time))
        elif k=='RawOn': print(ind+'on ...',s.handlers.keys())
        elif k=='RawEvent': print(ind+'event '+str(s.name))
        elif k=='RawBlock': print(ind+'block:'); atl(s,ind+'    ')
        else: print(ind+'?'+k, getattr(s,'__dict__',''))
def walk_unused(nodes,lo,hi,ind=''):
    for n in nodes:
        k=type(n).__name__
        ln=getattr(n,'linenumber',0)
        if k=='Init': walk(n.block,lo,hi,ind); continue
        if lo<=ln<=hi:
            nm=getattr(n,'imgname',None) or getattr(n,'varname',None) or getattr(n,'imspec',None)
            code=getattr(n,'code',None)
            src=code._state[1] if code is not None and hasattr(code,'_state') else ''
            print('%sL%d %s %s %s'%(ind,ln,k,nm,src[:300].replace('\n',' | ')))
            a=getattr(n,'atl',None)
            if a is not None and hasattr(a,'statements'): atl(a,ind+'    ')
            if k in('Transform',) : atl(n.atl,ind+'    ')
