"""Stock convbin wrapper: back up own generated parts, split exact raw halves."""
import sys,subprocess,tempfile,json,hashlib,shutil,time
from pathlib import Path
args=sys.argv[1:]
def value(flag): return args[args.index(flag)+1] if flag in args else None
real=Path.home()/"CEdev/bin/convbin"
if value("-j")!="elf" or value("-n")!="DDLC":
    raise SystemExit(subprocess.call([str(real),*args]))
out=Path(value("-o"));src=Path(value("-i"))
with tempfile.TemporaryDirectory() as d:
    raw=Path(d)/"engine.bin"
    subprocess.run([str(real),"-j","elf","-k","bin","-i",str(src),"-o",str(raw)],check=True)
    data=raw.read_bytes()
    if not data.startswith(bytes.fromhex("EF7B")): raise SystemExit("engine lacks expected EF7B tokens")
    length=len(data)-2;half=(length+1)//2
    if not 0<half<=65503: raise SystemExit("balanced engine exceeds TI DATA cap")
    backup=out.parent/"split-backups"/(str(time.time_ns())+"-"+hashlib.sha256(data).hexdigest()[:12])
    backup.mkdir(parents=True)
    for f in out.parent.glob(out.name+".*.8xv"):
        if f.name.removeprefix(out.name+".").removesuffix(".8xv").isdigit(): shutil.move(str(f),backup/f.name)
    filtered=[];i=0
    while i<len(args):
        if args[i]=="-m": i+=2;continue
        filtered.append(args[i]);i+=1
    subprocess.run([str(real),*filtered,"-m",str(half)],check=True)
    parts=sorted(out.parent.glob(out.name+".*.8xv"))
    if [p.name for p in parts]!=[out.name+".0.8xv",out.name+".1.8xv"]: raise SystemExit("expected exactly two generated code parts")
    from loader.build_guard import apply
    apply(out,parts,data[2:])
    (out.parent/"balanced-split.json").write_text(json.dumps(dict(raw_bytes=length,target=half,engine_sha256=hashlib.sha256(data[2:]).hexdigest(),backup=str(backup),parts=[p.name for p in parts]),indent=2))
