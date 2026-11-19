"""Tests for standalone DDEBUG.8xv packaging and DDLC.8xp auto-loader integrity."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

import build_ddebug
from _cedev import find_cedev_tool


ROOT = Path(__file__).resolve().parent.parent


def _extract_8xv_payload(path: Path) -> tuple[str, int, bytes]:
    data = path.read_bytes()
    assert data[:8] == b"**TI83F*", f"bad 8xv signature in {path}"
    entry = data[55:-2]
    hdr_len, data_len, var_type = struct.unpack_from("<HHB", entry, 0)
    assert var_type == 0x15, f"expected AppVar type 0x15, got {var_type:#x}"
    name = entry[5:13].rstrip(b"\x00").decode("ascii")
    flag = entry[14]
    payload_len = struct.unpack_from("<H", entry, 17)[0]
    payload = entry[19 : 19 + payload_len]
    assert len(payload) == payload_len
    return name, flag, payload


def _zx0_decompress(src: bytes, expected_len: int) -> tuple[bytes, int]:
    """Bit-exact simulator of the 24-bit dzx0_turbo routine in tools/loader/integrity.s."""
    out = bytearray(expected_len + 64)
    hl = 0
    de = 0
    a = 0
    cf = 1
    iy = 0xFFFFFF
    bc = 1
    max_off = 0

    def add_a_a() -> bool:
        nonlocal a, cf
        r = a + a
        cf = (r >> 8) & 1
        a = r & 0xFF
        return a != 0

    def rla() -> None:
        nonlocal a, cf
        nc = (a >> 7) & 1
        a = ((a << 1) | cf) & 0xFF
        cf = nc

    def rl8(v: int) -> int:
        nonlocal cf
        nc = (v >> 7) & 1
        v = ((v << 1) | cf) & 0xFF
        cf = nc
        return v

    def rr8(v: int) -> int:
        nonlocal cf
        nc = v & 1
        v = ((cf << 7) | (v >> 1)) & 0xFF
        cf = nc
        return v

    def elias_off() -> None:
        nonlocal a, hl, bc
        c = bc & 0xFF
        b = (bc >> 8) & 0xFF
        bcu = (bc >> 16) & 0xFF
        while True:
            add_a_a()
            c = rl8(c)
            nz = add_a_a()
            if not cf:
                continue
            if nz:
                bc = (bcu << 16) | (b << 8) | c
                return
            a = src[hl]
            hl += 1
            rla()
            if cf:
                bc = (bcu << 16) | (b << 8) | c
                return
            for _ in range(3):
                add_a_a()
                c = rl8(c)
                add_a_a()
                if cf:
                    bc = (bcu << 16) | (b << 8) | c
                    return
            while True:
                add_a_a()
                c = rl8(c)
                b = rl8(b)
                nz = add_a_a()
                if not cf:
                    continue
                if nz:
                    bc = (bcu << 16) | (b << 8) | c
                    return
                a = src[hl]
                hl += 1
                rla()
                if not cf:
                    continue
                bc = (bcu << 16) | (b << 8) | c
                return

    def elias_len() -> None:
        nonlocal a, hl, bc
        c = bc & 0xFF
        b = (bc >> 8) & 0xFF
        bcu = (bc >> 16) & 0xFF
        while True:
            add_a_a()
            c = rl8(c)
            nz = add_a_a()
            if not cf:
                continue
            if nz:
                bc = (bcu << 16) | (b << 8) | c
                return
            a = src[hl]
            hl += 1
            rla()
            if cf:
                bc = (bcu << 16) | (b << 8) | c
                return
            for _ in range(3):
                add_a_a()
                c = rl8(c)
                add_a_a()
                if cf:
                    bc = (bcu << 16) | (b << 8) | c
                    return
            while True:
                if not add_a_a():
                    a = src[hl]
                    hl += 1
                    rla()
                c = rl8(c)
                b = rl8(b)
                if cf:
                    ix = (0x010000 + ((bcu << 16) | (b << 8) | c)) & 0xFFFFFF
                    while True:
                        if not add_a_a():
                            a = src[hl]
                            hl += 1
                            rla()
                        if cf:
                            bc = ix
                            return
                        ix = (ix * 2) & 0xFFFFFF
                        if not add_a_a():
                            a = src[hl]
                            hl += 1
                            rla()
                        if cf:
                            ix = (ix + 1) & 0xFFFFFF
                if not add_a_a():
                    a = src[hl]
                    hl += 1
                    rla()
                if not cf:
                    continue
                bc = (bcu << 16) | (b << 8) | c
                return

    state = "lit_start"
    while True:
        if state == "lit_start":
            a = src[hl]
            hl += 1
            rla()
            state = "lit_skip"
        elif state == "lit":
            bc = (bc & 0xFFFF00) | ((bc + 1) & 0xFF)
            if not add_a_a():
                state = "lit_start"
                continue
            state = "lit_skip"
        elif state == "lit_skip":
            if not cf:
                elias_len()
            out[de : de + bc] = src[hl : hl + bc]
            hl += bc
            de += bc
            bc = 0
            add_a_a()
            state = "new_off" if cf else "last_off"
        elif state == "last_off":
            bc = (bc & 0xFFFF00) | ((bc + 1) & 0xFF)
            if not add_a_a():
                a = src[hl]
                hl += 1
                rla()
            if not cf:
                elias_len()
            state = "copy"
        elif state == "new_off":
            bc = (bc - 2) & 0xFFFFFF
            if not add_a_a():
                a = src[hl]
                hl += 1
                rla()
            if not cf:
                elias_off()
            c = (bc + 1) & 0xFF
            bc = (bc & 0xFFFF00) | c
            if c == 0:
                break
            b = src[hl]
            hl += 1
            c = rr8(c)
            b = rr8(b)
            iy = (iy & 0xFF0000) | (c << 8) | b
            off = 0x1000000 - iy
            if off > max_off:
                max_off = off
            bc = 1
            if not cf:
                elias_len()
            bc = (bc + 1) & 0xFFFFFF
            state = "copy"
        elif state == "copy":
            src_pos = (iy + de) & 0xFFFFFF
            for _ in range(bc):
                out[de] = out[src_pos]
                src_pos += 1
                de += 1
            bc = 0
            add_a_a()
            state = "new_off" if cf else "lit"

    assert hl == len(src), f"unconsumed ZX0 bytes: {hl} != {len(src)}"
    return bytes(out[:de]), max_off


class TestDdebugLoader(unittest.TestCase):
    def test_built_ddebug_appvar_roundtrip(self) -> None:
        ddebug_8xv = ROOT / "build" / "DDEBUG.8xv"
        ddebug_elf = ROOT / "bin" / "ddebug" / "DDLC.obj"
        if not (ddebug_8xv.exists() and ddebug_elf.exists()):
            self.skipTest("build/DDEBUG.8xv or bin/ddebug/DDLC.obj not built")

        name, flag, payload = _extract_8xv_payload(ddebug_8xv)
        self.assertEqual(name, "DDEBUG")
        self.assertEqual(flag, 0x80)
        self.assertLessEqual(len(payload), build_ddebug.TI_APPVAR_MAX)

        magic, raw_size, comp_crc = struct.unpack_from("<4sII", payload, 0)
        zx0_stream = payload[12:]
        self.assertEqual(magic, b"DDBG")
        self.assertGreaterEqual(raw_size, 64)
        self.assertLessEqual(raw_size, build_ddebug.MAX_RAW_ENGINE)
        self.assertEqual(zlib.crc32(zx0_stream) & 0xFFFFFFFF, comp_crc)

        decoded, max_off = _zx0_decompress(zx0_stream, raw_size)
        self.assertEqual(len(decoded), raw_size)
        self.assertLessEqual(max_off, 32768)

        # Verify decompressed bytes match bin/ddebug/DDLC.obj engine binary byte-for-byte
        convbin = find_cedev_tool("convbin", env_var="CONVBIN_REAL")
        with tempfile.TemporaryDirectory() as td:
            raw_bin = Path(td) / "engine.bin"
            subprocess.run(
                [str(convbin), "-j", "elf", "-k", "bin", "-i", str(ddebug_elf), "-o", str(raw_bin)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            data = raw_bin.read_bytes()
            self.assertTrue(data.startswith(b"\xef\x7b"))
            expected_raw = data[2:]
            self.assertEqual(len(expected_raw), raw_size)
            self.assertEqual(decoded, expected_raw)

        report_path = ROOT / "build" / "ddebug-build.json"
        if report_path.exists():
            info = json.loads(report_path.read_text())
            self.assertEqual(info["raw_bytes"], raw_size)
            self.assertEqual(info["compressed_crc32"], f"{comp_crc:08x}")
            self.assertEqual(info["raw_sha256"], hashlib.sha256(decoded).hexdigest())

    def test_loader_contains_ddebug_guard_and_valid_relocations(self) -> None:
        loader_8xp = ROOT / "bin" / "DDLC.8xp"
        if not loader_8xp.exists():
            self.skipTest("bin/DDLC.8xp not built")

        raw = loader_8xp.read_bytes()
        body = raw[55 + 19 : -2]
        self.assertEqual(body[:2], b"\xef\x7b")
        mem = body[2:]

        # Entry jumps into the integrity guard before stock convbin loader
        self.assertEqual(mem[1], 0xC3)
        entry_target = struct.unpack_from("<I", mem, 2)[0] & 0xFFFFFF
        guard_off = entry_target - 0xD1A881
        self.assertGreater(guard_off, 0)
        self.assertLess(guard_off, len(mem))

        # Both DDEBUG probe and fallback DDLC0/DDLC1 records present
        self.assertIn(b"\x15DDEBUG\x00\x00", mem[guard_off:])
        self.assertIn(b"\x15DDLC0\x00\x00\x00", mem[guard_off:])
        self.assertIn(b"\x15DDLC1\x00\x00\x00", mem[guard_off:])

        # Relocated DDEBUG decompressor stub targets RELOC_BASE = 0xD09466
        self.assertIn(bytes.fromhex("ed b0 c3 66 94 d0"), mem[guard_off:])


if __name__ == "__main__":
    unittest.main()
