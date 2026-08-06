"""Compile a flattened Ren'Py AST into VM bytecode via tools/vnasm.py.

Unlike rpyc_ast.flatten() (a flat scan, used for inspection/image-table
building), this module is a recursive-descent emitter: control flow (Label,
If, Menu) needs real branching bytecode, not a pre-order dump, so it walks
each node's nested .block/.entries/.items directly.

Unsupported constructs degrade instead of failing the build: an unrecognized
If condition takes its branch unconditionally, an unrecognized $ statement or
UserStatement becomes OP_NOP, and a Jump/Call to a label outside the compiled
file set lands on a shared OP_END stub (vnasm.Assembler.patch_missing_labels).
Every skip is logged so a run is auditable, not silently lossy. The one hard
failure is variable-slot overflow (VN_MAX_VARS=64) -- a wrong slot means a
wrong branch taken at runtime, a correctness bug, not a cosmetic gap.
"""

from __future__ import annotations

import ast
import itertools
from dataclasses import dataclass, field
from pathlib import Path

import vnasm
from rpyc_ast import kind, load_rpyc, pycode_source

# Speaker codes are stable across the whole game (renpy.ast.Define confirms
# s/n/y/m = DynamicCharacter(image='sayori'|'natsuki'|'yuri'|'monika')).
# Character ids match src/main.c's render.c placeholder order.
TAG_TO_CHAR = {"sayori": 0, "natsuki": 1, "yuri": 2, "monika": 3}
CODE_TO_TAG = {"s": "sayori", "n": "natsuki", "y": "yuri", "m": "monika"}

_CMP_MAP = {
    ast.Eq: vnasm.CMP_EQ, ast.NotEq: vnasm.CMP_NE,
    ast.Lt: vnasm.CMP_LT, ast.LtE: vnasm.CMP_LE,
    ast.Gt: vnasm.CMP_GT, ast.GtE: vnasm.CMP_GE,
}

_AUDIO_PREFIXES = ("play ", "stop ", "queue ", "voice ")

VN_MAX_VARS = 64


class CompileError(Exception):
    """A hard failure -- unlike a logged skip, this aborts the build."""


@dataclass
class SkipEntry:
    file: str
    line: int
    kind: str
    reason: str


