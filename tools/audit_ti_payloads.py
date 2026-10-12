"""Strict TI entry audit. Format: https://merthsoft.com/linkguide/ti83+/fformat.html"""
import argparse, hashlib, json, struct, zipfile, subprocess, tempfile, os
from pathlib import Path
LIMIT=65505
def parse(b, source):
    def u(p):
        if p+2>len(b): raise ValueError("truncated length")
        return struct.unpack_from("<H",b,p)[0]
    if b[:11]!=b"**TI83F*\x1a\x0a\x00": raise ValueError("invalid TI signature")
    end=55+u(53)
    if end+2!=len(b): raise ValueError("file section length mismatch")
    if sum(b[55:end])&65535!=u(end): raise ValueError("checksum mismatch")
    rows=[]; p=55
    while p<end:
        h=u(p); n=u(p+2)
        if h not in (11,13): raise ValueError("unsupported entry header")
        q=p+2+h
        if q+2+n>end or u(q)!=n: raise ValueError("entry bounds/duplicate length mismatch")
        data=b[q+2:q+2+n]; typ=b[p+4]
        if typ not in (5,6,21): raise ValueError("unexpected variable type")
        if n<2 or int.from_bytes(data[:2],"little")!=n-2: raise ValueError("internal program/AppVar length mismatch")
        rows.append(dict(source=source,name=b[p+5:p+13].rstrip(b"\0").decode("ascii"),type=typ,
                         data_bytes=n,content_bytes=n-2,headroom=LIMIT-n,passes=n<=LIMIT))
        p=q+2+n
    if p!=end or not rows: raise ValueError("invalid entry count")
    return rows
def main():
    a=argparse.ArgumentParser();a.add_argument("bundle",type=Path);a.add_argument("--out",type=Path,required=True);a.add_argument("--engine",type=Path);v=a.parse_args()
    rows=[]; errors=[]; dependency_checks=[]
    with zipfile.ZipFile(v.bundle) as z:
        for name in z.namelist():
            if name.endswith("/") or name in ("METADATA","_CHECKSUM"): continue
            try: rows.extend(parse(z.read(name),name))
            except Exception as e: errors.append(dict(source=name,error=str(e)))
    if v.engine:
        parts=sorted(v.engine.parent.glob(v.engine.name+".*.8xv"))
        loader=v.engine.read_bytes()
        entry_start=55+2+int.from_bytes(loader[55:57],"little")+2
        data=loader[entry_start:-2]
        required=[]
        pos=len(data)-10
        while pos>=0 and data[pos]==21:
            name=data[pos+1:pos+9].rstrip(b"\0").decode("ascii")
            required.insert(0,name);pos-=9
        actual=[parse(p.read_bytes(),str(p))[0]["name"] for p in parts]
        with tempfile.TemporaryDirectory() as tmp:
            raw_path=Path(tmp)/"engine.bin"
            converter=os.environ.get("CONVBIN",str(Path.home()/"CEdev/bin/convbin"))
            subprocess.run([converter,"-j","elf","-i",str(v.engine.with_suffix(".obj")),"-k","bin","-o",str(raw_path)],check=True,capture_output=True)
            raw=raw_path.read_bytes()
            chunks=[]
            for part in parts:
                b=part.read_bytes();start=55+2+int.from_bytes(b[55:57],"little")+4
                chunks.append(b[start:-2])
            if raw[:2]!=b"\xef\x7b" or b"".join(chunks)!=raw[2:]:
                errors.append(dict(source=str(v.engine),error="code parts differ from contiguous current ELF engine after ASM tokens"))

        if len(required)!=2 or actual!=required:
            errors.append(dict(source=str(v.engine),error="exactly two loader code dependencies required; loader="+repr(required)+" built="+repr(actual)))

        with zipfile.ZipFile(v.bundle) as z:
            names=set(z.namelist())
            if v.engine.name not in names or z.read(v.engine.name)!=v.engine.read_bytes():
                errors.append(dict(source=str(v.engine),error="packaged engine does not match actual binary"))
            for part in parts:
                entries=parse(part.read_bytes(),str(part))
                dependency_checks.extend(entries)
                if part.name not in names:
                    errors.append(dict(source=str(part),error="missing loader-required engine AppVar"))
                elif z.read(part.name)!=part.read_bytes():
                    errors.append(dict(source=str(part),error="engine AppVar differs from built code"))
                if any(not entry["passes"] for entry in entries):
                    errors.append(dict(source=str(part),error="engine AppVar DATA exceeds cap"))
    mains=[r for r in rows if r["name"]=="DDLC" and r["type"] in (5,6)]
    apps=[r for r in rows if r["type"]==21]
    ok=not errors and len(mains)==1 and bool(apps) and all(r["passes"] for r in rows)
    result=dict(bundle=str(v.bundle),sha256=hashlib.sha256(v.bundle.read_bytes()).hexdigest(),compressed_bytes=v.bundle.stat().st_size,
        limit=LIMIT,measurement="variable data including internal 2-byte length, excluding file/entry wrappers",
        engine_dependencies=dependency_checks,engine_unsplit=not dependency_checks,all_pass=ok,variable_count=len(rows),main=mains,largest_appvar=max(apps,key=lambda r:r["data_bytes"]) if apps else None,variables=rows,errors=errors)
    v.out.parent.mkdir(parents=True,exist_ok=True);v.out.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({k:result[k] for k in ("all_pass","variable_count","main","largest_appvar","errors")}))
    if not ok: raise SystemExit(1)
if __name__=="__main__": main()
