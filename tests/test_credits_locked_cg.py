"""Tests for credits CG lock/unlock tracking (persistent.clear[0..9] & clearall)."""

import ast
import unittest

import compile_script
import vnasm


class CreditsResolver:
    table: dict = {}

    def sprite_layers(self, imgname):
        return (None, None)

    def condswitch_variants(self, imgname):
        return None

    def scene_id(self, imgname):
        if len(imgname) == 1 and imgname[0].startswith("credits_cg"):
            return 100 + int(imgname[0][len("credits_cg"):])
        return None


def make_show(expr_str: str):
    obj = type("Show", (), {})()
    obj.imspec = ((expr_str,), None, None, [], None, None, [])
    obj.atl = None
    obj.linenumber = 1
    return obj


def _run_credits_cg_branch(clearall_val: int, clear_slot_val: int, cg_num: int) -> int:
    c = compile_script.Compiler(resolver=CreditsResolver())
    c.emit_node(make_show(f'("credits_cg{cg_num}" + lockedtext)'), "credits.rpyc")
    c.asm.resolve()
    code = bytes(c.asm.code)
    slots = [0] * 256
    slots[c.variables["persistent.clearall"]] = clearall_val
    slots[c.variables[f"persistent.clear[{cg_num - 1}]"]] = clear_slot_val

    pc = 0
    while pc < len(code):
        op = code[pc]
        if op == vnasm.OP_IF:
            var = code[pc + 1]
            cmp_op = code[pc + 2]
            val = int.from_bytes(code[pc + 3:pc + 5], "little", signed=True)
            tgt = int.from_bytes(code[pc + 5:pc + 8], "little") & 0xFFFF
            if cmp_op == vnasm.CMP_NE and slots[var] != val:
                pc = tgt
                continue
            pc += 8
        elif op == vnasm.OP_SCENE:
            return code[pc + 2]
        elif op == vnasm.OP_JUMP:
            pc = int.from_bytes(code[pc + 1:pc + 4], "little") & 0xFFFF
        else:
            raise AssertionError(f"unexpected opcode {op:#x}")
    raise AssertionError("OP_SCENE not reached")


class CreditsLockedCGTests(unittest.TestCase):
    def test_credits_cg_silhouette_when_locked_and_clear_when_unlocked(self) -> None:
        self.assertEqual(_run_credits_cg_branch(0, 0, 1), vnasm.TRANS_CUT | vnasm.TRANS_SILHOUETTE)
        self.assertEqual(_run_credits_cg_branch(0, 1, 1), vnasm.TRANS_CUT)
        self.assertEqual(_run_credits_cg_branch(1, 0, 1), vnasm.TRANS_CUT)
        self.assertEqual(_run_credits_cg_branch(0, 0, 10), vnasm.TRANS_CUT | vnasm.TRANS_SILHOUETTE)
        self.assertEqual(_run_credits_cg_branch(0, 1, 10), vnasm.TRANS_CUT)

    def test_save_persistent_is_supported_noop(self) -> None:
        c = compile_script.Compiler(resolver=CreditsResolver())
        stmt = ast.parse("renpy.save_persistent()").body[0]
        self.assertTrue(c._emit_python_stmt(stmt))


if __name__ == "__main__":
    unittest.main()
