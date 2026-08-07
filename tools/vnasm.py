"""Assembler for the VN bytecode described in docs/FORMAT.md.

Used by gen_demo.py now and by compile_script.py once the asset pipeline
lands. Handles label resolution, backpatching, and the per-chunk string pool.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

# --- opcodes (must stay in sync with src/vn.h) ------------------------------

OP_NOP = 0x00
OP_SAY = 0x01
OP_SCENE = 0x02
OP_SHOW = 0x03
OP_HIDE = 0x04
OP_MENU = 0x05
OP_JUMP = 0x06
OP_CALL = 0x07
OP_RETURN = 0x08
OP_SET = 0x09
OP_IF = 0x0A
OP_PAUSE = 0x0B
OP_SOUND = 0x0C
OP_END = 0x0D
OP_ADD = 0x0E

CMP_EQ, CMP_NE, CMP_LT, CMP_LE, CMP_GT, CMP_GE = range(6)
TRANS_CUT, TRANS_FADE = 0, 1

# OP_SHOW's pos:u8 is half the on-screen center X (decode: center_x = pos*2),
# not a bucketed enum -- see compile_script.py's _pos_from_x. Values below are
# DDLC's own canvas X (transforms.rpy's l11/l21/l22, center/2-character-left/
# 2-character-right) divided by 8 -- the same 0.25 canvas-to-screen scale
# used everywhere else, halved again to fit a byte. Only used as convenient
# presets here and in gen_demo.py; the real pipeline computes a byte per
# scene from the actual transform in use, not from this fixed set.
POS_CENTER = 640 // 8
POS_LEFT = 400 // 8
POS_RIGHT = 880 // 8

SPEAKER_NONE = 0xFF
NO_SPRITE = 0xFF


class AsmError(Exception):
    pass


@dataclass
class Assembler:
    code: bytearray = field(default_factory=bytearray)
    strings: list[str] = field(default_factory=list)
    _pool: dict[str, int] = field(default_factory=dict)
    _labels: dict[str, int] = field(default_factory=dict)
    _patches: list[tuple[int, str]] = field(default_factory=list)

    # --- primitives --------------------------------------------------------

    def _u8(self, v: int) -> None:
        if not 0 <= v <= 0xFF:
            raise AsmError(f"u8 out of range: {v}")
        self.code.append(v)

    def _u16(self, v: int) -> None:
        if not 0 <= v <= 0xFFFF:
            raise AsmError(f"u16 out of range: {v}")
        self.code += struct.pack("<H", v)

    def _i16(self, v: int) -> None:
        if not -0x8000 <= v <= 0x7FFF:
            raise AsmError(f"i16 out of range: {v}")
        self.code += struct.pack("<h", v)

    def _u24(self, v: int) -> None:
        if not 0 <= v <= 0xFFFFFF:
            raise AsmError(f"u24 out of range: {v}")
        self.code += struct.pack("<I", v)[:3]

    def _ref(self, label: str) -> None:
        """Emit a u24 placeholder to be backpatched by resolve()."""
        self._patches.append((len(self.code), label))
        self.code += b"\x00\x00\x00"

    def string(self, text: str) -> int:
        """Intern @text in the pool and return its index."""
        if text not in self._pool:
            self._pool[text] = len(self.strings)
            self.strings.append(text)
        return self._pool[text]

    # --- labels ------------------------------------------------------------

    def label(self, name: str) -> None:
        if name in self._labels:
            raise AsmError(f"duplicate label: {name}")
        self._labels[name] = len(self.code)

    def patch_missing_labels(self) -> list[str]:
        """Point every referenced-but-undefined label at a shared OP_END stub.

        Used when compiling a subset of a larger game: a Call/Jump to a label
        that lives in a file outside the compiled set would otherwise make
        resolve() raise. Landing on OP_END there is a deliberate, visible
        "this content wasn't imported" stop rather than undefined behavior.
        Returns the sorted list of missing label names, for logging.
        """
        missing = sorted({name for _, name in self._patches if name not in self._labels})
        if not missing:
            return missing

        stub = len(self.code)
        self.end()
        for name in missing:
            self._labels[name] = stub
        return missing

    def resolve(self) -> None:
        for offset, name in self._patches:
            if name not in self._labels:
                raise AsmError(f"undefined label: {name}")
            target = self._labels[name]
            self.code[offset:offset + 3] = struct.pack("<I", target)[:3]
        self._patches.clear()

    # --- instructions ------------------------------------------------------

    def nop(self) -> None:
        self._u8(OP_NOP)

    def say(self, speaker: int, text: str) -> None:
        self._u8(OP_SAY)
        self._u8(speaker)
        self._u16(self.string(text))

    def narrate(self, text: str) -> None:
        self.say(SPEAKER_NONE, text)

    def scene(self, bg: int, trans: int = TRANS_CUT) -> None:
        self._u8(OP_SCENE)
        self._u8(bg)
        self._u8(trans)

    def show(self, char: int, sprite: int, pos: int = POS_CENTER) -> None:
        self._u8(OP_SHOW)
        self._u8(char)
        self._u8(sprite)
        self._u8(pos)

    def hide(self, char: int) -> None:
        self._u8(OP_HIDE)
        self._u8(char)

    def menu(self, options: list[tuple[str, str]]) -> None:
        """@options is a list of (choice text, target label)."""
        self._u8(OP_MENU)
        self._u8(len(options))
        for text, target in options:
            self._u16(self.string(text))
            self._ref(target)

    def jump(self, label: str) -> None:
        self._u8(OP_JUMP)
        self._ref(label)

    def call(self, label: str) -> None:
        self._u8(OP_CALL)
        self._ref(label)

    def ret(self) -> None:
        self._u8(OP_RETURN)

    def set(self, var: int, value: int) -> None:
        self._u8(OP_SET)
        self._u8(var)
        self._i16(value)

    def add(self, var: int, delta: int) -> None:
        self._u8(OP_ADD)
        self._u8(var)
        self._i16(delta)

    def if_(self, var: int, cmp: int, value: int, label: str) -> None:
        """Jump to @label when the condition holds."""
        self._u8(OP_IF)
        self._u8(var)
        self._u8(cmp)
        self._i16(value)
        self._ref(label)

    def pause(self, frames: int) -> None:
        self._u8(OP_PAUSE)
        self._u8(frames)

    def sound(self, id_: int = 0) -> None:
        """Always a no-op on-calc (no CE audio) -- reserved so audio-control
        script lines still consume a real opcode slot instead of vanishing."""
        self._u8(OP_SOUND)
        self._u8(id_)

    def end(self) -> None:
        self._u8(OP_END)

    # --- output ------------------------------------------------------------

    def to_chunk_bytes(self) -> bytes:
        """Serialize to the on-appvar chunk container: code + string pool.

        Layout (all lengths little-endian):
            u16 code_length
            <code_length bytes of bytecode>
            u16 string_count
            per string, in pool order (matches OP_SAY/OP_MENU's u16 index):
                u16 byte_length
                <byte_length bytes of UTF-8 text>
                1 trailing NUL byte (not counted in byte_length)

        The trailing NUL lets src/assets.c point vn_host_t.string() directly
        at the string's bytes inside the (archived, directly-addressable)
        AppVar instead of copying it to a NUL-terminated scratch buffer --
        the rest of the engine already expects a plain `const char *`.

        This is what actually gets zx0-compressed and packed into a TI
        AppVar; docs/FORMAT.md's "Chunking" section documents this layout.
        """
        self.resolve()
        if len(self.code) > 0xFFFF:
            raise AsmError(f"chunk code exceeds u16 length: {len(self.code)} bytes")

        out = bytearray()
        out += struct.pack("<H", len(self.code))
        out += self.code
        out += struct.pack("<H", len(self.strings))
        for s in self.strings:
            encoded = s.encode("utf-8")
            if len(encoded) > 0xFFFF:
                raise AsmError(f"string exceeds u16 length: {len(encoded)} bytes: {s[:40]!r}")
            out += struct.pack("<H", len(encoded))
            out += encoded
            out += b"\x00"
        return bytes(out)

    def to_c(self, name: str, header: str) -> str:
        """Render the assembled chunk as a C source file."""
        self.resolve()

        lines = [
            "/*",
            f" * {header}",
            " *",
            " * GENERATED by tools/gen_demo.py -- do not edit by hand.",
            " */",
            "",
            f'#include "{name}.h"',
            "",
            f"const uint8_t {name}_code[{len(self.code)}] = {{",
        ]

        for i in range(0, len(self.code), 12):
            row = ", ".join(f"0x{b:02X}" for b in self.code[i:i + 12])
            lines.append(f"    {row},")

        lines += [
            "};",
            "",
            f"const char *const {name}_strings[{len(self.strings)}] = {{",
        ]
        for s in self.strings:
            lines.append(f"    {c_quote(s)},")
        lines += [
            "};",
            "",
            f"const unsigned {name}_code_size = {len(self.code)};",
            f"const unsigned {name}_string_count = {len(self.strings)};",
            "",
        ]
        return "\n".join(lines)


def c_quote(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif 0x20 <= ord(ch) < 0x7F:
            out.append(ch)
        else:
            out.append(f"\\x{ord(ch):02x}")
    out.append('"')
    return "".join(out)
