"""Tests for inline Show+ATL palette effects & CG micro-animations (Phase 4 #14)."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import compile_script
import vnasm

ROOT = Path(__file__).resolve().parent.parent


class MicroAnimResolver:
    table = {
        ("black",): type("Def", (), {"kind": "solid"})(),
        ("white",): type("Def", (), {"kind": "solid"})(),
        ("darkred",): type("Def", (), {"kind": "solid"})(),
    }

    def condswitch_variants(self, imgname):
        return None

    def sprite_layers(self, imgname):
        return (5, vnasm.NO_OVERLAY)

    def scene_id(self, imgname):
        return {
            ("bg", "club_day"): 10,
            ("n_blackeyes",): 44,
            ("black",): 1,
            ("white",): 2,
        }.get(imgname)

    def cg_composite_scene_id(self, layers):
        return 60


def mknode(cls_name, **kw):
    obj = type(cls_name, (), {})()
    for k, v in kw.items():
        setattr(obj, k, v)
    return obj


def mkatl(stmts):
    return mknode("RawBlock", statements=stmts)


def mp(dur="0", exprs=None, props=None):
    return mknode(
        "RawMultipurpose",
        duration=dur,
        warper="linear" if dur != "0" else None,
        expressions=[(e, None) for e in (exprs or [])],
        properties=list(props or []),
    )


class TestShowAtlMicroAnims(unittest.TestCase):
    def test_white_black_red_atl_compile_to_palette_fx(self):
        c = compile_script.Compiler(MicroAnimResolver())

        # white flash fade
        c.emit_node(mknode("Show", imspec=(("white",), None, None, [], None, "4", []),
                           atl=mkatl([mp(props=[("alpha", "0.6")]), mp("0.25", props=[("alpha", "0.0")])])), "t.rpy")
        c.emit_node(mknode("Hide", imspec=(("white",), None, None, [], None, None, [])), "t.rpy")

        # black fadeout
        c.emit_node(mknode("Show", imspec=(("black",), None, "fadeout", [], None, None, []),
                           atl=mkatl([mp(props=[("alpha", "0")]), mp("0", exprs=["0.25"]), mp("3.0", props=[("alpha", "1.00")])])), "t.rpy")
        c.emit_node(mknode("Hide", imspec=(("fadeout",), None, None, [], None, None, [])), "t.rpy")

        # black ambient dim 0.5
        c.emit_node(mknode("Show", imspec=(("black",), None, "b2", [], None, None, []),
                           atl=mkatl([mp(props=[("alpha", "0.5")])])), "t.rpy")
        c.emit_node(mknode("Hide", imspec=(("b2",), None, None, [], None, None, [])), "t.rpy")

        # darkred fade
        c.emit_node(mknode("Show", imspec=(("darkred",), None, None, [], None, None, []),
                           atl=mkatl([mp(props=[("alpha", "0")]), mp("2.0", props=[("alpha", "1.0")])])), "t.rpy")

        # Parse OP_EFFECT ops from code
        code = c.asm.code
        effects = []
        i = 0
        while i < len(code):
            op = code[i]
            self.assertEqual(op, vnasm.OP_EFFECT)
            dly = int.from_bytes(code[i + 2:i + 4], "little")
            length = int.from_bytes(code[i + 4:i + 6], "little")
            effects.append((code[i + 1], dly, length))
            i += 6
        self.assertEqual(
            effects,
            [
                (vnasm.VN_EFFECT_FLASH_WHITE, 0, 250),
                (vnasm.VN_EFFECT_NONE, 0, 0),
                (vnasm.VN_EFFECT_FADE_BLACK, 250, 3000),
                (vnasm.VN_EFFECT_NONE, 0, 0),
                (vnasm.VN_EFFECT_DIM_HALF, 0, 0),
                (vnasm.VN_EFFECT_NONE, 0, 0),
                (vnasm.VN_EFFECT_TINT_RED, 0, 2000),
            ],
        )

    def test_dust_cg_wiggle_n_eye_and_malpha(self):
        c = compile_script.Compiler(MicroAnimResolver())
        # y_cg2_dust1 + n_cg2_wiggle + say
        c.emit_node(mknode("Show", imspec=(("n_cg2_base",), None, None, ["n_cg2_wiggle"], None, None, []), atl=None), "t.rpy")
        c.emit_node(mknode("Say", who=None, what="hi", attributes=None), "t.rpy")
        self.assertIn(vnasm.OP_LAYER_FX, c.asm.code)

        c2 = compile_script.Compiler(MicroAnimResolver())
        c2.emit_node(mknode("Show", imspec=(("y_cg2_dust1",), None, None, [], None, None, []), atl=None), "t.rpy")
        c2.emit_node(mknode("Say", who=None, what="dust", attributes=None), "t.rpy")
        self.assertIn(vnasm.OP_OVERLAY_FX, c2.asm.code)

        # malpha suppresses first 16 calls, shows on 17th
        c3 = compile_script.Compiler(MicroAnimResolver())
        for _ in range(16):
            c3.emit_node(mknode("Show", imspec=(("monika", "1"), None, None, ["malpha(ac / ad)"], None, None, []), atl=None), "t.rpy")
        self.assertEqual(len(c3.asm.code), 0)
        c3.emit_node(mknode("Show", imspec=(("monika", "1"), None, None, ["malpha(ac / ad)"], None, None, []), atl=None), "t.rpy")
        self.assertIn(vnasm.OP_SHOW, c3.asm.code)

        # n_eye does not replace n_blackeyes scene
        c4 = compile_script.Compiler(MicroAnimResolver())
        c4.emit_node(mknode("Show", imspec=(("n_blackeyes",), None, None, ["i11"], None, None, []), atl=None), "t.rpy")
        before = len(c4.asm.code)
        c4.emit_node(mknode("Show", imspec=(("n_eye",), None, None, [], None, None, []), atl=None), "t.rpy")
        self.assertEqual(len(c4.asm.code), before)

    def test_c_timed_palette_effects_preserve_ui_indices(self):
        render_src = (ROOT / "src" / "render.c").read_text()
        start = render_src.index("void render_timed_fx_tick(")
        end = render_src.index("\nstatic uint8_t  layer_s_fx;")
        fx_slice = render_src[start:end]

        harness = f"""
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <assert.h>
#include "vn.h"

