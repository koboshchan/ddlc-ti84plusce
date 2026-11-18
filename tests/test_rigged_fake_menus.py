"""Tests for rigged_choice, non-interactive display_menu flash, and blank confirm."""

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


class RiggedFakeMenuTests(unittest.TestCase):
    def test_ch21_flash_ch23_rigged_and_blank_confirm_compile(self) -> None:
        c = compile_script.Compiler(resolver=DummyResolver())

        s_flash = ast.parse(
            "renpy.display_menu(items=[('Natsuki.', True), ('Yuri.', True)], interact=False, screen='choice')"
        ).body[0]
        self.assertTrue(c._emit_python_stmt(s_flash))

        s_rigged = ast.parse(
            'madechoice = renpy.display_menu([("Natsuki.", "natsuki"), ("Yuri.", "yuri"), ("Monika.", "monika")], screen="rigged_choice")'
        ).body[0]
        self.assertTrue(c._emit_python_stmt(s_rigged))

        c._emit_confirm_screen("", True, True)

        # Verify madechoice comparison normalizes "monika" -> 2
        op = c._const_operand_for_var("madechoice", ast.parse('"monika"', mode="eval").body)
        self.assertEqual(op, 2)

        code = bytes(c.asm.code)
        self.assertEqual(code[0:4], bytes([vnasm.OP_MENU_SPECIAL, vnasm.VN_MENU_FLASH, 0xFF, 2]))
        # 2 captions -> 4 bytes; next instruction starts at offset 8
        self.assertEqual(
            code[8:12],
            bytes([vnasm.OP_MENU_SPECIAL, vnasm.VN_MENU_RIGGED, c.variables["madechoice"], 3]),
        )
        # 3 captions -> 6 bytes; next instruction starts at offset 18
        self.assertEqual(
            code[18:22],
            bytes([vnasm.OP_MENU_SPECIAL, vnasm.VN_MENU_CONFIRM_OK, c.variables["_return"], 1]),
        )

    def test_vm_executes_special_menu_modes(self) -> None:
        harness = r"""
#include <assert.h>
#include <string.h>
#include <stdint.h>
#include "vn.h"

static const char * const pool[] = { "Natsuki.", "Yuri.", "Monika.", "OK" };
static const char *host_str(void *ctx, uint16_t idx) {
    (void)ctx; return idx < 4 ? pool[idx] : "";
}
static void host_upd(void *ctx, const vn_scene_t *s, uint8_t t) { (void)ctx; (void)s; (void)t; }
static void host_say(void *ctx, const vn_scene_t *s) { (void)ctx; (void)s; }

static uint8_t modes_seen[4];
static uint8_t mode_pos = 0;
static uint8_t host_menu_cb(void *ctx, const vn_scene_t *s, const char *const *o, uint8_t c) {
    (void)ctx; (void)o;
    modes_seen[mode_pos++] = s->menu_mode;
    if (s->menu_mode == VN_MENU_RIGGED) {
        assert(c == 3);
        return 0; /* player fought pull and chose Natsuki */
    }
    return 0;
}
static const vn_host_t host = {
    .string = host_str, .update = host_upd, .say = host_say, .menu = host_menu_cb
};

int main(void) {
    uint8_t code[] = {
        OP_MENU_SPECIAL, VN_MENU_FLASH, 0xFF, 2, 0, 0, 1, 0,
        OP_MENU_SPECIAL, VN_MENU_RIGGED, 0, 3, 0, 0, 1, 0, 2, 0,
        OP_MENU_SPECIAL, VN_MENU_CONFIRM_OK, 1, 1, 3, 0,
        OP_END
    };
    vn_vm_t vm;
    vn_init(&vm, code, sizeof(code), &host, 1);
    vm.vars[0] = 99;
    vm.vars[1] = 0;
    assert(vn_run(&vm) == VN_FINISHED);
    assert(mode_pos == 3);
    assert(modes_seen[0] == VN_MENU_FLASH);
    assert(modes_seen[1] == VN_MENU_RIGGED);
    assert(modes_seen[2] == VN_MENU_CONFIRM_OK);
    assert(vm.scene.menu_mode == VN_MENU_NORMAL);
    assert(vm.vars[0] == 0);
    assert(vm.vars[1] == 1);
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
