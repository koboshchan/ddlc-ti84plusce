"""Tests for Commit 2 Act 2/3 polish opcodes and compiler lowering."""

from __future__ import annotations

import ast
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import compile_script
import image_resolve
import vnasm

_FIXED_OPS = {
    vnasm.OP_NOP: 0,
    vnasm.OP_SAY: 3,
    vnasm.OP_SCENE: 2,
    vnasm.OP_SHOW: 7,
    vnasm.OP_HIDE: 1,
    vnasm.OP_JUMP: 3,
    vnasm.OP_CALL: 3,
    vnasm.OP_RETURN: 0,
    vnasm.OP_SET: 3,
    vnasm.OP_IF: 7,
    vnasm.OP_PAUSE: 2,
    vnasm.OP_SOUND: 1,
    vnasm.OP_END: 0,
    vnasm.OP_ADD: 3,
    vnasm.OP_MINIGAME: 4,
    vnasm.OP_RANDOM: 5,
    vnasm.OP_JUMP_VAR: 1,
    vnasm.OP_CALL_VAR: 1,
    vnasm.OP_DELETE_SAVES: 0,
    vnasm.OP_TEAR_SHOW: 7,
    vnasm.OP_TEAR_HIDE: 0,
    vnasm.OP_WINDOW_HIDE: 0,
    vnasm.OP_WINDOW_SHOW: 0,
    vnasm.OP_GLITCHTEXT: 2,
    vnasm.OP_CHAR_CHECK: 4,
    vnasm.OP_CHAR_DELETE: 1,
    vnasm.OP_QUIT: 0,
    vnasm.OP_PAUSE_LONG: 4,
    vnasm.OP_POEM_VIEW: 1,
    vnasm.OP_NOISE: 1,
    0x20: 0,  # OP_WINDOW_AUTO
    0x21: 5,  # OP_TEXT_OVERLAY
    vnasm.OP_MOTION: 2,
    vnasm.OP_WINDOW_STYLE: 1,
    vnasm.OP_MONIKA_TOPIC: 6,
    vnasm.OP_EFFECT: 5,
    vnasm.OP_OVERLAY_FX: 2,
    vnasm.OP_LAYER_FX: 3,
    vnasm.OP_TRANS: 1,
    vnasm.OP_CREDITS: 25,
    vnasm.OP_TEXT_STYLE: 1,
    vnasm.OP_JMP_VAR_CMP: 6,
}


def _scan(code: bytes) -> list[tuple[int, bytes]]:
    out = []
    pc = 0
    while pc < len(code):
        op = code[pc]
        pc += 1
        if op == vnasm.OP_CONSOLE:
            subop = code[pc]
            n = 5 if subop in (vnasm.VN_CONSOLE_SHOW, vnasm.VN_CONSOLE_SHOW_CLR) else 1
            out.append((op, code[pc:pc + n]))
            pc += n
        elif op == vnasm.OP_MENU:
            count = code[pc]
            end = pc + 1 + count * 5
            out.append((op, code[pc:end]))
            pc = end
        elif op == vnasm.OP_MENU_SPECIAL:
            count = code[pc + 2]
            end = pc + 3 + count * 2
            out.append((op, code[pc:end]))
            pc = end
        elif op == vnasm.OP_MENU_COND:
            end = pc + 1
            count = code[pc]
            for _ in range(count):
                cc = code[end]
                end += 1 + cc * 4 + 5
            out.append((op, code[pc:end]))
            pc = end
        elif op in _FIXED_OPS:
            n = _FIXED_OPS[op]
            out.append((op, code[pc:pc + n]))
            pc += n
        else:
            raise AssertionError(f"Unknown opcode 0x{op:02x} at {pc - 1}")
    return out


class DummyResolver:
    table: dict = {}

    def condswitch_variants(self, _imgname):
        return None


