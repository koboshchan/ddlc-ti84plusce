"""Tests for ShowLayer camera/layer effects (Phase 4 #13)."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import compile_script
import rpyc_ast
import vnasm

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "assets" / "raw"


class ShowLayerResolver:
    table: dict = {}

    def condswitch_variants(self, imgname):
        return None

    def sprite_layers(self, imgname):
        return (1, vnasm.NO_OVERLAY)

    def scene_id(self, imgname):
        return 1


def extract_layer_render_slice() -> str:
    src = (ROOT / "src" / "render.c").read_text()
    start = src.index("static uint8_t  layer_s_fx;")
    end = src.index("\nvoid render_splash_warning(")
    return src[start:end]


class TestShowLayerEffects(unittest.TestCase):
    def test_all_real_showlayer_nodes_compile_without_skip(self):
        if not RAW_DIR.exists():
            self.skipTest("assets/raw not extracted")
        c = compile_script.Compiler(ShowLayerResolver())
        # Give Compiler a dummy menu_clicked variable slot and emit all ShowLayer blocks
        total = 0
        for p in sorted(RAW_DIR.glob("*.rpyc")):
            _, top = rpyc_ast.load_rpyc(p)
            nodes = list(rpyc_ast.flatten(top))
            for idx, st in enumerate(nodes):
                if rpyc_ast.kind(st) == "ShowLayer":
                    total += 1
                    c._block_nodes = nodes
                    c._block_idx = idx
                    c._emit_ShowLayer(st, p.name)
        self.assertEqual(total, 31)
        layer_skips = [s for s in c.skipped if "ShowLayer" in s.reason]
        self.assertEqual(layer_skips, [], f"Skipped ShowLayer nodes: {layer_skips}")

    def test_specific_showlayer_opcodes(self):
        c = compile_script.Compiler(ShowLayerResolver())
        # heartbeat
        n_hb = type("ShowLayer", (), {"layer": "master", "at_list": ["heartbeat"], "atl": None})()
        c._emit_ShowLayer(n_hb, "t.rpy")
        # dizzy
        n_dz = type("ShowLayer", (), {"layer": "master", "at_list": ["dizzy(0.5, 1.0)"], "atl": None})()
        c._emit_ShowLayer(n_dz, "t.rpy")
        # clear
        n_clr = type("ShowLayer", (), {"layer": "master", "at_list": [], "atl": None})()
        c._emit_ShowLayer(n_clr, "t.rpy")

        code = c.asm.code
        self.assertEqual(
            list(code),
            [
                vnasm.OP_LAYER_FX, vnasm.VN_LAYER_HEARTBEAT, 0, 0,
                vnasm.OP_LAYER_FX, vnasm.VN_LAYER_DIZZY, 0, 0,
                vnasm.OP_LAYER_FX, vnasm.VN_LAYER_NONE, 0, 0,
            ],
        )

    def test_c_crop_stretch_shift_and_vm_runtime(self):
        render_slice = extract_layer_render_slice()
        harness = f"""
#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <assert.h>
#include "vn.h"

#define SCREEN_W 320
#define SCENE_H 180
#define COL_BLACK 1
static uint8_t gfx_vbuffer[SCREEN_W * 240];
static unsigned test_clock_ms = 0;
static unsigned ticks_to_ms(unsigned c) {{ return c; }}
static unsigned clock(void) {{ return test_clock_ms; }}

void fast_stretch_row(uint8_t *dst, const uint8_t *src, size_t dst_w, size_t src_w) {{
    size_t acc = 0;
    size_t sx = 0;
    for (size_t x = 0; x < dst_w; x++) {{
        dst[x] = src[sx];
        acc += src_w;
        while (acc >= dst_w) {{
            acc -= dst_w;
            sx++;
        }}
    }}
}}

void fast_row_shift(uint8_t *row, uint8_t *scratch, size_t off_wrapped) {{
    if (off_wrapped == 0) return;
    memcpy(scratch, row, SCREEN_W);
    memcpy(row, scratch + SCREEN_W - off_wrapped, off_wrapped);
    memcpy(row + off_wrapped, scratch, SCREEN_W - off_wrapped);
}}

{render_slice}