static uint16_t gfx_palette[256];
static uint16_t current_palette[256];
static bool timed_fx_inverted = false;
static bool room_mask_applied = false;
static unsigned test_ms = 0;
static unsigned clock(void) {{ return test_ms; }}
static unsigned ticks_to_ms(unsigned c) {{ return c; }}

{fx_slice}

int main(void) {{
    for (unsigned i = 0; i < 256; i++) {{
        /* mid gray-cyan RGB555 */
        current_palette[i] = (uint16_t)((16U << 10) | (16U << 5) | 16U);
        gfx_palette[i] = current_palette[i];
    }}

    vn_scene_t sc;
    memset(&sc, 0, sizeof(sc));

    /* FADE_BLACK keeps UI entries [0..15] intact and darkens [16..255] */
    sc.timed_fx = VN_EFFECT_FADE_BLACK;
    sc.timed_fx_seq = 1;
    sc.timed_fx_delay_ms = 100;
    sc.timed_fx_len_ms = 1000;
    test_ms = 0;
    render_timed_fx_tick(&sc);
    assert(gfx_palette[16] == current_palette[16]); /* still in delay */
    test_ms = 1100;
    render_timed_fx_tick(&sc);
    assert(gfx_palette[1] == current_palette[1]);
    assert(gfx_palette[16] == 0);

    /* FLASH_WHITE spikes white and restores cleanly when finished */
    sc.timed_fx = VN_EFFECT_FLASH_WHITE;
    sc.timed_fx_seq = 2;
    sc.timed_fx_delay_ms = 0;
    sc.timed_fx_len_ms = 250;
    test_ms = 1100;
    render_timed_fx_tick(&sc);
    assert(gfx_palette[20] > current_palette[20]);
    test_ms = 1500;
    render_timed_fx_tick(&sc);
    assert(gfx_palette[20] == current_palette[20]);

    /* TINT_RED increases R relative to G/B */
    sc.timed_fx = VN_EFFECT_TINT_RED;
    sc.timed_fx_seq = 3;
    sc.timed_fx_delay_ms = 0;
    sc.timed_fx_len_ms = 0;
    test_ms = 1600;
    render_timed_fx_tick(&sc);
    unsigned r = (gfx_palette[20] >> 10) & 31U;
    unsigned g = (gfx_palette[20] >> 5) & 31U;
    unsigned b = gfx_palette[20] & 31U;
    assert(r >= 16U && g < 8U && b < 8U);

    /* VN_EFFECT_NONE restores palette */
    sc.timed_fx = VN_EFFECT_NONE;
    render_timed_fx_tick(&sc);
    assert(memcmp(gfx_palette, current_palette, sizeof(gfx_palette)) == 0);
    return 0;
}}
"""
        with tempfile.TemporaryDirectory() as tmp:
            cp = Path(tmp) / "t.c"
            bp = Path(tmp) / "t"
            cp.write_text(harness)
            subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                 "-I", str(ROOT / "src"), str(cp), "-o", str(bp)],
                check=True,
            )
            subprocess.run([str(bp)], check=True)


if __name__ == "__main__":
    unittest.main()
