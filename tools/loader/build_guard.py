"""Inject the CRC-32 split-part integrity preflight into DDLC.8xp's loader."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _cedev import find_cedev_tool

TI_USER_MEM = 0xD1A881
TI_DATA_LIMIT = 65505


def _extract_var_payload(blob: bytes) -> tuple[bytes, int]:
    """Return (raw variable content excluding 2-byte length, data-length field offset)."""
    header_len = struct.unpack_from("<H", blob, 55)[0]
    data_len_off = 55 + 2 + header_len
    data_len = struct.unpack_from("<H", blob, data_len_off)[0]
    data = blob[data_len_off + 2 : data_len_off + 2 + data_len]
    if len(data) != data_len or int.from_bytes(data[:2], "little") != data_len - 2:
        raise ValueError("Malformed TI variable container")
    return data[2:], data_len_off


def _assemble_guard(values: list[tuple[int, int]], org_addr: int) -> bytes:
    """Assemble integrity.s at @p org_addr using CEdev's bundled GNU binutils."""
    as_bin = find_cedev_tool("z80-none-elf-as", subdir="binutils/bin")
    ld_bin = find_cedev_tool("z80-none-elf-ld", subdir="binutils/bin")
    objcopy_bin = find_cedev_tool("z80-none-elf-objcopy", subdir="binutils/bin")

    src_template = (Path(__file__).with_name("integrity.s")).read_text()
    equates = "\n".join(
        f"\t.equ\tLENGTH{i}, {length}\n\t.equ\tCRC{i}, 0x{crc:08X}"
        for i, (length, crc) in enumerate(values)
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        asm_path = tmp / "guard.s"
        obj_path = tmp / "guard.o"
        elf_path = tmp / "guard.elf"
        bin_path = tmp / "guard.bin"

        asm_path.write_text(f"{equates}\n{src_template}")
        subprocess.run(
            [str(as_bin), "-march=ez80+full", str(asm_path), "-o", str(obj_path)],
            check=True,
        )
        subprocess.run(
            [
                str(ld_bin),
                "-Ttext",
                f"0x{org_addr:06X}",
                "--entry=_guard_start",
                "-o",
                str(elf_path),
                str(obj_path),
            ],
            check=True,
        )
        subprocess.run(
            [str(objcopy_bin), "-O", "binary", str(elf_path), str(bin_path)],
            check=True,
        )
        return bin_path.read_bytes()


def apply(out_8xp: Path, parts: list[Path], raw_engine: bytes) -> dict:
    """Prepend the split-code CRC32 check to @p out_8xp and write loader-integrity.json."""
    contents = [_extract_var_payload(p.read_bytes())[0] for p in parts]
    if b"".join(contents) != raw_engine:
        raise ValueError("Split code AppVars do not concatenate to raw engine bytes")

    values = [(len(chunk), zlib.crc32(chunk) & 0xFFFFFFFF) for chunk in contents]

    raw_8xp = out_8xp.read_bytes()
    code, data_len_off = _extract_var_payload(raw_8xp)
    if code[:2] != b"\xef\x7b":
        raise ValueError("Loader program lacks expected EF7B eZ80 ASM token")

    # Locate stock convbin split-extractor prologue (`ld hl, ... ; ld de, 0xD09466`).
    pos = code.find(bytes.fromhex("116694D0"))
    if pos < 4 or code[pos - 4] != 0x21:
        raise ValueError("Could not locate stock convbin split-loader prologue")

    header = code[: pos - 4]
    stock = bytearray(code[pos - 4 :])

    org_addr = TI_USER_MEM + len(header) - 2
    guard = _assemble_guard(values, org_addr)

    # Shift stock extractor's embedded source pointer past the injected guard.
    orig_ptr = int.from_bytes(stock[1:4], "little")
    stock[1:4] = (orig_ptr + len(guard)).to_bytes(3, "little")

    patched_code = header + guard + bytes(stock)
    patched_data = struct.pack("<H", len(patched_code)) + patched_code

    entry_hdr = bytearray(raw_8xp[55:data_len_off])
    entry_hdr[2:4] = struct.pack("<H", len(patched_data))
    section = bytes(entry_hdr) + struct.pack("<H", len(patched_data)) + patched_data

    file_hdr = bytearray(raw_8xp[:55])
    file_hdr[53:55] = struct.pack("<H", len(section))
    checksum = struct.pack("<H", sum(section) & 0xFFFF)
    out_8xp.write_bytes(bytes(file_hdr) + section + checksum)

    for i, (length, crc) in enumerate(values):
        expected_rec = (
            bytes([21])
            + f"DDLC{i}".encode("ascii")
            + bytes(3)
            + struct.pack("<HI", length, crc)
        )
        if expected_rec not in guard:
            raise RuntimeError(f"Missing DDLC{i} metadata record in assembled guard")

    report = {
        "parts": [
            {
                "name": f"DDLC{i}",
                "raw_length": length,
                "crc32": f"{crc:08x}",
                "sha256": hashlib.sha256(contents[i]).hexdigest(),
            }
            for i, (length, crc) in enumerate(values)
        ],
        "guard_bytes": len(guard),
        "loader_DATA": len(patched_data),
        "loader_headroom": TI_DATA_LIMIT - len(patched_data),
        "engine_sha256": hashlib.sha256(raw_engine).hexdigest(),
    }
    (out_8xp.parent / "loader-integrity.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    return report
