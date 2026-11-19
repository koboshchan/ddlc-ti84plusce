"""Host unit tests for tools/pak_builder.py and src/pak.c (spanning + contiguous)."""

from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import pak_builder


def _build_lut(entries):
    out = bytearray(struct.pack("<H", len(entries)))
    for vol, off, length in entries:
        out += struct.pack("<BHH", vol, off, length)
    return bytes(out)


class PakTests(unittest.TestCase):
    def test_packer_contiguous_spanning_and_dedup(self):
        # Small volume cap (64 B) forces multi-volume spanning (including 3-volume span!)
        packer = pak_builder.AssetPacker(vol_max=64)
        packer.add_named("BIGC", bytes(range(40)), contiguous=True)
        packer.add_named("SMALLC", b"hello_contig", contiguous=True)
        # 150-byte spannable asset must span across 3+ volumes
        long_span = bytes((i * 37 + 11) & 0xFF for i in range(150))
        packer.add_named("SPAN3", long_span, contiguous=False)
        packer.add_named("TINY1", b"abc", contiguous=False)
        packer.add_named("TINY2", b"defghijklmno", contiguous=False)

        dup_blob = b"DUPLICATE_SPRITE_DATA!"
        packer.add_group("DSPR", [dup_blob, b"UNIQUE_SPR", dup_blob], contiguous=True)
        packer.add_group("DCGPAL", [b"P" * 30, b"Q" * 30, b"P" * 30], contiguous=False)

        res = packer.build({"DSPR": "DSPRLUT", "DCGPAL": "DCGPLUT"}, _build_lut)

        self.assertGreaterEqual(len(res.volumes), 4)
        for v in res.volumes[:-1]:
            self.assertEqual(len(v), 64)
        self.assertGreaterEqual(res.spanning_count, 1)

        # Deduplicated group entries share identical (vol, off, len)
        self.assertEqual(res.group_locations["DSPR"][0], res.group_locations["DSPR"][2])
        self.assertEqual(res.group_locations["DCGPAL"][0], res.group_locations["DCGPAL"][2])

        # Verify SPAN3 actually spans and roundtrips bit-exact
        vol, off, length, flags = res.named_locations["SPAN3"]
        self.assertEqual(flags & pak_builder.PAK_FLAG_SPANNING, pak_builder.PAK_FLAG_SPANNING)
        self.assertEqual(pak_builder.read_span(res.volumes, vol, off, length), long_span)

    def test_actual_c_pak_module_with_ubsan(self):
        packer = pak_builder.AssetPacker(vol_max=64)
        contig_blob = bytes((i * 7) & 0xFF for i in range(44))
        span_blob = bytes((i * 13 + 3) & 0xFF for i in range(145))
        small_blob = b"DENTRY_TEST_BYTES"
        packer.add_named("CONTIG", contig_blob, contiguous=True)
        packer.add_named("SPAN145", span_blob, contiguous=False)
        packer.add_named("SMALL", small_blob, contiguous=False)
        res = packer.build({}, _build_lut)

        def c_array(name, data):
            vals = ",".join(str(b) for b in data)
            return f"static const uint8_t {name}[{len(data)}] = {{{vals}}};\n"

        vol_decls = "".join(c_array(f"vol_{i}", v) for i, v in enumerate(res.volumes))
        vol_ptrs = ",".join(f"vol_{i}" for i in range(len(res.volumes)))
        vol_lens = ",".join(str(len(v)) for v in res.volumes)

        harness = f"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

{c_array("dpkidx_data", res.index_bytes)}
{vol_decls}
static const uint8_t *const vol_ptrs[{len(res.volumes)}] = {{{vol_ptrs}}};
static const uint16_t vol_lens[{len(res.volumes)}] = {{{vol_lens}}};
static const uint8_t standalone_data[5] = {{10, 20, 30, 40, 50}};

#define MAX_H 16
static struct {{ bool used; const uint8_t *ptr; uint16_t len; }} handles[MAX_H];

