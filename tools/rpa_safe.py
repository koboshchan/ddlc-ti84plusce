
import pickle,zlib,io,sys
class U(pickle.Unpickler):
    def find_class(self,m,n):
        raise pickle.UnpicklingError('blocked %s.%s'%(m,n))
def index(path):
    with open(path,'rb') as f:
        head=f.readline().split()
        assert head[0]==b'RPA-3.0'
        off=int(head[1],16); key=int(head[2],16)
        f.seek(off); data=zlib.decompress(f.read())
    idx=U(io.BytesIO(data)).load()
    out={}
    for k,v in idx.items():
        o,l,pre=v[0] if len(v[0])==3 else (v[0][0],v[0][1],b'')
        out[k if isinstance(k,str) else k.decode()]=(o^key,l^key,pre)
    return out
def read(path,name):
    ix=index(path); o,l,pre=ix[name]
    with open(path,'rb') as f:
        f.seek(o); return (pre.encode() if isinstance(pre,str) else pre)+f.read(l-len(pre))
if __name__=='__main__':
    p=sys.argv[1]
    if len(sys.argv)==2:
        for k in index(p): print(k)
    else:
        open(sys.argv[3],'wb').write(read(p,sys.argv[2]))