@dataclass
class Compiler:
    resolver: "image_resolve.ImageResolver"
    asm: vnasm.Assembler = field(default_factory=vnasm.Assembler)
    variables: dict = field(default_factory=dict)         # name -> slot
    last_sprite: dict = field(default_factory=dict)        # char id -> sprite id
    skipped: list = field(default_factory=list)            # [SkipEntry]
    stats: dict = field(default_factory=dict)               # kind -> count
    _pending_scene: int | None = field(default=None, init=False)
    _gensym_counter: itertools.count = field(default_factory=itertools.count, init=False)

    # -- driver ---------------------------------------------------------------

    def compile_file(self, path: Path) -> None:
        _, top = load_rpyc(path)
        self.emit_block(top, path.name)
        self._flush_pending_scene(vnasm.TRANS_CUT)

    def finish(self) -> list[str]:
        """Resolve labels, patching any cross-file target left dangling.

        Returns the (sorted) list of label names that had to be stubbed --
        i.e. content that jumps/calls somewhere outside the compiled set.
        """
        missing = self.asm.patch_missing_labels()
        self.asm.resolve()
        return missing

    # -- bookkeeping ------------------------------------------------------------

    def _gensym(self, prefix: str) -> str:
        return f"__{prefix}_{next(self._gensym_counter)}"

    def _count(self, k: str) -> None:
        self.stats[k] = self.stats.get(k, 0) + 1

    def _skip(self, node, fname: str, reason: str) -> None:
        self.skipped.append(SkipEntry(fname, getattr(node, "linenumber", 0), kind(node), reason))
        self._count(f"skip:{kind(node)}")

    def _var_slot(self, name: str) -> int:
        if name in self.variables:
            return self.variables[name]
        if len(self.variables) >= VN_MAX_VARS:
            raise CompileError(
                f"variable slot overflow: no room for {name!r} "
                f"(VN_MAX_VARS={VN_MAX_VARS} already used: {sorted(self.variables)})")
        slot = len(self.variables)
        self.variables[name] = slot
        return slot

    # -- scene buffering (so `scene X \n with dissolve` gets the real trans) ---

    def _flush_pending_scene(self, trans: int) -> None:
        if self._pending_scene is not None:
            self.asm.scene(self._pending_scene, trans)
            self._pending_scene = None

    # -- block emission ---------------------------------------------------------

    def emit_block(self, nodes, fname: str) -> None:
        for node in nodes or []:
            self.emit_node(node, fname)

    def emit_node(self, node, fname: str) -> None:
        k = kind(node)
        method = getattr(self, f"_emit_{k}", None)
        if method is not None:
            method(node, fname)
            self._count(k)
            return

        if k in _DECLARATION_KINDS or k == "Pass":
            return  # not part of the runtime instruction stream; nothing to emit

        self._skip(node, fname, f"unhandled node kind {k}")
        self._flush_pending_scene(vnasm.TRANS_CUT)
        self.asm.nop()

    # -- per-kind emitters --------------------------------------------------------

    def _emit_Label(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        name = node.name
        if not isinstance(name, str):
            self._skip(node, fname, "non-literal label name")
            return
        self.asm.label(name)
        self.emit_block(node.block, fname)

    def _emit_Say(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        who = node.who
        text = node.what
        if not isinstance(text, str):
            self._skip(node, fname, "non-literal Say.what")
            return
        speaker = vnasm.SPEAKER_NONE
        if isinstance(who, str):
            tag = CODE_TO_TAG.get(who)
            char = TAG_TO_CHAR.get(tag) if tag else None
            if char is not None:
                speaker = char
        self.asm.say(speaker, text)

    def _emit_Show(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        imgname = tuple(node.imspec[0]) if node.imspec and node.imspec[0] else None
        if not imgname:
            self._skip(node, fname, "Show with empty imspec")
            return

        char = TAG_TO_CHAR.get(imgname[0])
        if char is None:
            self._skip(node, fname, f"unknown character tag in {imgname!r}")
            return

        if len(imgname) == 1:
            # Bare "show natsuki": keep whatever this character is currently
            # wearing. Confirmed there's no Image def for a bare character
            # name to fall back on, so this only works if we've already
            # shown them earlier in this same linear pass.
            sprite = self.last_sprite.get(char)
            if sprite is None:
                self._skip(node, fname, f"bare 'show {imgname[0]}' with no prior sprite tracked; defaulted to 0")
                sprite = 0
        else:
            sprite = self.resolver.sprite_id(imgname)
            if sprite is None:
                self._skip(node, fname, f"unresolved sprite {imgname!r}")
                return
            self.last_sprite[char] = sprite

        # Position: DDLC positions shown poses via custom named ATL
        # transforms (transforms.rpy: 't31', 'f22', ...), not the simple
        # built-in 'left'/'right'/'truecenter' keywords -- resolving those
        # to real coordinates needs ATL property parsing this pass doesn't
        # implement, so position always defaults to POS_CENTER for now.
        self.asm.show(char, sprite, vnasm.POS_CENTER)

    def _emit_Hide(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        imgname = node.imspec[0] if node.imspec else None
        char = TAG_TO_CHAR.get(imgname[0]) if imgname else None
        if char is None:
            self._skip(node, fname, f"unknown character tag in Hide {imgname!r}")
            return
        self.asm.hide(char)

    def _emit_Scene(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        imgname = tuple(node.imspec[0]) if node.imspec and node.imspec[0] else None
        if not imgname:
            self._skip(node, fname, "Scene with empty imspec")
            return
        scene_id = self.resolver.scene_id(imgname)
        if scene_id is None:
            self._skip(node, fname, f"unresolved scene image {imgname!r}")
            return
        self._pending_scene = scene_id
        self.last_sprite.clear()  # OP_SCENE clears all actors in the VM too

    def _emit_With(self, node, fname: str) -> None:
        expr = node.expr if isinstance(node.expr, str) else ""
        trans = vnasm.TRANS_FADE if "dissolve" in expr else vnasm.TRANS_CUT
        self._flush_pending_scene(trans)

    def _emit_Jump(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        if getattr(node, "expression", False) or not isinstance(node.target, str):
            self._skip(node, fname, "dynamic jump target unsupported")
            self.asm.nop()
            return
        self.asm.jump(node.target)

    def _emit_Call(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        if getattr(node, "expression", False) or not isinstance(node.label, str):
            self._skip(node, fname, "dynamic call target unsupported")
            self.asm.nop()
            return
        self.asm.call(node.label)

    def _emit_Return(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        self.asm.ret()

    def _emit_UserStatement(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        line = node.line if isinstance(node.line, str) else ""
        if line.strip().startswith(_AUDIO_PREFIXES):
            self.asm.sound(0)
            return
        self._skip(node, fname, f"unsupported statement: {line!r}")
        self.asm.nop()

    def _emit_Python(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        src = pycode_source(node.code) if getattr(node, "code", None) else None
        if not src:
            self._skip(node, fname, "Python node with no captured source")
            self.asm.nop()
            return
        try:
            module = ast.parse(src, mode="exec")
        except SyntaxError as e:
            self._skip(node, fname, f"unparseable python block: {e}")
            self.asm.nop()
            return

        any_emitted = False
        for stmt in module.body:
            if self._emit_python_stmt(stmt):
                any_emitted = True
            else:
                self._skip(node, fname, f"unsupported python statement: {ast.dump(stmt)[:80]}")
        if not any_emitted:
            self.asm.nop()

    def _emit_python_stmt(self, stmt) -> bool:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            var = _ident_name(stmt.targets[0])
            val = _const_int(stmt.value)
            if var is not None and val is not None:
                self.asm.set(self._var_slot(var), val)
                return True
        if isinstance(stmt, ast.AugAssign) and isinstance(stmt.op, (ast.Add, ast.Sub)):
            var = _ident_name(stmt.target)
            val = _const_int(stmt.value)
            if var is not None and val is not None:
                delta = val if isinstance(stmt.op, ast.Add) else -val
                self.asm.add(self._var_slot(var), delta)
                return True
        return False

    def _emit_If(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        entries = node.entries or []
        if not entries:
            return

        end_label = self._gensym("if_end")
        conditional: list[tuple[int, int, int, str, list]] = []
        else_block = None

        for cond_str, block in entries:
            if isinstance(cond_str, str) and cond_str.strip() == "True":
                else_block = block
                break

            parsed = _parse_condition(cond_str) if isinstance(cond_str, str) else None
            if parsed is None:
                # Documented degrade: unsupported condition's branch is taken
                # unconditionally, and later entries in this chain (which can
                # only be reached if this one were false) become unreachable.
                self._skip(node, fname,
                          f"unsupported If condition {cond_str!r}; took its branch unconditionally")
                else_block = block
                break

            var, cmp, val = parsed
            slot = self._var_slot(var)
            branch_label = self._gensym("if_branch")
            conditional.append((slot, cmp, val, branch_label, block))

        for slot, cmp, val, branch_label, _ in conditional:
            self.asm.if_(slot, cmp, val, branch_label)

        if else_block is not None:
            self.emit_block(else_block, fname)
        if conditional:
            self.asm.jump(end_label)

        for slot, cmp, val, branch_label, block in conditional:
            self.asm.label(branch_label)
            self.emit_block(block, fname)
            self.asm.jump(end_label)

        self.asm.label(end_label)

    def _emit_Menu(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        rows = []
        for item in node.items or []:
            if len(item) == 3:
                caption, _condition, block = item
            elif len(item) == 2:
                caption, block = item
            else:
                continue
            if isinstance(caption, str) and block:
                rows.append((caption, block))

        if not rows:
            self._skip(node, fname, "Menu with no selectable items")
            return

        end_label = self._gensym("menu_end")
        branch_labels = [self._gensym("menu_opt") for _ in rows]
        self.asm.menu([(caption, lbl) for (caption, _), lbl in zip(rows, branch_labels)])

        for (_, block), lbl in zip(rows, branch_labels):
            self.asm.label(lbl)
            self.emit_block(block, fname)
            self.asm.jump(end_label)
        self.asm.label(end_label)


_DECLARATION_KINDS = {
    "Image", "Define", "Default", "Init", "EarlyPython", "Style", "Transform",
    "Screen", "ArgumentInfo", "ParameterInfo", "PyCode", "PyExprStr",
}


# --- structural expression matching (never eval()/exec()) --------------------

def _ident_name(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _ident_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _const_int(node) -> int | None:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _const_int(node.operand)
        return -inner if inner is not None else None
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        v = int(node.value)
        return v if -32768 <= v <= 32767 else None
    return None


def _parse_condition(cond_str: str) -> tuple[str, int, int] | None:
    """Match `IDENT <cmp> CONST` / `attr.chain <cmp> CONST`. None if not that shape."""
    try:
        expr = ast.parse(cond_str, mode="eval").body
    except SyntaxError:
        return None
    if not isinstance(expr, ast.Compare) or len(expr.ops) != 1 or len(expr.comparators) != 1:
        return None
    cmp = _CMP_MAP.get(type(expr.ops[0]))
    if cmp is None:
        return None
    var = _ident_name(expr.left)
    val = _const_int(expr.comparators[0])
    if var is None or val is None:
        return None
    return var, cmp, val
