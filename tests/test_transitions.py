"""Tests for palette-safe With transitions (Phase 4 #15)."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import compile_script
import vnasm

ROOT = Path(__file__).resolve().parent.parent


class TransResolver:
    table = {}

    def condswitch_variants(self, imgname):
        return None

    def sprite_layers(self, imgname):
        return (5, vnasm.NO_OVERLAY)

    def scene_id(self, imgname):
        return {("bg", "club_day"): 10, ("black",): 1}.get(imgname)

    def cg_composite_scene_id(self, layers):
        return 60


def mk(cls_name, **kw):
    obj = type(cls_name, (), {})()
    for k, v in kw.items():
        setattr(obj, k, v)
    return obj


class TestWithTransitions(unittest.TestCase):
    def test_with_expressions_and_batched_transitions(self):
        c = compile_script.Compiler(TransResolver())
        # Scene + Show + With open_eyes
        nodes = [
            mk("Scene", imspec=(("bg", "club_day"), None, None, [], None, None, [])),
            mk("Show", imspec=(("sayori", "1b"), None, None, ["face"], None, None, []), atl=None),
            mk("With", expr="open_eyes"),
            # Single Show + With dissolve
            mk("Show", imspec=(("natsuki", "1bi"), None, None, ["face"], None, None, []), atl=None),
            mk("With", expr="dissolve"),
            # CG Show + With dissolve_cg (fast path via OP_SCENE directly)
            mk("Show", imspec=(("y_cg1_exp1",), None, None, [], None, None, []), atl=None),
            mk("With", expr="dissolve_cg"),
            # Scene + With close_eyes (fast path via OP_SCENE directly)
            mk("Scene", imspec=(("black",), None, None, [], None, None, [])),
            mk("With", expr="close_eyes"),
        ]
        c.emit_block(nodes, "t.rpy")
        code = list(c.asm.code)
        # Verify OP_TRANS HOLD ... OP_TRANS OPEN_EYES ... OP_TRANS HOLD ... OP_TRANS DISSOLVE
        self.assertIn(vnasm.OP_TRANS, code)
        self.assertIn(vnasm.TRANS_HOLD, code)
        self.assertIn(vnasm.TRANS_EYES_OPEN, code)
        self.assertIn(vnasm.TRANS_DISSOLVE, code)
        # Verify fast paths produced OP_SCENE 60 TRANS_DISSOLVE and OP_SCENE 1 TRANS_EYES_CLOSE
        self.assertIn(bytes([vnasm.OP_SCENE, 60, vnasm.TRANS_DISSOLVE]), bytes(code))
        self.assertIn(bytes([vnasm.OP_SCENE, 1, vnasm.TRANS_EYES_CLOSE]), bytes(code))

    def test_c_transitions_palette_safety_and_vm_batch(self):
        render_src = (ROOT / "src" / "render.c").read_text()
        fade_start = render_src.index("#define FADE_STEP_MS")
        fade_end = render_src.index("\nvoid render_pause_box(")
        fade_slice = render_src[fade_start:fade_end]

        trans_start = render_src.index("#define DISSOLVE_STEPS")
        trans_slice = render_src[trans_start:]

        harness = f"""
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <assert.h>
#include "vn.h"

#define SCREEN_W 320
#define SCREEN_H 240
#define SCENE_H 180
#define COL_BLACK 1

static uint16_t gfx_palette[256];
static uint8_t mock_vscreen[SCREEN_W * SCREEN_H * 2];
static uint8_t mock_vbuffer[SCREEN_W * SCREEN_H * 2];
static uint8_t *gfx_vbuffer = mock_vbuffer;
#define gfx_vscreen mock_vscreen
static void gfx_SetDrawScreen(void) {{ gfx_vbuffer = mock_vscreen; }}
static void gfx_SetDrawBuffer(void) {{ gfx_vbuffer = mock_vbuffer; }}
static uint16_t gfx_Darken(uint16_t c, uint8_t amt) {{
    unsigned r = (((c >> 10) & 31U) * amt) >> 8;
    unsigned g = (((c >> 5) & 31U) * amt) >> 8;
    unsigned b = ((c & 31U) * amt) >> 8;
    return (uint16_t)((r << 10) | (g << 5) | b);
}}
static void msleep(unsigned ms) {{ (void)ms; }}
static void fast_rect_blit(uint8_t *d, size_t dp, const uint8_t *s, size_t sp, size_t w, size_t h) {{
    for (size_t r = 0; r < h; r++) memcpy(d + r * dp, s + r * sp, w);
}}
static void fast_bayer_dissolve(uint8_t *dst, const uint8_t *src, uint8_t step) {{
    static const uint8_t thr[4] = {{4, 8, 12, 16}};
    static const uint8_t b4[4][4] = {{
        {{0, 8, 2, 10}}, {{12, 4, 14, 6}}, {{3, 11, 1, 9}}, {{15, 7, 13, 5}}
    }};
    uint8_t t = thr[step & 3];
    for (int y = 0; y < SCENE_H; y++) {{
        for (int x = 0; x < SCREEN_W; x++) {{
            if (b4[y & 3][x & 3] < t) dst[y * SCREEN_W + x] = src[y * SCREEN_W + x];
        }}
    }}
}}