class Act23PolishTests(unittest.TestCase):
    def test_assembler_and_python_lowering_units(self) -> None:
        c = compile_script.Compiler(resolver=DummyResolver())
        for src in (
            "style.say_dialogue = style.edited",
            "style.say_dialogue = style.normal",
            "waittime = renpy.random.randint(4, 8)",
        ):
            stmt = ast.parse(src).body[0]
            self.assertTrue(c._emit_python_stmt(stmt), f"failed to emit: {src}")

        code = bytes(c.asm.code)
        ops = _scan(code)
        self.assertEqual(ops[0], (vnasm.OP_TEXT_STYLE, bytes([1])))
        self.assertEqual(ops[1], (vnasm.OP_TEXT_STYLE, bytes([0])))
        self.assertEqual(ops[2][0], vnasm.OP_RANDOM)
        var_id, lo, hi = struct.unpack("<Bhh", ops[2][1])
        self.assertEqual(var_id, c.variables["waittime"])
        self.assertEqual((lo, hi), (4, 8))

    @unittest.skipUnless((ROOT / "assets/raw/script-ch30.rpyc").is_file(),
                         "extracted .rpyc assets required")
    def test_real_scripts_emit_polish_opcodes_without_yuri_regression(self) -> None:
        stems = [
            "script",
            "script-ch21",
            "script-ch22",
            "script-poemresponses",
            "script-poemresponses2",
            "script-ch30",
        ]
        raw_dir = ROOT / "assets/raw"
        with tempfile.TemporaryDirectory() as tmp:
            resolver = image_resolve.ImageResolver(raw_dir, Path(tmp))
            compiler = compile_script.Compiler(
                resolver=resolver,
                transform_animations=compile_script.load_transform_animations(raw_dir),
            )
            compiler.label_params = compile_script.load_label_params(raw_dir, stems)

            by_stem: dict[str, list[tuple[int, bytes]]] = {}
            for stem in stems:
                chunk_ops: list[tuple[int, bytes]] = []
                compiler.asm = vnasm.Assembler(chunk_id=0)
                for asm in compiler.compile_file_chunked(raw_dir / f"{stem}.rpyc", 0):
                    chunk_ops.extend(_scan(bytes(asm.code)))
                by_stem[stem] = chunk_ops

        # 1. style.edited / style.normal -> OP_TEXT_STYLE(1 / 0)
        ch22_styles = [payload[0] for op, payload in by_stem["script-ch22"] if op == vnasm.OP_TEXT_STYLE]
        self.assertIn(1, ch22_styles)
        self.assertIn(0, ch22_styles)

        # 2. n_poemappeal[2] > y_poemappeal[2] -> OP_JMP_VAR_CMP
        var_cmps = [
            (payload[0], payload[1], payload[2])
            for op, payload in by_stem["script-poemresponses"]
            if op == vnasm.OP_JMP_VAR_CMP
        ]
        self.assertTrue(var_cmps, "expected OP_JMP_VAR_CMP in script-poemresponses")
        n2 = compiler.variables["n_poemappeal[2]"]
        y2 = compiler.variables["y_poemappeal[2]"]
        self.assertIn((n2, vnasm.CMP_GT, y2), var_cmps)

        # 3. renpy.call_screen("dialog", ...) -> VN_MENU_CONFIRM_OK
        confirm_menus = [
            payload
            for op, payload in by_stem["script-poemresponses2"]
            if op == vnasm.OP_MENU_SPECIAL and payload[0] == vnasm.VN_MENU_CONFIRM_OK
        ]
        self.assertGreaterEqual(len(confirm_menus), 2)

        # 4. ch30 fake_skip_indicator and waittime randint(4, 8), plus room_mask hide via Show/Hide AST
        ch30_ovl = [
            (payload[0], payload[1])
            for op, payload in by_stem["script-ch30"]
            if op == vnasm.OP_OVERLAY_FX
        ]
        self.assertIn((vnasm.VN_OVL_FAKE_SKIP, 1), ch30_ovl)
        self.assertIn((vnasm.VN_OVL_FAKE_SKIP, 0), ch30_ovl)

        wt_var = compiler.variables["waittime"]
        ch30_rand = [
            struct.unpack("<Bhh", payload)
            for op, payload in by_stem["script-ch30"]
            if op == vnasm.OP_RANDOM
        ]
        self.assertIn((wt_var, 4, 8), ch30_rand)

        # Verify Hide rm/rm2 after Show room_mask as rm emits (VN_OVL_ROOM_MASK, 0) cleanly
        def make_node(cls_name, imspec):
            cls = type(cls_name, (), {})
            n = cls()
            n.imspec = imspec
            n.atl = None
            return n

        rm_c = compile_script.Compiler(resolver=DummyResolver())
        rm_c.emit_node(make_node("Show", (("room_mask",), None, "rm", [], None, None, [])), "script-ch30.rpyc")
        rm_c.emit_node(make_node("Show", (("room_mask2",), None, "rm2", [], None, None, [])), "script-ch30.rpyc")
        rm_c.emit_node(make_node("Hide", (("rm",), None, None, [], None, None, [])), "script-ch30.rpyc")
        rm_c.emit_node(make_node("Hide", (("rm2",), None, None, [], None, None, [])), "script-ch30.rpyc")
        rm_ovl = [
            (payload[0], payload[1])
            for op, payload in _scan(bytes(rm_c.asm.code))
            if op == vnasm.OP_OVERLAY_FX
        ]
        self.assertEqual(rm_ovl, [(vnasm.VN_OVL_ROOM_MASK, 1), (vnasm.VN_OVL_ROOM_MASK, 0)])

        # 5. pause pause_length in script.rpyc -> pause(4000)
        pauses = [
            struct.unpack("<H", payload)[0]
            for op, payload in by_stem["script"]
            if op == vnasm.OP_PAUSE
        ]
        self.assertIn(4000, pauses)

        # 6. Zero "bare 'show yuri'" or rm/rm2/fake_skip skipped entries
        bad_skips = [
            s for s in compiler.skipped
            if any(k in str(s) for k in ("bare 'show yuri'", "'rm'", "'rm2'", "fake_skip_indicator"))
        ]
        self.assertEqual(bad_skips, [])

    def test_vm_text_style_fake_skip_and_jmp_var_cmp(self) -> None:
        harness = r"""
#include <assert.h>
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
    /* Verify OP_TEXT_STYLE, OP_OVERLAY_FX(VN_OVL_FAKE_SKIP), and OP_JMP_VAR_CMP */
    uint8_t code[64] = {
        OP_TEXT_STYLE, 1,                 /* 0..1 */
        OP_OVERLAY_FX, VN_OVL_FAKE_SKIP, 1, /* 2..4 */
        OP_SET, 0, 5, 0,                  /* 5..8:   vars[0] = 5 */
        OP_SET, 1, 3, 0,                  /* 9..12:  vars[1] = 3 */
        OP_JMP_VAR_CMP, 0, CMP_GT, 1, 27, 0, 0, /* 13..19: jump to 27 if vars[0] > vars[1] */
        OP_SET, 2, 99, 0,                 /* 20..23: skipped */
        OP_END,                           /* 24 */
        OP_NOP, OP_NOP,                   /* 25..26 */
        OP_SET, 2, 42, 0,                 /* 27..30: vars[2] = 42 */
        OP_TEXT_STYLE, 0,                 /* 31..32 */
        OP_OVERLAY_FX, VN_OVL_FAKE_SKIP, 0, /* 33..35 */
        OP_END                            /* 36 */
    };
    vn_vm_t vm;
    vn_init(&vm, code, sizeof(code), &host, 1);

    /* Step first two ops and inspect active scene flags before reset */
    assert(vn_step(&vm));
    assert(vm.scene.text_edited == 1);
    assert(vn_step(&vm));
    assert((vm.scene.overlay_fx_mask & VN_OVL_FAKE_SKIP) != 0);

    assert(vn_run(&vm) == VN_FINISHED);
    assert(vm.vars[2] == 42);
    assert(vm.scene.text_edited == 0);
    assert((vm.scene.overlay_fx_mask & VN_OVL_FAKE_SKIP) == 0);

    /* Now invert values so vars[0] <= vars[1]: branch must fall through */
    vn_init(&vm, code, sizeof(code), &host, 1);
    code[7] = 2; /* vars[0] = 2 */
    assert(vn_run(&vm) == VN_FINISHED);
    assert(vm.vars[2] == 99);
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
