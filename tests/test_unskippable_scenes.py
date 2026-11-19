"""Tests for unskippable scenes: OP_SKIP_LOCK, OP_PAUSE_HARD, and {nw} pacing."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

import compile_script
import vnasm

ROOT = Path(__file__).resolve().parent.parent


class DummyResolver:
    table = {}
    def condswitch_variants(self, imgname): return None
    def sprite_layers(self, imgname): return (5, vnasm.NO_OVERLAY)
    def scene_id(self, imgname): return 1


def mk(cls_name: str, **kw):
    obj = type(cls_name, (), {})()
    for k, v in kw.items():
        setattr(obj, k, v)
    return obj


def mk_py(source: str):
    return mk("Python", code=mk("PyCode", _state=(None, source)))


class TestUnskippableScenes(unittest.TestCase):
    def test_compiler_emits_skip_lock_and_hard_pause(self) -> None:
        c = compile_script.Compiler(DummyResolver())
        nodes = [
            mk_py("_dismiss_pause = config.developer"),
            mk_py("config.allow_skipping = False"),
            mk_py("renpy.pause(2.5, hard=True)"),
            mk_py("pause(1.5)"),
            mk_py("config.allow_skipping = True"),
        ]
        c.emit_block(nodes, "test.rpyc")
        code = bytes(c.asm.code)
        self.assertIn(bytes([vnasm.OP_SKIP_LOCK, vnasm.VN_DISMISS_PAUSE_LOCK]), code)
        self.assertIn(bytes([vnasm.OP_SKIP_LOCK, vnasm.VN_SKIP_LOCK]), code)
        self.assertIn(bytes([vnasm.OP_SKIP_LOCK, vnasm.VN_SKIP_UNLOCK]), code)
        self.assertIn(bytes([vnasm.OP_PAUSE_HARD]) + (2500).to_bytes(4, "little"), code)
        self.assertIn(bytes([vnasm.OP_PAUSE]) + (1500).to_bytes(2, "little"), code)

    def test_vm_executes_skip_lock_and_pause_hard_flags(self) -> None:
        harness = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include "vn.h"

static uint32_t pause_ms[4];
static bool pause_hard_seen[4];
static bool pause_skip_seen[4];
static bool pause_dismiss_seen[4];
static int pause_calls = 0;

static const char *h_str(void *c, uint16_t i) { (void)c; (void)i; return ""; }
static void h_upd(void *c, const vn_scene_t *s, uint8_t t) { (void)c; (void)s; (void)t; }
static void h_say(void *c, const vn_scene_t *s) { (void)c; (void)s; }
static uint8_t h_menu(void *c, const vn_scene_t *s, const char *const *o, uint8_t n) {
    (void)c; (void)s; (void)o; (void)n; return 0;
}
static void h_pause(void *c, uint32_t ms) {
    vn_vm_t *vm = (vn_vm_t *)c;
    assert(pause_calls < 4);
    pause_ms[pause_calls] = ms;
    pause_hard_seen[pause_calls] = vm->pause_hard;
    pause_skip_seen[pause_calls] = vm->skip_locked;
    pause_dismiss_seen[pause_calls] = vm->dismiss_pause_locked;
    pause_calls++;
}
static bool h_quit(void *c) { (void)c; return false; }

int main(void) {
    uint8_t code[64];
    size_t pc = 0;
    /* 1. Lock dismiss_pause, call normal pause(1000) */
    code[pc++] = OP_SKIP_LOCK; code[pc++] = 2;
    code[pc++] = OP_PAUSE; code[pc++] = 0xE8; code[pc++] = 0x03;
    /* 2. Lock skip, call pause_hard(2500) */
    code[pc++] = OP_SKIP_LOCK; code[pc++] = 1;
    code[pc++] = OP_PAUSE_HARD;
    code[pc++] = 0xC4; code[pc++] = 0x09; code[pc++] = 0x00; code[pc++] = 0x00;
    /* 3. Unlock skip & dismiss_pause, call normal pause(500) */
    code[pc++] = OP_SKIP_LOCK; code[pc++] = 0;
    code[pc++] = OP_SKIP_LOCK; code[pc++] = 3;
    code[pc++] = OP_PAUSE; code[pc++] = 0xF4; code[pc++] = 0x01;
    code[pc++] = OP_END;

    vn_vm_t vm;
    vn_host_t host = {0};
    host.ctx = &vm;
    host.string = h_str;
    host.update = h_upd;
    host.say = h_say;
    host.menu = h_menu;
    host.pause = h_pause;
    host.quit = h_quit;
    vn_init(&vm, code, pc, &host, 1);
    while (vn_step(&vm)) {}

    assert(vm.status == VN_FINISHED);
    assert(pause_calls == 3);
    assert(pause_ms[0] == 1000 && !pause_hard_seen[0] && !pause_skip_seen[0] && pause_dismiss_seen[0]);
    assert(pause_ms[1] == 2500 && pause_hard_seen[1] && pause_skip_seen[1] && pause_dismiss_seen[1]);
    assert(pause_ms[2] == 500 && !pause_hard_seen[2] && !pause_skip_seen[2] && !pause_dismiss_seen[2]);
    assert(!vm.pause_hard && !vm.skip_locked && !vm.dismiss_pause_locked);
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "test.c").write_text(harness)
            subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "src"),
                 str(p / "test.c"), str(ROOT / "src/vn.c"), "-o", str(p / "test")],
                check=True,
            )
            subprocess.run([str(p / "test")], check=True)


if __name__ == "__main__":
    unittest.main()