{fade_slice}
{trans_slice}

static uint8_t seen_trans[8];
static uint8_t seen_n = 0;
static void h_upd(void *ctx, const vn_scene_t *s, uint8_t t) {{ (void)ctx; (void)s; seen_trans[seen_n++] = t; }}
static const char *h_str(void *ctx, uint16_t i) {{ (void)ctx; (void)i; return ""; }}
static void h_say(void *ctx, const vn_scene_t *s) {{ (void)ctx; (void)s; }}
static uint8_t h_menu(void *ctx, const vn_scene_t *s, const char *const *c, uint8_t n) {{ (void)ctx; (void)s; (void)c; (void)n; return 0; }}
static void h_pause(void *ctx, uint32_t ms) {{ (void)ctx; (void)ms; }}
static bool h_quit(void *ctx) {{ (void)ctx; return false; }}

int main(void) {{
    uint16_t palA[256], palB[256];
    for (unsigned i = 0; i < 256; i++) {{
        palA[i] = (uint16_t)(0x7FFFU - i);
        palB[i] = (uint16_t)(0x0400U + i);
    }}
    render_apply_palette(palA);
    assert(render_palette_matches(palA));
    assert(!render_palette_matches(palB));

    /* Dissolve across differing palettes keeps screen black through retarget */
    render_dissolve_out();
    assert(gfx_palette[200] == 0);
    render_fade_retarget(palB);
    assert(gfx_palette[200] == 0);
    render_dissolve_in();
    assert(memcmp(gfx_palette, palB, sizeof(palB)) == 0);

    /* Same-pal Bayer, wipe, close_eyes, open_eyes leave gfx_vscreen equal to gfx_vbuffer */
    for (int i = 0; i < SCREEN_W * SCREEN_H * 2; i++) {{
        gfx_vscreen[i] = 9;
        gfx_vbuffer[i] = (uint8_t)(20 + (i % 200));
    }}
    render_dissolve_bayer();
    /* UI rows [180..239] updated */
    assert(memcmp(gfx_vscreen + SCENE_H * SCREEN_W, gfx_vbuffer + SCENE_H * SCREEN_W,
                  (SCREEN_H - SCENE_H) * SCREEN_W) == 0);

    memset(gfx_vscreen, 9, SCREEN_W * SCREEN_H);
    render_wipe_curtain_out();
    for (int i = 0; i < SCREEN_W * SCENE_H; i++) assert(gfx_vscreen[i] == COL_BLACK);
    render_wipe_left_reveal();
    assert(memcmp(gfx_vscreen, gfx_vbuffer, SCREEN_W * SCENE_H) == 0);

    memset(gfx_vscreen, 7, SCREEN_W * SCREEN_H);
    render_eyes_close_anim();
    for (int i = 0; i < SCREEN_W * SCENE_H; i++) assert(gfx_vscreen[i] == COL_BLACK);
    render_eyes_open_reveal();
    assert(memcmp(gfx_vscreen, gfx_vbuffer, SCREEN_W * SCENE_H) == 0);

    /* VM OP_TRANS batching suppresses intermediate updates and fires closing trans once */
    uint8_t code[] = {{
        OP_TRANS, TRANS_HOLD,
        OP_SCENE, 4, TRANS_CUT,
        OP_SHOW, 0, 1, 0, 0xFF, 0xFF, 80, 0,
        OP_TRANS, TRANS_EYES_OPEN,
        OP_END
    }};
    vn_host_t host = {{
        .string = h_str, .update = h_upd, .say = h_say,
        .menu = h_menu, .pause = h_pause, .quit = h_quit,
    }};
    vn_vm_t vm;
    vn_init(&vm, code, sizeof(code), &host, 1);
    while (vn_step(&vm)) {{}}
    assert(seen_n == 1 && seen_trans[0] == TRANS_EYES_OPEN);
    return 0;
}}
"""
        with tempfile.TemporaryDirectory() as tmp:
            cp = Path(tmp) / "t_trans.c"
            bp = Path(tmp) / "t_trans"
            cp.write_text(harness)
            subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                 "-I", str(ROOT / "src"), str(cp), str(ROOT / "src" / "vn.c"), "-o", str(bp)],
                check=True,
            )
            subprocess.run([str(bp)], check=True)


if __name__ == "__main__":
    unittest.main()
