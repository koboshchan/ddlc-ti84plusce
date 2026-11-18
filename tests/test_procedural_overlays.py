"""Tests for procedural overlays: RectCluster, Blood, AnimatedMask, and LiveTile."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import compile_script
import vnasm

ROOT = Path(__file__).resolve().parent.parent


class OverlayResolver:
    table: dict = {}

    def condswitch_variants(self, imgname):
        return None

    def sprite_layers(self, imgname):
        return (1, vnasm.NO_OVERLAY)

    def scene_id(self, imgname):
        return {
            ("black",): 1,
            ("bg", "glitch"): 42,
            ("monika_bg",): 77,
            ("monika_bg_highlight",): 78,
        }.get(imgname)


def make_node(cls_name: str, **attrs):
    obj = type(cls_name, (), {})()
    for k, v in attrs.items():
        setattr(obj, k, v)
    return obj


def show_tag(tag, alias=None):
    parts = tag if isinstance(tag, tuple) else (tag,)
    return make_node("Show", imspec=(parts, None, alias, [], None, None, []), atl=None, linenumber=1)


def hide_tag(tag):
    parts = tag if isinstance(tag, tuple) else (tag,)
    return make_node("Hide", imspec=(parts, None, None, [], None, None, []), linenumber=2)


class ProceduralOverlaysTests(unittest.TestCase):
    def test_rectcluster_and_blood_do_not_replace_scene_and_refcount_hides(self) -> None:
        c = compile_script.Compiler(resolver=OverlayResolver())
        c.emit_node(show_tag("n_rects_ghost1"), "script-poemresponses2.rpyc")
        c.emit_node(show_tag("n_rects_ghost2"), "script-poemresponses2.rpyc")
        c.emit_node(hide_tag("n_rects_ghost1"), "script-poemresponses2.rpyc")
        c.emit_node(hide_tag("n_rects_ghost2"), "script-poemresponses2.rpyc")
        c.emit_node(show_tag("blood_eye"), "script-ch22.rpyc")
        c.emit_node(hide_tag("blood_eye"), "script-ch22.rpyc")

        self.assertEqual(
            bytes(c.asm.code),
            bytes([
                vnasm.OP_OVERLAY_FX, vnasm.VN_OVL_RECTS_N, 1,
                vnasm.OP_OVERLAY_FX, vnasm.VN_OVL_RECTS_N, 0,
                vnasm.OP_OVERLAY_FX, vnasm.VN_OVL_BLOOD_EYE, 1,
                vnasm.OP_OVERLAY_FX, vnasm.VN_OVL_BLOOD_EYE, 0,
            ]),
        )

    def test_room_mask_survives_monika_bg_and_bg_glitch_enables_live_tile(self) -> None:
        c = compile_script.Compiler(resolver=OverlayResolver())
        c.emit_node(show_tag("room_mask", alias="rm"), "script-ch30.rpyc")
        c.emit_node(show_tag("room_mask2", alias="rm2"), "script-ch30.rpyc")
        c.emit_node(show_tag("monika_bg"), "script-ch30.rpyc")
        c.emit_node(show_tag("monika_bg_highlight"), "script-ch30.rpyc")
        c.emit_node(show_tag(("bg", "glitch")), "script-exclusives2-yuri.rpyc")

        code = bytes(c.asm.code)
        # Expect VN_OVL_ROOM_MASK re-applied after monika_bg/monika_bg_highlight,
        # then bg glitch emits OP_SCENE(42) + OP_OVERLAY_FX(VN_OVL_LIVE_TILE, 1).
        self.assertIn(bytes([vnasm.OP_SCENE, 77, vnasm.TRANS_CUT,
                             vnasm.OP_OVERLAY_FX, vnasm.VN_OVL_ROOM_MASK, 1]), code)
        self.assertIn(bytes([vnasm.OP_SCENE, 78, vnasm.TRANS_CUT,
                             vnasm.OP_OVERLAY_FX, vnasm.VN_OVL_ROOM_MASK, 1]), code)
        self.assertTrue(
            code.endswith(bytes([vnasm.OP_SCENE, 42, vnasm.TRANS_CUT,
                                 vnasm.OP_OVERLAY_FX, vnasm.VN_OVL_LIVE_TILE, 1]))
        )

    def test_vm_and_overlay_bounds_safety(self) -> None:
        render_src = (ROOT / "src/render.c").read_text()
        overlay_block = render_src[render_src.index("static void render_live_tile_bg(void)\n{"):
                                   render_src.index("void render_timed_fx_tick(")]
        harness = r"""
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include "vn.h"