static void fill_pattern(uint8_t *buf) {{
    for (int y = 0; y < SCENE_H; y++) {{
        for (int x = 0; x < SCREEN_W; x++) {{
            buf[y * SCREEN_W + x] = (uint8_t)((x * 3 + y * 5 + 7) & 0xFF);
        }}
    }}
    memset(buf + SCENE_H * SCREEN_W, 0xAA, 60 * SCREEN_W);
}}

static void ref_crop_stretch(const uint8_t *src_fb, uint8_t *dst_fb, int x0, int y0, int cw, int ch) {{
    for (int y = 0; y < SCENE_H; y++) {{
        int sy = y0 + (y * ch) / SCENE_H;
        const uint8_t *srow = src_fb + sy * SCREEN_W + x0;
        uint8_t *drow = dst_fb + y * SCREEN_W;
        if (cw == SCREEN_W) {{
            memcpy(drow, srow, SCREEN_W);
        }} else {{
            fast_stretch_row(drow, srow, SCREEN_W, (size_t)cw);
        }}
    }}
}}

static uint8_t dummy_code[64];
static uint8_t update_calls = 0;
static void stub_update(void *ctx, const vn_scene_t *s, uint8_t trans) {{
    (void)ctx; (void)s; (void)trans;
    update_calls++;
}}
static const char *stub_str(void *ctx, uint16_t idx) {{ (void)ctx; (void)idx; return ""; }}
static void stub_say(void *ctx, const vn_scene_t *s) {{ (void)ctx; (void)s; }}
static uint8_t stub_menu(void *ctx, const vn_scene_t *s, const char *const *c, uint8_t n) {{ (void)ctx; (void)s; (void)c; (void)n; return 0; }}
static void stub_pause(void *ctx, uint32_t ms) {{ (void)ctx; (void)ms; }}
static bool stub_quit(void *ctx) {{ (void)ctx; return false; }}

