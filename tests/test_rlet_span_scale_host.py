#!/usr/bin/env python3
"""Host exhaustive verification of scale_rlet_row_spans() in src/assets.c
against a dense RLET decode + per-pixel Bresenham transparency-skip reference."""

from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def extract_scale_rlet_row_spans() -> str:
    src = (ROOT / "src/assets.c").read_text()
    start = src.index("typedef struct {\n    unsigned src_w;")
    end = src.index("static __attribute__((noinline)) int scale_permille_val(", start)
    return src[start:end]


HARNESS_HEAD = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
'''

HARNESS_TAIL = r'''
static uint32_t rng_state = 0x12345678u;

static uint32_t rng_next(void)
{
    uint32_t x = rng_state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    rng_state = x;
    return x;
}

static size_t encode_rlet_row(const uint8_t *dense, unsigned w, uint8_t *out)
{
    size_t pos = 0;
    unsigned x = 0;
    while (x < w) {
        unsigned t = 0;
        while (x + t < w && dense[x + t] == 0u) {
            t++;
        }
        out[pos++] = (uint8_t)t;
        x += t;
        if (x >= w) {
            break;
        }
        unsigned o = 0;
        while (x + o < w && dense[x + o] != 0u) {
            o++;
        }
        out[pos++] = (uint8_t)o;
        memcpy(out + pos, dense + x, o);
        pos += o;
        x += o;
    }
    return pos;
}

static void reference_scale_row(uint8_t *dest_row, const uint8_t *dense,
                                unsigned src_w, unsigned dst_w,
                                unsigned start_dx, unsigned end_dx)
{
    unsigned init_num = start_dx * src_w + (dst_w >> 1);
    unsigned sx = init_num / dst_w;
    unsigned acc = init_num % dst_w;
    unsigned vis_w = end_dx - start_dx;

    for (unsigned i = 0; i < vis_w; i++) {
        uint8_t px = dense[sx];
        if (px != 0u) {
            dest_row[i] = px;
        }
        acc += src_w;
        while (acc >= dst_w) {
            acc -= dst_w;
            sx++;
        }
    }
}

int main(void)
{
    uint8_t dense[256];
    uint8_t rlet[520];
    uint8_t ref_dst[320];
    uint8_t got_dst[320];

    static const unsigned widths[] = { 2, 3, 7, 16, 38, 44, 63, 92, 105, 140, 180, 220 };
    static const unsigned scales[] = { 1000, 1005, 1012, 1025, 1037, 1050 };

    unsigned cases = 0;
    for (size_t wi = 0; wi < sizeof(widths) / sizeof(widths[0]); wi++) {
        unsigned src_w = widths[wi];
        for (size_t si = 0; si < sizeof(scales) / sizeof(scales[0]); si++) {
            unsigned dst_w = (src_w * scales[si] + 500u) / 1000u;
            for (int pat = 0; pat < 8; pat++) {
                memset(dense, 0, sizeof(dense));
                if (pat == 0) {
                    /* All transparent */
                } else if (pat == 1) {
                    /* All opaque */
                    for (unsigned x = 0; x < src_w; x++) {
                        dense[x] = (uint8_t)(1u + (x % 200u));
                    }
                } else {
                    /* Random multi-span pattern */
                    unsigned x = 0;
                    bool opq = (pat & 1) != 0;
                    while (x < src_w) {
                        unsigned run = 1u + (rng_next() % (src_w / 3u + 1u));
                        if (x + run > src_w) {
                            run = src_w - x;
                        }
                        if (opq) {
                            for (unsigned k = 0; k < run; k++) {
                                dense[x + k] = (uint8_t)(1u + ((x + k + (unsigned)pat * 17u) % 250u));
                            }
                        }
                        x += run;
                        opq = !opq;
                    }
                }

                size_t rlet_len = encode_rlet_row(dense, src_w, rlet);
                rlet[rlet_len] = 0xEE; /* sentinel after row */

                /* Test full row + 5 clipped windows */
                unsigned clips[6][2] = {
                    { 0u, dst_w },
                    { 0u, dst_w / 2u },
                    { dst_w / 2u, dst_w },
                    { dst_w / 4u, (dst_w * 3u) / 4u },
                    { 1u, dst_w > 2u ? dst_w - 1u : dst_w },
                    { dst_w / 3u, dst_w / 3u + 1u },
                };
                for (int ci = 0; ci < 6; ci++) {
                    unsigned sdx = clips[ci][0];
                    unsigned edx = clips[ci][1];
                    if (sdx >= edx || edx > dst_w) {
                        continue;
                    }
                    memset(ref_dst, 0x5A, sizeof(ref_dst));
                    memset(got_dst, 0x5A, sizeof(got_dst));

                    reference_scale_row(ref_dst, dense, src_w, dst_w, sdx, edx);
                    unsigned gap = dst_w - src_w;
                    rlet_sc.src_w = src_w;
                    rlet_sc.dst_w = dst_w;
                    rlet_sc.gap = gap;
                    rlet_sc.half = dst_w >> 1;
                    rlet_sc.step_q = gap ? (src_w / gap) : 0u;
                    rlet_sc.step_r = gap ? (src_w % gap) : 0u;
                    rlet_sc.start_dx = sdx;
                    rlet_sc.end_dx = edx;
                    const uint8_t *end_ptr = scale_rlet_row_spans(got_dst, rlet);

                    if (end_ptr != rlet + rlet_len) {
                        fprintf(stderr,
                                "FAIL ptr mismatch src_w=%u dst_w=%u pat=%d got=%td want=%zu\n",
                                src_w, dst_w, pat, end_ptr - rlet, rlet_len);
                        return 1;
                    }
                    if (memcmp(ref_dst, got_dst, sizeof(ref_dst)) != 0) {
                        for (unsigned i = 0; i < sizeof(ref_dst); i++) {
                            if (ref_dst[i] != got_dst[i]) {
                                fprintf(stderr,
                                        "FAIL pixel mismatch src_w=%u dst_w=%u pat=%d clip=[%u,%u) i=%u ref=%u got=%u\n",
                                        src_w, dst_w, pat, sdx, edx, i, ref_dst[i], got_dst[i]);
                                return 1;
                            }
                        }
                    }
                    cases++;
                }
            }
        }
    }

    printf("RLET opaque-span ldir scaling verification PASS (%u cases)\n", cases);
    return 0;
}
'''


class RletSpanScaleHostTest(unittest.TestCase):
    def test_span_scaler_matches_reference_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            src = tmp / "test_rlet_span.c"
            exe = tmp / "test_rlet_span"
            src.write_text(HARNESS_HEAD + extract_scale_rlet_row_spans() + HARNESS_TAIL)
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-O2",
                    "-fsanitize=undefined,address",
                    str(src),
                    "-o",
                    str(exe),
                ],
                check=True,
            )
            subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    unittest.main()
