#!/usr/bin/env python3
"""Package a separate DDEBUG.8xv debug+perf AppVar for DDLC.8xp auto-loading.

Layout inside DDEBUG AppVar:
    [0..3]   "DDBG" magic
    [4..7]   u32 little-endian uncompressed engine size (raw_engine = engine.bin[2:])
    [8..11]  u32 little-endian CRC-32/ISO-HDLC of the compressed ZX0 stream
    [12..]   raw ZX0 bitstream of raw_engine
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _cedev import find_cedev_tool

TI_APPVAR_MAX = 65503
# Empirical TI-84 Plus CE RAM ceiling with all 151 archived AppVars + arTIfiCE + LibLoad clibs (~29.5 KB)
MAX_RAW_ENGINE = 120000


def build_ddebug(elf_path: Path, out_8xv: Path, report_path: Path | None = None) -> dict:
    convbin = find_cedev_tool("convbin", env_var="CONVBIN_REAL")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        raw_bin = tmp / "engine.bin"
        subprocess.run(
            [str(convbin), "-j", "elf", "-k", "bin", "-i", str(elf_path), "-o", str(raw_bin)],
            check=True,
        )
        data = raw_bin.read_bytes()
        if not data.startswith(b"\xef\x7b"):
            raise SystemExit(f"build_ddebug: {elf_path} binary lacks EF7B ASM token")

        raw_engine = data[2:]
        if not (64 <= len(raw_engine) <= MAX_RAW_ENGINE):
            raise SystemExit(
                f"build_ddebug: raw engine ({len(raw_engine)} B) outside supported 64..{MAX_RAW_ENGINE} B range"
            )

        eng_raw = tmp / "raw.bin"
        eng_zx0 = tmp / "raw.zx0"
        eng_raw.write_bytes(raw_engine)
        subprocess.run(
            [str(convbin), "-j", "bin", "-k", "bin", "-c", "zx0", "-i", str(eng_raw), "-o", str(eng_zx0)],
            check=True,
        )
        zx0_bytes = eng_zx0.read_bytes()
        crc = zlib.crc32(zx0_bytes) & 0xFFFFFFFF

        payload = b"DDBG" + struct.pack("<II", len(raw_engine), crc) + zx0_bytes
        if len(payload) > TI_APPVAR_MAX:
            raise SystemExit(
                f"build_ddebug: compressed DDEBUG payload ({len(payload)} B) exceeds TI AppVar limit ({TI_APPVAR_MAX} B)"
            )

        payload_bin = tmp / "ddebug.bin"
        payload_bin.write_bytes(payload)
        out_8xv.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                str(convbin),
                "-i", str(payload_bin),
                "-j", "bin",
                "-k", "8xv",
                "-n", "DDEBUG",
                "-o", str(out_8xv),
                "-r",
                "-b", "TI-84 Plus CE",
            ],
            check=True,
        )

    info = {
        "name": "DDEBUG",
        "out": str(out_8xv),
        "raw_bytes": len(raw_engine),
        "compressed_bytes": len(zx0_bytes),
        "payload_bytes": len(payload),
        "headroom_bytes": TI_APPVAR_MAX - len(payload),
        "compressed_crc32": f"{crc:08x}",
        "raw_sha256": hashlib.sha256(raw_engine).hexdigest(),
    }
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(info, indent=2) + "\n")
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--elf", type=Path, required=True, help="Compiled debug ELF (.obj)")
    ap.add_argument("--out", type=Path, required=True, help="Output DDEBUG.8xv path")
    ap.add_argument("--report", type=Path, default=None, help="Optional JSON metadata report")
    args = ap.parse_args()

    info = build_ddebug(args.elf, args.out, args.report)
    print(
        f"[success] {info['out']}: raw={info['raw_bytes']} B, "
        f"zx0={info['compressed_bytes']} B, payload={info['payload_bytes']} B "
        f"(headroom {info['headroom_bytes']} B, crc32={info['compressed_crc32']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
