"""Tests for Act 3 Monika textbox style and non-repeating topic shuffle."""

import ast
import subprocess
import tempfile
import unittest
from pathlib import Path

import compile_script
import vnasm

ROOT = Path(__file__).resolve().parent.parent


class DummyResolver:
    table: dict = {}


class Act3MonikaUITests(unittest.TestCase):
    def test_compiler_emits_window_style_and_monika_topic_without_overwriting_topic_1(self) -> None:
        c = compile_script.Compiler(resolver=DummyResolver())
        for src in (
            "style.say_window = style.window_monika",
            "style.say_window = style.window",
            "persistent.monikatopics = []",
            "if len(persistent.monikatopics) == 0:\n    persistent.monikatopics = range(1,57)",
            "persistent.current_monikatopic = random.choice(persistent.monikatopics)",
            "persistent.monikatopics.remove(persistent.current_monikatopic)",
        ):
            stmt = ast.parse(src).body[0]
            self.assertTrue(c._emit_python_stmt(stmt), f"failed to emit: {src}")
        for k in c.MONIKATOPIC_VALUES:
            c.asm.label(f"ch30_{k}")
            c.asm.end()

        code = bytes(c.asm.code)
        # First two instructions must be OP_WINDOW_STYLE 1 and OP_WINDOW_STYLE 0
        self.assertEqual(code[0:4], bytes([vnasm.OP_WINDOW_STYLE, 1, vnasm.OP_WINDOW_STYLE, 0]))
        self.assertIn(vnasm.OP_MONIKA_TOPIC, code)
        self.assertIn(27, c.MONIKATOPIC_VALUES)

    def test_vm_monika_topic_non_repeating_and_topic27_gating(self) -> None:
        harness = r"""
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include "vn.h"

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
    uint8_t code[16] = {
        OP_WINDOW_STYLE, 1,
        OP_MONIKA_TOPIC, 0, 1, 2, 3, 4, 5,
        OP_END
    };
    vn_vm_t vm;
    vn_init(&vm, code, sizeof(code), &host, 12345);

    /* Run 52 draws without seen_colors_poem: must see every one of the 52 topics once. */
    uint8_t seen[64] = {0};
    for (int i = 0; i < 52; i++) {
        vm.pc = 0;
        vm.status = VN_RUNNING;
        assert(vn_run(&vm) == VN_FINISHED);
        assert(vm.scene.box_style == 1);
        int16_t t = vm.vars[0];
        assert(t >= 1 && t <= 56);
        assert(t != 14 && t != 25 && t != 26 && t != 27);
        assert(!seen[t]);
        seen[t] = 1;
    }

    /* 53rd draw refills cleanly; enable seen_colors_poem and draw 53 times to see topic 27 once. */
    vm.vars[1] = 1;
    vm.vars[2] = vm.vars[3] = vm.vars[4] = vm.vars[5] = 0;
    uint8_t seen2[64] = {0};
    for (int i = 0; i < 53; i++) {
        vm.pc = 2;
        vm.status = VN_RUNNING;
        assert(vn_run(&vm) == VN_FINISHED);
        int16_t t = vm.vars[0];
        assert(t >= 1 && t <= 56 && t != 14 && t != 25 && t != 26);
        assert(!seen2[t]);
        seen2[t] = 1;
    }
    assert(seen2[27] == 1);
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as td:
            src_c = Path(td) / "harness.c"
            exe = Path(td) / "harness"
            src_c.write_text(harness)
            subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "src"),
                 str(src_c), str(ROOT / "src/vn.c"), "-o", str(exe)],
                check=True,
            )
            subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    unittest.main()
