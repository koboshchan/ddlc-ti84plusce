"""Tests for character transform flags (dip, leftin, instant, thide, lhide)."""

import pickle
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

import compile_script
import vnasm

ROOT = Path(__file__).resolve().parent.parent
DDLC_ZIP = Path("/Users/lucaszhang/Downloads/ddlc-win.zip")


class DummyResolver:
    table: dict = {}

    def sprite_layers(self, imgname):
        return (1, vnasm.NO_OVERLAY)


def make_compiler() -> compile_script.Compiler:
    return compile_script.Compiler(
        resolver=DummyResolver(),
        transform_animations={
            "f11": (640, compile_script.VN_FLAG_ZOOM),
            "h11": (640, compile_script.VN_FLAG_HOP),
            "s11": (640, compile_script.VN_FLAG_SINK),
            "d11": (640, compile_script.VN_FLAG_DIP),
            "l31": (240, compile_script.VN_FLAG_LEFTIN),
            "i11": (640, compile_script.VN_FLAG_INSTANT),
        },
    )


def make_node(cls_name: str, **attrs):
    obj = type(cls_name, (), {})()
    for k, v in attrs.items():
        setattr(obj, k, v)
    return obj


def show_node(tag: str, *attrs: str, at_list=()):
    return make_node(
        "Show",
        imspec=((tag,) + attrs, None, None, list(at_list), None, None, []),
        atl=None,
        linenumber=1,
    )


def hide_node(tag: str):
    return make_node(
        "Hide",
        imspec=((tag,), None, None, [], None, None, []),
        linenumber=2,
    )


def say_node(who: str, what: str, attributes=()):
    return make_node(
        "Say",
        who=who,
        what=what,
        attributes=list(attributes),
        linenumber=3,
    )


class TransformFlagsTests(unittest.TestCase):
    def test_dip_leftin_instant_and_oneshot_clearing(self) -> None:
        c = make_compiler()
        c.emit_node(show_node("sayori", "1a", at_list=["d11"]), "ch0")
        c.emit_node(say_node("s", "Dipping!", attributes=["1b"]), "ch0")
        c.emit_node(show_node("natsuki", "1a", at_list=["l31"]), "ch0")
        c.emit_node(show_node("yuri", "1a", at_list=["i11"]), "ch0")

        code = bytes(c.asm.code)
        # Verify OP_SHOW opcodes and flags: OP_SHOW is 8 bytes
        # [0]: OP_SHOW(0x03), ch:u8, sprite:u16, overlay:u16, pos:u8, flags:u8
        shows = []
        i = 0
        while i < len(code):
            op = code[i]
            if op == vnasm.OP_SHOW:
                shows.append((code[i + 1], code[i + 6], code[i + 7]))
                i += 8
            elif op == vnasm.OP_SAY:
                i += 4
            else:
                self.fail(f"unexpected opcode {op:#x}")

        self.assertEqual(
            shows,
            [
                (0, 640 // 8, vnasm.VN_FLAG_DIP),
                (0, 640 // 8, 0),  # VN_FLAG_DIP must not persist onto next Say
                (1, 240 // 8, vnasm.VN_FLAG_LEFTIN),
                (2, 640 // 8, vnasm.VN_FLAG_INSTANT),
            ],
        )

    def test_thide_skips_show_and_lhide_marks_hide(self) -> None:
        c = make_compiler()
        c.emit_node(show_node("sayori", "1a", at_list=["l31"]), "ch0")
        c.emit_node(show_node("sayori", at_list=["lhide"]), "ch0")
        c.emit_node(hide_node("sayori"), "ch0")

        c.emit_node(show_node("monika", "1a", at_list=["f11"]), "ch0")
        c.emit_node(show_node("monika", at_list=["thide"]), "ch0")
        c.emit_node(hide_node("monika"), "ch0")

        code = bytes(c.asm.code)
        ops = []
        i = 0
        while i < len(code):
            op = code[i]
            if op == vnasm.OP_SHOW:
                ops.append(("SHOW", code[i + 1], code[i + 7]))
                i += 8
            elif op == vnasm.OP_HIDE:
                ops.append(("HIDE", code[i + 1]))
                i += 2
            else:
                self.fail(f"unexpected opcode {op:#x}")

        self.assertEqual(
            ops,
            [
                ("SHOW", 0, vnasm.VN_FLAG_LEFTIN),
                ("SHOW", 0, vnasm.VN_FLAG_LHIDE),
                ("HIDE", 0),
                ("SHOW", 3, vnasm.VN_FLAG_ZOOM),
                ("HIDE", 3),
            ],
        )

    def test_load_transform_animations_has_dip_and_leftin(self) -> None:
        if not DDLC_ZIP.exists():
            self.skipTest(f"{DDLC_ZIP} not present")
        with zipfile.ZipFile(DDLC_ZIP) as zf:
            data = zf.read("DDLC-1.1.1-pc/game/scripts.rpa")
        hdr = data[:data.index(b"\n")].decode().split()
        offset, key = int(hdr[1], 16), int(hdr[2], 16)
        index = pickle.loads(zlib.decompress(data[offset:]))
        e = index["transforms.rpyc"][0]
        off, dlen = e[0] ^ key, e[1] ^ key
        prefix = e[2] if len(e) > 2 and e[2] else b""
        if isinstance(prefix, str):
            prefix = prefix.encode("latin1")
        with tempfile.TemporaryDirectory() as td:
            raw_dir = Path(td)
            (raw_dir / "transforms.rpyc").write_bytes(prefix + data[off:off + dlen])
            anims = compile_script.load_transform_animations(raw_dir)
        self.assertEqual(anims.get("d11"), (640, vnasm.VN_FLAG_DIP))
        self.assertEqual(anims.get("l31"), (240, vnasm.VN_FLAG_LEFTIN))
        self.assertEqual(anims.get("i11"), (640, vnasm.VN_FLAG_INSTANT))


if __name__ == "__main__":
    unittest.main()