int main(void) {{
    static uint8_t ref_in[SCREEN_W * 240];
    static uint8_t ref_out[SCREEN_W * SCENE_H];
    fill_pattern(ref_in);

    /* Test crop-and-stretch pixel-for-pixel against out-of-place reference */
    const int cases[][4] = {{
        {{0, 0, 320, 180}},
        {{8, 4, 304, 171}},
        {{11, 6, 298, 168}},
        {{27, 15, 266, 150}},
        {{70, 8, 180, 101}},
        {{0, 0, 240, 135}},
        {{40, 45, 240, 135}},
        {{20, 0, 280, 180}},
        {{80, 13, 160, 90}},
    }};
    for (size_t ci = 0; ci < sizeof(cases) / sizeof(cases[0]); ci++) {{
        int x0 = cases[ci][0], y0 = cases[ci][1], cw = cases[ci][2], ch = cases[ci][3];
        memcpy(gfx_vbuffer, ref_in, sizeof(ref_in));
        layer_crop_stretch(x0, y0, cw, ch);
        ref_crop_stretch(ref_in, ref_out, x0, y0, cw, ch);
        assert(memcmp(gfx_vbuffer, ref_out, SCREEN_W * SCENE_H) == 0);
        for (int i = SCREEN_W * SCENE_H; i < SCREEN_W * 240; i++) {{
            assert(gfx_vbuffer[i] == 0xAA);
        }}
    }}

    /* Test viewport shift bounds & black borders */
    fill_pattern(gfx_vbuffer);
    layer_viewport_shift(6, 4);
    for (int y = 0; y < 4; y++)
        for (int x = 0; x < SCREEN_W; x++)
            assert(gfx_vbuffer[y * SCREEN_W + x] == COL_BLACK);
    for (int x = 0; x < 6; x++)
        assert(gfx_vbuffer[10 * SCREEN_W + x] == COL_BLACK);
    assert(gfx_vbuffer[10 * SCREEN_W + 20] == ref_in[6 * SCREEN_W + 14]);

    fill_pattern(gfx_vbuffer);
    layer_viewport_shift(-8, -5);
    for (int y = SCENE_H - 5; y < SCENE_H; y++)
        for (int x = 0; x < SCREEN_W; x++)
            assert(gfx_vbuffer[y * SCREEN_W + x] == COL_BLACK);
    for (int x = SCREEN_W - 8; x < SCREEN_W; x++)
        assert(gfx_vbuffer[10 * SCREEN_W + x] == COL_BLACK);
    assert(gfx_vbuffer[10 * SCREEN_W + 20] == ref_in[15 * SCREEN_W + 28]);

    /* Test VM OP_LAYER_FX, MENU_ZOOM variable read, and OP_SCENE reset */
    size_t pc = 0;
    dummy_code[pc++] = OP_SET;
    dummy_code[pc++] = 5;
    dummy_code[pc++] = 2;
    dummy_code[pc++] = 0;
    dummy_code[pc++] = OP_LAYER_FX;
    dummy_code[pc++] = VN_LAYER_MENU_ZOOM;
    dummy_code[pc++] = 5;
    dummy_code[pc++] = 0;
    dummy_code[pc++] = OP_SAY;
    dummy_code[pc++] = 0xFF;
    dummy_code[pc++] = 0;
    dummy_code[pc++] = 0;
    dummy_code[pc++] = OP_SCENE;
    dummy_code[pc++] = 3;
    dummy_code[pc++] = 0;
    dummy_code[pc++] = OP_END;

    vn_host_t host = {{
        .string = stub_str, .update = stub_update, .say = stub_say,
        .menu = stub_menu, .pause = stub_pause, .quit = stub_quit,
    }};
    vn_vm_t vm;
    vn_init(&vm, dummy_code, pc, &host, 1);
    assert(vn_step(&vm) && vm.status == VN_RUNNING); /* OP_SET */
    assert(vn_step(&vm) && vm.status == VN_RUNNING); /* OP_LAYER_FX */
    assert(vm.scene.layer_fx == VN_LAYER_MENU_ZOOM);
    assert(vm.scene.layer_fx_param == 2);
    assert(update_calls == 1);
    assert(vn_step(&vm) && vm.status == VN_RUNNING); /* OP_SAY */
    assert(vn_step(&vm) && vm.status == VN_RUNNING); /* OP_SCENE clears layer_fx */
    assert(vm.scene.layer_fx == VN_LAYER_NONE);
    assert(vm.scene.layer_fx_param == 0);
    assert(!vn_step(&vm) && vm.status == VN_FINISHED);

    /* Heartbeat step state settles during idle interval */
    vn_scene_t sc;
    memset(&sc, 0, sizeof(sc));
    sc.layer_fx = VN_LAYER_HEARTBEAT;
    sc.layer_fx_seq = 1;
    test_clock_ms = 0;
    fill_pattern(gfx_vbuffer);
    apply_layer_fx(&sc, test_clock_ms);
    assert(!layer_fx_active_pending());
    assert(!layer_fx_scene_pending(&sc, test_clock_ms));
    test_clock_ms = 200; /* inside lub beat */
    assert(layer_fx_active_pending());
    assert(layer_fx_scene_pending(&sc, test_clock_ms));
    fill_pattern(gfx_vbuffer);
    apply_layer_fx(&sc, test_clock_ms);
    assert(!layer_fx_active_pending());

    /* Rewind shear + crop bounds safety */
    sc.layer_fx = VN_LAYER_REWIND;
    sc.layer_fx_seq = 2;
    for (unsigned ms = 0; ms <= 800; ms += 90) {{
        fill_pattern(gfx_vbuffer);
        apply_layer_fx(&sc, ms);
        for (int i = SCREEN_W * SCENE_H; i < SCREEN_W * 240; i++) {{
            assert(gfx_vbuffer[i] == 0xAA);
        }}
    }}
    return 0;
}}
"""
        with tempfile.TemporaryDirectory() as tmp:
            c_path = Path(tmp) / "test_layer.c"
            bin_path = Path(tmp) / "test_layer"
            c_path.write_text(harness)
            subprocess.run(
                [
                    "cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined",
                    "-I", str(ROOT / "src"),
                    str(c_path), str(ROOT / "src" / "vn.c"),
                    "-o", str(bin_path),
                ],
                check=True,
            )
            subprocess.run([str(bin_path)], check=True)


if __name__ == "__main__":
    unittest.main()
