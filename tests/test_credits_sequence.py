"""Tests for end-credits sequence (Phase 5 #16: OP_CREDITS + src/credits.c)."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import compile_script
import vnasm

ROOT = Path(__file__).resolve().parent.parent


class CreditsResolver:
    table = {}

    def condswitch_variants(self, imgname):
        return None

    def sprite_layers(self, imgname):
        return (5, vnasm.NO_OVERLAY)

    def scene_id(self, imgname):
        mapping = {
            ("credits_logo",): 70,
            ("credits_ts",): 71,
            ("end_glitch1",): 72,
            ("poem_end",): 73,
            ("black",): 1,
        }
        for i in range(1, 11):
            mapping[(f"credits_cg{i}",)] = 80 + i
        return mapping.get(imgname)


def mk(cls_name, **kw):
    obj = type(cls_name, (), {})()
    for k, v in kw.items():
        setattr(obj, k, v)
    return obj


class TestCreditsSequence(unittest.TestCase):
    def test_credits_labels_compile_to_op_credits_and_preserve_poem_end(self):
        c = compile_script.Compiler(CreditsResolver())
        lbl1 = mk("Label", name="credits", block=[mk("UserStatement", line="pause 41")])
        lbl2 = mk("Label", name="credits2", block=[
            mk("Show", imspec=(("credits_logo",), None, None, [], None, None, []), atl=None),
            mk("UserStatement", line="pause 9.12"),
            mk("Show", imspec=(("poem_end",), None, None, [], None, None, []), atl=None),
            mk("With", expr="Dissolve(1)"),
        ])
        c.emit_block([lbl1, lbl2], "credits.rpyc")
        code = c.asm.code
        self.assertEqual(code.count(vnasm.OP_CREDITS), 2)
        # Ensure poem_end dissolves in via batched OP_TRANS(HOLD) -> OP_SCENE 73 -> OP_TRANS(DISSOLVE)
        self.assertIn(
            bytes([vnasm.OP_TRANS, vnasm.TRANS_HOLD,
                   vnasm.OP_SCENE, 73, vnasm.TRANS_CUT,
                   vnasm.OP_TRANS, vnasm.TRANS_DISSOLVE]),
            bytes(code),
        )
        # Stage 0 and Stage 1 opcodes present with resolved credits scene IDs
        idx0 = code.index(vnasm.OP_CREDITS)
        self.assertEqual(code[idx0 + 1], 0)
        self.assertEqual(list(code[idx0 + 13:idx0 + 26]), [70, 71, 72, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90])

    def test_vm_op_credits_computes_clearall_and_clear_mask(self):
        harness = r"""
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include "vn.h"

static uint8_t  got_stage = 99;
static bool     got_clearall = false;
static uint16_t got_mask = 0;
static uint8_t  got_scenes[13];

static void h_credits(void *ctx, uint8_t stage, bool clearall, uint16_t mask, const uint8_t *scenes) {
    (void)ctx;
    got_stage = stage;
    got_clearall = clearall;
    got_mask = mask;
    for (int i = 0; i < 13; i++) got_scenes[i] = scenes[i];
}
static const char *h_str(void *c, uint16_t i) { (void)c; (void)i; return ""; }
static void h_upd(void *c, const vn_scene_t *s, uint8_t t) { (void)c; (void)s; (void)t; }
static void h_say(void *c, const vn_scene_t *s) { (void)c; (void)s; }
static uint8_t h_menu(void *c, const vn_scene_t *s, const char *const *o, uint8_t n) { (void)c; (void)s; (void)o; (void)n; return 0; }
static void h_pause(void *c, uint32_t ms) { (void)c; (void)ms; }
static bool h_quit(void *c) { (void)c; return false; }

int main(void) {
    uint8_t code[64];
    size_t pc = 0;
    /* Set var[20]=0 (clearall), var[21]=1 (cg1), var[25]=1 (cg5), var[30]=1 (cg10) */
    code[pc++] = OP_SET; code[pc++] = 21; code[pc++] = 1; code[pc++] = 0;
    code[pc++] = OP_SET; code[pc++] = 25; code[pc++] = 1; code[pc++] = 0;
    code[pc++] = OP_SET; code[pc++] = 30; code[pc++] = 1; code[pc++] = 0;
    code[pc++] = OP_CREDITS;
    code[pc++] = 1;  /* stage */
    code[pc++] = 20; /* clearall */
    for (uint8_t i = 0; i < 10; i++) code[pc++] = (uint8_t)(21 + i);
    for (uint8_t i = 0; i < 13; i++) code[pc++] = (uint8_t)(70 + i);
    code[pc++] = OP_END;

    vn_host_t host = {
        .string = h_str, .update = h_upd, .say = h_say,
        .menu = h_menu, .pause = h_pause, .quit = h_quit, .credits = h_credits,
    };
    vn_vm_t vm;
    vn_init(&vm, code, pc, &host, 1);
    while (vn_step(&vm)) {}
    assert(vm.status == VN_FINISHED);
    assert(got_stage == 1);
    assert(!got_clearall);
    assert(got_mask == ((1U << 0) | (1U << 4) | (1U << 9)));
    assert(got_scenes[0] == 70 && got_scenes[12] == 82);
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            cp = Path(tmp) / "t_cred.c"
            bp = Path(tmp) / "t_cred"
            cp.write_text(harness)
            subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                 "-I", str(ROOT / "src"), str(cp), str(ROOT / "src" / "vn.c"), "-o", str(bp)],
                check=True,
            )
            subprocess.run([str(bp)], check=True)


if __name__ == "__main__":
    unittest.main()