#define SCREEN_W 320
#define SCENE_H 180
#define COL_BLACK 1
#define COL_BOX_EDGE 4
static uint8_t guard_vbuffer[320 * 240];
static uint8_t *gfx_vbuffer = guard_vbuffer;
static uint8_t active_color;
static uint32_t rng = 99;
static uint32_t tear_rand_next(void) { rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5; return rng; }
static void gfx_SetColor(uint8_t c) { active_color = c; }
static void gfx_FillRectangle_NoClip(int x, int y, int w, int h) {
    assert(x >= 0 && y >= 0 && w > 0 && h > 0);
    assert(x + w <= SCREEN_W && y + h <= SCENE_H);
    for (int yy = y; yy < y + h; yy++) {
        memset(gfx_vbuffer + yy * SCREEN_W + x, active_color, (size_t)w);
    }
}
static void fast_rect_blit(uint8_t *dst, size_t dp, const uint8_t *src, size_t sp, size_t w, size_t h) {
    assert(dst >= gfx_vbuffer && dst + (h - 1) * dp + w <= gfx_vbuffer + SCREEN_W * SCENE_H);
    assert(src >= gfx_vbuffer && src + (h - 1) * sp + w <= gfx_vbuffer + SCREEN_W * SCENE_H);
    for (size_t r = 0; r < h; r++) memcpy(dst + r * dp, src + r * sp, w);
}
static uint8_t blood_red_index(void) { return 4; }
""" + overlay_block + r"""
static const char *host_str(void *ctx, uint16_t idx) { (void)ctx; (void)idx; return ""; }
static void host_upd(void *ctx, const vn_scene_t *s, uint8_t t) { (void)ctx; (void)s; (void)t; }
static void host_say(void *ctx, const vn_scene_t *s) { (void)ctx; (void)s; }
static uint8_t host_menu(void *ctx, const vn_scene_t *s, const char *const *o, uint8_t c) {
    (void)ctx; (void)s; (void)o; (void)c; return 0;
}
static const vn_host_t host = {
    .string = host_str, .update = host_upd, .say = host_say, .menu = host_menu
};

int main(void) {
    memset(guard_vbuffer, 0xAA, sizeof(guard_vbuffer));
    vn_scene_t s = {0};
    s.overlay_fx_mask = VN_OVL_RECTS_N | VN_OVL_RECTS_M | VN_OVL_BLOOD | VN_OVL_BLOOD_EYE | VN_OVL_LIVE_TILE;
    for (int f = 0; f < 60; f++) {
        render_live_tile_bg();
        render_overlay_fx(&s);
    }
    /* Rows 180..239 must stay untouched by scene overlays */
    for (size_t i = 320 * 180; i < sizeof(guard_vbuffer); i++) {
        assert(guard_vbuffer[i] == 0xAA);
    }

    uint8_t code[] = {
        OP_OVERLAY_FX, VN_OVL_BLOOD, 1,
        OP_OVERLAY_FX, VN_OVL_RECTS_N, 1,
        OP_OVERLAY_FX, VN_OVL_BLOOD, 0,
        OP_SCENE, 3, TRANS_CUT,
        OP_END
    };
    vn_vm_t vm;
    vn_init(&vm, code, sizeof(code), &host, 1);
    assert(vn_run(&vm) == VN_FINISHED);
    assert(vm.scene.overlay_fx_mask == 0);
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as td:
            src_c = Path(td) / "harness.c"
            exe = Path(td) / "harness"
            src_c.write_text(harness)
            subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                 "-I", str(ROOT / "src"), str(src_c), str(ROOT / "src/vn.c"), "-o", str(exe)],
                check=True,
            )
            subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    unittest.main()
