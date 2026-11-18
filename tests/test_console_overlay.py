import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import compile_script
import vnasm


class _StubResolver:
    table = {}

    def condswitch_variants(self, imgname):
        return None

    def sprite_layers(self, imgname):
        return (0, vnasm.NO_OVERLAY)

    def scene_id(self, imgname):
        return 1


class TestConsoleOverlay(unittest.TestCase):
    def test_ch23_and_ch30_emit_console_ops(self) -> None:
        raw_dir = Path(__file__).resolve().parent.parent / "assets" / "raw"
        compiler = compile_script.Compiler(_StubResolver())
        chunks = {}
        for name in ("script-ch23", "script-ch30"):
            compiler.asm = compiler.new_assembler()
            chunks[name] = compiler.compile_file_chunked(raw_dir / f"{name}.rpyc", compiler.asm.chunk_id)

        def collect_console(asms):
            cmds = []
            for a in asms:
                code = bytes(a.code)
                i = 0
                # Walk strings via chunk string table instead of decoding every opcode length:
                for s in a.strings:
                    if ("chr" in s or "deleted successfully" in s or "does not exist" in s) and (
                        "os.remove" in s or "renpy.file" in s or "chr" in s
                    ):
                        cmds.append(s)
                if vnasm.OP_CONSOLE in code:
                    pass
            return cmds

        ch23_asms = chunks["script-ch23"]
        ch30_asms = chunks["script-ch30"]
        self.assertTrue(any(vnasm.OP_CONSOLE in bytes(a.code) for a in ch23_asms))
        self.assertTrue(any(vnasm.OP_CONSOLE in bytes(a.code) for a in ch30_asms))
        ch23_strs = collect_console(ch23_asms)
        ch30_strs = collect_console(ch30_asms)
        self.assertTrue(any("characters/yuri.chr" in s for s in ch23_strs))
        self.assertTrue(any("characters/natsuki.chr" in s for s in ch23_strs))
        self.assertTrue(any("monika.chr does not exist." in s for s in ch30_strs))

    def test_assembler_console_encoding(self) -> None:
        a = vnasm.Assembler()
        a.console(vnasm.VN_CONSOLE_SHOW, "os.remove(\"yuri.chr\")", "yuri.chr deleted.")
        a.console(vnasm.VN_CONSOLE_HIDE)
        a.console(vnasm.VN_CONSOLE_CLEAR)
        code = bytes(a.code)
        # OP_CONSOLE(0) + u8 + u16 + u16 = 6, + HIDE(2) = 2, + CLEAR(2) = 2 => 10 bytes
        self.assertEqual(len(code), 10)
        self.assertEqual(code[0], vnasm.OP_CONSOLE)
        self.assertEqual(code[1], vnasm.VN_CONSOLE_SHOW)
        self.assertEqual(code[6], vnasm.OP_CONSOLE)
        self.assertEqual(code[7], vnasm.VN_CONSOLE_HIDE)
        self.assertEqual(code[8], vnasm.OP_CONSOLE)
        self.assertEqual(code[9], vnasm.VN_CONSOLE_CLEAR)


if __name__ == "__main__":
    unittest.main()