uint8_t ti_Open(const char *name, const char *mode) {{
    (void)mode;
    const uint8_t *ptr = NULL;
    uint16_t len = 0;
    if (strcmp(name, "DPKIDX") == 0) {{
        ptr = dpkidx_data; len = sizeof(dpkidx_data);
    }} else if (strncmp(name, "DPK", 3) == 0) {{
        int idx = atoi(name + 3);
        if (idx >= 0 && idx < {len(res.volumes)}) {{
            ptr = vol_ptrs[idx]; len = vol_lens[idx];
        }}
    }} else if (strcmp(name, "SOLO") == 0) {{
        ptr = standalone_data; len = sizeof(standalone_data);
    }}
    if (!ptr) return 0;
    for (uint8_t h = 1; h < MAX_H; h++) {{
        if (!handles[h].used) {{
            handles[h].used = true;
            handles[h].ptr = ptr;
            handles[h].len = len;
            return h;
        }}
    }}
    return 0;
}}
void ti_Close(uint8_t h) {{ assert(h > 0 && h < MAX_H && handles[h].used); handles[h].used = false; }}
uint16_t ti_GetSize(uint8_t h) {{ assert(handles[h].used); return handles[h].len; }}
void *ti_GetDataPtr(uint8_t h) {{ assert(handles[h].used); return (void *)handles[h].ptr; }}
size_t ti_Read(void *dst, size_t sz, size_t cnt, uint8_t h) {{
    assert(handles[h].used);
    size_t n = sz * cnt;
    if (n > handles[h].len) n = handles[h].len;
    memcpy(dst, handles[h].ptr, n);
    return n / sz;
}}

#include "pak.h"

int main(void) {{
    assert(!pak_active());
    assert(pak_init());
    assert(pak_active());

    /* Contiguous view */
    pak_view_t v;
    assert(pak_view_open("CONTIG", &v));
    assert(v.size == 44);
    for (unsigned i = 0; i < 44; i++) assert(v.ptr[i] == ((i * 7) & 0xFF));
    pak_view_close(&v);

    /* Spanning asset refuses zero-copy view, succeeds via read_whole & sub-range read_asset */
    assert(!pak_view_open("SPAN145", &v));
    uint16_t sz = 0;
    uint8_t *got = pak_read_whole("SPAN145", &sz);
    assert(got && sz == 145);
    for (unsigned i = 0; i < 145; i++) assert(got[i] == ((i * 13 + 3) & 0xFF));
    free(got);

    uint8_t sub[50];
    assert(pak_read_asset("SPAN145", 55, sub, 50));
    for (unsigned i = 0; i < 50; i++) assert(sub[i] == (((55 + i) * 13 + 3) & 0xFF));
    assert(!pak_read_asset("SPAN145", 140, sub, 10));

    /* Small asset + standalone fallback */
    uint8_t *sm = pak_read_whole("SMALL", &sz);
    assert(sm && sz == 17 && memcmp(sm, "DENTRY_TEST_BYTES", 17) == 0);
    free(sm);

    assert(pak_view_open("SOLO", &v));
    assert(v.size == 5 && v.ptr[4] == 50);
    pak_view_close(&v);

    uint8_t vh = pak_open_vol("DSCN", 0);
    assert(vh != 0 && ti_GetSize(vh) == vol_lens[0]);
    ti_Close(vh);

    /* Ensure zero leaked handles */
    for (uint8_t h = 1; h < MAX_H; h++) assert(!handles[h].used);
    pak_shutdown();
    assert(!pak_active());
    return 0;
}}
"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "fileioc.h").write_text(
                "uint8_t ti_Open(const char*,const char*);\n"
                "void ti_Close(uint8_t);\n"
                "uint16_t ti_GetSize(uint8_t);\n"
                "void *ti_GetDataPtr(uint8_t);\n"
                "size_t ti_Read(void*,size_t,size_t,uint8_t);\n"
            )
            (p / "test.c").write_text(harness)
            subprocess.run(
                [
                    "cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=undefined", "-DDDLC_PAK_ENABLE",
                    "-I", str(p), "-I", str(ROOT / "src"),
                    str(p / "test.c"), str(ROOT / "src/pak.c"),
                    "-o", str(p / "test"),
                ],
                check=True,
            )
            subprocess.run([str(p / "test")], check=True)


if __name__ == "__main__":
    unittest.main()
