"""Compiler regressions for the launch-only early-deletion flag."""
import ast
import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import vnasm
from compile_script import Compiler, CompileError, VN_MAX_VARS, _match_playthrough_char_flag_check

CHECK = """if persistent.playthrough == 0:
    try: renpy.file("../characters/sayori.chr")
    except: s_kill_early = True
"""


def execute(compiler, values, missing):
    """Execute only this small emitted guard, including real packed targets."""
    compiler.asm.resolve()
    code = compiler.asm.code
    slots = [0] * VN_MAX_VARS
    for name, value in values.items():
        slots[compiler.variables[name]] = value
    pc = probes = 0
    while pc < len(code):
        op = code[pc]
        if op == vnasm.OP_SET:
            slots[code[pc + 1]] = struct.unpack_from("<h", code, pc + 2)[0]
            pc += 4
        elif op == vnasm.OP_IF:
            var, cmp = code[pc + 1:pc + 3]
            value = struct.unpack_from("<h", code, pc + 3)[0]
            target = int.from_bytes(code[pc + 5:pc + 8], "little")
            assert cmp == vnasm.CMP_NE
            pc = target if slots[var] != value else pc + 8
        elif op == vnasm.OP_JUMP:
            pc = int.from_bytes(code[pc + 1:pc + 4], "little")
        elif op == vnasm.OP_CHAR_CHECK:
            probes += 1
            assert code[pc + 1] == 0
            target = int.from_bytes(code[pc + 2:pc + 5], "little")
            pc = target if missing else pc + 5
        else:
            raise AssertionError(f"unexpected opcode {op} at {pc}")
    return slots, probes


class PersistCompilerTests(unittest.TestCase):
    def compiler(self, source):
        compiler = Compiler(resolver=SimpleNamespace())
        for stmt in ast.parse(source).body:
            self.assertTrue(compiler._emit_python_stmt(stmt))
        return compiler

    def test_flag_is_independent_and_nonpersistent(self):
        compiler = self.compiler("s_kill_early = None\n" + CHECK)
        self.assertEqual(compiler.variables["s_kill_early"], VN_MAX_VARS - 1)
        self.assertEqual(compiler.variables["persistent.deleted_sayori"], 5)
        self.assertFalse("s_kill_early".startswith("persistent."))

    def test_reset_does_not_resurrect_sayori(self):
        compiler = self.compiler("s_kill_early = None\n" + CHECK)
        values, probes = execute(compiler, {
            "s_kill_early": 1, "persistent.playthrough": 1,
            "persistent.deleted_sayori": 1}, True)
        self.assertEqual(values[5], 1)
        self.assertEqual(values[255], 0)
        self.assertEqual(probes, 0)

    def test_act1_missing_character_triggers(self):
        compiler = self.compiler("s_kill_early = None\n" + CHECK)
        values, probes = execute(compiler, {"persistent.playthrough": 0}, True)
        self.assertEqual(values[255], 1)
        self.assertEqual(values[5], 0)
        self.assertEqual(probes, 1)

    def test_act1_present_character_does_not_trigger(self):
        compiler = self.compiler("s_kill_early = None\n" + CHECK)
        values, probes = execute(compiler, {
            "s_kill_early": 1, "persistent.playthrough": 0}, False)
        self.assertEqual(values[255], 0)
        self.assertEqual(probes, 1)

    def test_later_playthroughs_never_probe(self):
        for act in (1, 2, 3, 4, -1):
            for missing in (False, True):
                with self.subTest(act=act, missing=missing):
                    compiler = self.compiler("s_kill_early = None\n" + CHECK)
                    values, probes = execute(compiler, {
                        "s_kill_early": 1, "persistent.playthrough": act,
                        "persistent.deleted_sayori": 1}, missing)
                    self.assertEqual((values[255], values[5], probes), (0, 1, 0))

    def test_wrapped_guard_preserves_already_set_flag(self):
        source = "if not s_kill_early:\n" + "\n".join(
            "    " + line for line in CHECK.splitlines())
        for flag in (0, 1):
            compiler = self.compiler(source)
            values, probes = execute(compiler, {
                "s_kill_early": flag, "persistent.playthrough": 0}, True)
            self.assertEqual(values[255], 1)
            self.assertEqual(probes, 1 - flag)

    def test_unrelated_wrapper_is_not_recognized(self):
        source = "if not other_flag:\n" + "\n".join(
            "    " + line for line in CHECK.splitlines())
        self.assertIsNone(_match_playthrough_char_flag_check(ast.parse(source).body[0]))

    def test_wrong_playthrough_comparison_is_not_recognized(self):
        for condition in ("!= 0", "== 1", "<= 0"):
            with self.subTest(condition=condition):
                self.assertIsNone(_match_playthrough_char_flag_check(
                    ast.parse(CHECK.replace("== 0", condition)).body[0]))

    def test_flag_allocation_does_not_move_later_variables(self):
        old = Compiler(resolver=SimpleNamespace())
        new = Compiler(resolver=SimpleNamespace())
        for n in range(80):
            self.assertEqual(old._var_slot(f"v{n}"), new._var_slot(f"v{n}"))
        new._var_slot("s_kill_early")
        for n in range(80, 200):
            self.assertEqual(old._var_slot(f"v{n}"), new._var_slot(f"v{n}"))
        self.assertEqual({k: v for k, v in new.variables.items()
                          if k != "s_kill_early"}, old.variables)

    def test_tail_reservation_and_overflow(self):
        compiler = Compiler(resolver=SimpleNamespace())
        compiler._var_slot("s_kill_early")
        for n in range(255 - 15):
            self.assertEqual(compiler._var_slot(f"v{n}"), n + 15)
        self.assertEqual(len(compiler.variables), 256)
        with self.assertRaises(CompileError):
            compiler._var_slot("overflow")
        self.assertEqual(compiler._var_slot("s_kill_early"), 255)

    def test_tail_is_reserved_even_before_flag_is_seen(self):
        compiler = Compiler(resolver=SimpleNamespace())
        for n in range(255 - 15):
            compiler._var_slot(f"v{n}")
        with self.assertRaises(CompileError):
            compiler._var_slot("overflow")
        self.assertEqual(compiler._var_slot("s_kill_early"), 255)


if __name__ == "__main__":
    unittest.main()
