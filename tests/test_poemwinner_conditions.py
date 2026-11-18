"""Verify poemwinner[N] assignments and If conditions emit numeric 0/1/2 IDs."""

from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import compile_script
import image_resolve
import vnasm

_TARGET_FILES = (
    "script-ch3",
    "script-ch22",
    "script-ch23",
    "script-exclusives2-yuri",
    "script-poemresponses",
)

# Variable-length instruction walker matching src/vn.c's bytecode encoding.
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
    0x1F: 1,  # OP_NOISE
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


def _scan_bytecode(code: bytes) -> list[tuple[int, tuple[int, ...]]]:
    out = []
    pc = 0
    while pc < len(code):
        op = code[pc]
        pc += 1
        if op == vnasm.OP_CONSOLE:
            subop = code[pc]
            pc += 5 if subop in (vnasm.VN_CONSOLE_SHOW, vnasm.VN_CONSOLE_SHOW_CLR) else 1
        elif op == vnasm.OP_MENU:
            count = code[pc]
            pc += 1 + count * 5
        elif op == vnasm.OP_MENU_SPECIAL:
            count = code[pc + 2]
            pc += 3 + count * 2
        elif op == vnasm.OP_MENU_COND:
            count = code[pc]
            pc += 1
            for _ in range(count):
                cc = code[pc]
                pc += 1 + cc * 4 + 5
        elif op == vnasm.OP_IF:
            var, cmp_op = code[pc], code[pc + 1]
            val = struct.unpack_from("<h", code, pc + 2)[0]
            out.append((op, (var, cmp_op, val)))
            pc += 7
        elif op == vnasm.OP_SET:
            var = code[pc]
            val = struct.unpack_from("<h", code, pc + 1)[0]
            out.append((op, (var, val)))
            pc += 3
        elif op in _FIXED_OPS:
            pc += _FIXED_OPS[op]
        else:
            raise AssertionError(f"Unknown opcode 0x{op:02x} at {pc - 1}")
    return out


class PoemwinnerComparisonTests(unittest.TestCase):
    @unittest.skipUnless((ROOT / "assets/raw/script-ch3.rpyc").is_file(),
                         "extracted .rpyc assets required")
    def test_all_10_if_sites_and_sets_use_enum_ids(self):
        raw_dir = ROOT / "assets/raw"
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            resolver = image_resolve.ImageResolver(raw_dir, build_dir)
            compiler = compile_script.Compiler(
                resolver=resolver,
                transform_animations=compile_script.load_transform_animations(raw_dir),
            )
            compiler.label_params = compile_script.load_label_params(raw_dir, list(_TARGET_FILES))

            chunks = []
            for stem in _TARGET_FILES:
                compiler.asm = vnasm.Assembler(chunk_id=len(chunks))
                for asm in compiler.compile_file_chunked(raw_dir / f"{stem}.rpyc", len(chunks)):
                    chunks.append(bytes(asm.code))

        winner_slots = {
            compiler.variables[f"poemwinner[{i}]"]: i
            for i in range(3)
            if f"poemwinner[{i}]" in compiler.variables
        }
        self.assertTrue(winner_slots)

        if_counts = {0: 0, 1: 0, 2: 0}
        set_hits = []
        for code in chunks:
            for op, args in _scan_bytecode(code):
                if op == vnasm.OP_IF and args[0] in winner_slots:
                    var, _cmp, val = args
                    self.assertIn(val, (0, 1, 2),
                                  f"poemwinner[{winner_slots[var]}] compared against non-enum {val}")
                    if_counts[val] += 1
                elif op == vnasm.OP_SET and args[0] in winner_slots:
                    var, val = args
                    self.assertIn(val, (0, 1, 2),
                                  f"poemwinner[{winner_slots[var]}] assigned non-enum {val}")
                    set_hits.append((winner_slots[var], val))

        # 10 If comparisons across the 5 target files: sayori (1), natsuki (5), yuri (4)
        self.assertGreaterEqual(if_counts[0], 1)
        self.assertGreaterEqual(if_counts[1], 5)
        self.assertGreaterEqual(if_counts[2], 4)
        # script-ch22: poemwinner[1] = "yuri", script-ch23: poemwinner[2] = "yuri"
        self.assertIn((1, 2), set_hits)
        self.assertIn((2, 2), set_hits)


if __name__ == "__main__":
    unittest.main()
