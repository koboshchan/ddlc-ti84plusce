"""Tests for `show screen invert(length, delay)` compilation and VM timing."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import compile_script
import vnasm

ROOT = Path(__file__).resolve().parent.parent


class DummyResolver:
    table: dict = {}


class InvertEffectTests(unittest.TestCase):
    def test_invert_statement_compiles_to_op_effect(self) -> None:
        c = compile_script.Compiler(resolver=DummyResolver())
        node = type("UserStatement", (), {})()
        node.line = "show screen invert(0.15, 0.3)"
        node.linenumber = 139
        c.emit_node(node, "script-ch20.rpyc")

        # OP_EFFECT(0x26), fx=1, delay_ms=300 (0x012c), length_ms=150 (0x0096)
        self.assertEqual(
            bytes(c.asm.code),
            bytes([vnasm.OP_EFFECT, vnasm.VN_EFFECT_INVERT, 0x2C, 0x01, 0x96, 0x00]),
        )

    def test_vm_and_palette_invert_toggle(self) -> None:
        harness = r"""
#include <assert.h>
#include <stdint.h>
#include <string.h>
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
    uint8_t code[] = {
        OP_EFFECT, VN_EFFECT_INVERT, 0x2C, 0x01, 0x96, 0x00,
        OP_END
    };
    vn_vm_t vm;
    vn_init(&vm, code, sizeof(code), &host, 1);
    assert(vn_run(&vm) == VN_FINISHED);
    assert(vm.scene.timed_fx == VN_EFFECT_INVERT);
    assert(vm.scene.timed_fx_delay_ms == 300);
    assert(vm.scene.timed_fx_len_ms == 150);
    assert(vm.scene.timed_fx_seq == 1);

    /* OP_SCENE clears timed_fx cleanly */
    uint8_t clear_code[] = { OP_SCENE, 5, TRANS_CUT, OP_END };
    vn_init(&vm, clear_code, sizeof(clear_code), &host, 1);
    vm.scene.timed_fx = VN_EFFECT_INVERT;
    assert(vn_run(&vm) == VN_FINISHED);
    assert(vm.scene.timed_fx == VN_EFFECT_NONE);
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
