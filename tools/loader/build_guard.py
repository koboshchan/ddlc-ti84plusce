from pathlib import Path
import zlib,struct,subprocess,json,hashlib
def content(b):
 h=struct.unpack_from("<H",b,55)[0];q=55+2+h
 n=struct.unpack_from("<H",b,q)[0];data=b[q+2:q+2+n]
 assert len(data)==n and int.from_bytes(data[:2],"little")==n-2
 return data[2:],q
def apply(out,parts,raw):
 here=Path(__file__).parent
 contents=[content(p.read_bytes())[0] for p in parts]
 assert b"".join(contents)==raw
 values=[(len(x),zlib.crc32(x)&0xffffffff) for x in contents]
 (here/"expected.inc").write_text("\n".join(f"LENGTH{i} := {n}\nCRC{i} := {crc}" for i,(n,crc) in enumerate(values))+"\n")
 assembler=Path.home()/"ti/opt/fasmg-source/core/fasmg.x64"
 b=out.read_bytes();code,q=content(b)
 assert code[:2]==bytes.fromhex("EF7B")
 pos=code.find(bytes.fromhex("116694D0"));assert pos>=4
 header=code[:pos-4];stock=bytearray(code[pos-4:]);assert stock[0]==0x21
 asm=(here/"integrity.asm").read_text().replace("org ti.userMem",f"org ti.userMem+{len(header)-2}")
 (here/"generated.asm").write_text(asm)
 subprocess.run([str(assembler),"generated.asm","guard.bin"],cwd=here,check=True)
 guard=(here/"guard.bin").read_bytes()
 stock[1:4]=(int.from_bytes(stock[1:4],"little")+len(guard)).to_bytes(3,"little")
 code=header+guard+stock;data=struct.pack("<H",len(code))+code
 entry=bytearray(b[55:q]);entry[2:4]=struct.pack("<H",len(data))
 section=bytes(entry)+struct.pack("<H",len(data))+data
 head=bytearray(b[:55]);head[53:55]=struct.pack("<H",len(section))
 out.write_bytes(bytes(head)+section+struct.pack("<H",sum(section)&65535))
 report=dict(parts=[dict(name=f"DDLC{i}",raw_length=n,crc32=f"{crc:08x}",sha256=hashlib.sha256(contents[i]).hexdigest()) for i,(n,crc) in enumerate(values)],guard_bytes=len(guard),loader_DATA=len(data),loader_headroom=65505-len(data),engine_sha256=hashlib.sha256(raw).hexdigest())
 (out.parent/"loader-integrity.json").write_text(json.dumps(report,indent=2))
 for i,(n,crc) in enumerate(values):
  assert bytes([21])+f"DDLC{i}".encode()+bytes(3)+struct.pack("<HI",n,crc) in guard
 assert b"".join(content(p.read_bytes())[0] for p in parts)==raw
 return report
