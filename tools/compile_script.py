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
import re
from dataclasses import dataclass, field
from pathlib import Path

import vnasm
from rpyc_ast import kind, load_rpyc, pycode_source

# Speaker codes are stable across the whole game (renpy.ast.Define confirms
# s/n/y/m = DynamicCharacter(image='sayori'|'natsuki'|'yuri'|'monika')).
# Character ids match src/main.c's render.c placeholder order.
TAG_TO_CHAR = {"sayori": 0, "natsuki": 1, "yuri": 2, "monika": 3}
CODE_TO_TAG = {"s": "sayori", "n": "natsuki", "y": "yuri", "m": "monika"}

# Ren'Py inline text tags (`{i}...{/i}`, `{cps=30}`, `{w=0.5}`, `{nw}`, ...)
# show up literally in Say.what/Menu captions -- e.g. ch0 alone has 4 "{i}"
# italics pairs. The engine's text renderer (text.c/render.c) has no notion
# of markup, italics, per-span color, or a variable typing speed, so an
# unstripped tag would show up as literal brace characters in the dialogue
# box instead of doing anything. Ren'Py's own escape for a literal brace is
# doubled ("{{"), which this preserves; every other "{...}" is a tag and is
# dropped rather than interpreted, since there's nothing here that could act
# on cps/wait/color even if it were parsed out.
_TAG_RE = re.compile(r"\{\{|\{[^{}]*\}")


def _strip_text_tags(text: str) -> str:
    return _TAG_RE.sub(lambda m: "{" if m.group(0) == "{{" else "", text)


# DDLC positions characters via named ATL transforms (transforms.rpy) rather
# than the simple built-in left/right/truecenter keywords. Each one turns out
# to be a single call like `tcommon(640)` / `focus(880)` / `sink(240)` --
# different visual effects (appear, focus-zoom, sink-in, ...) that all take
# the same thing: an absolute X position on Ren'Py's 1280-wide canvas. This
# regex pulls that X back out without needing to know what the effect does.
_POS_EXPR_RE = re.compile(r"^\w+\((\d+)\)$")


def load_transform_positions(raw_dir: Path) -> dict[str, int]:
    """Maps each position-carrying transform's name (e.g. "t21") to its X
    coordinate in Ren'Py's 1280-wide canvas, by reading transforms.rpyc.
    Transforms that aren't a single `func(N)` call (e.g. "thide", a hide
    animation with no position of its own) are simply absent from the map."""
    path = raw_dir / "transforms.rpyc"
    if not path.is_file():
        return {}

    _, top = load_rpyc(path)
    positions: dict[str, int] = {}
    for init_node in top:
        for node in getattr(init_node, "block", None) or []:
            if kind(node) != "Transform":
                continue
            atl = getattr(node, "atl", None)
            statements = getattr(atl, "statements", None) if atl else None
            if not statements:
                continue
            exprs = getattr(statements[0], "expressions", None)
            if not exprs:
                continue
            expr = exprs[0][0]
            m = _POS_EXPR_RE.match(expr.strip()) if isinstance(expr, str) else None
            if m:
                positions[node.varname] = int(m.group(1))
    return positions


def _pos_from_x(x: int) -> int:
    """Converts a canvas X (0..1280) to OP_SHOW's pos:u8 -- half the
    screen-space center X (image_resolve.py's 0.25 canvas-to-screen scale,
    halved again so the result fits a byte; render.c decodes it back via
    `center_x = pos * 2`, 2px granularity on the 320px-wide scene).

    An earlier version bucketed X into one of 5 fixed anchors instead. That
    lost DDLC's real spacing: a 4-character scene's own transforms place them
    at roughly even quarters of the canvas (e.g. 200/493/786/1080), but two
    of those always fell in adjacent buckets while the middle bucket sat
    unused, rendering as two overlapping pairs with a gap between them
    instead of four evenly spaced characters."""
    return max(0, min(255, round(x / 8)))

_CMP_MAP = {
    ast.Eq: vnasm.CMP_EQ, ast.NotEq: vnasm.CMP_NE,
    ast.Lt: vnasm.CMP_LT, ast.LtE: vnasm.CMP_LE,
    ast.Gt: vnasm.CMP_GT, ast.GtE: vnasm.CMP_GE,
}

# The complement of each comparator -- e.g. NOT(x == v) == (x != v). Used to
# compile a "jump if false" edge (needed for short-circuit and/or) out of
# OP_IF, which only has a "jump if true" form -- see _emit_condition().
_CMP_NEGATE = {
    vnasm.CMP_EQ: vnasm.CMP_NE, vnasm.CMP_NE: vnasm.CMP_EQ,
    vnasm.CMP_LT: vnasm.CMP_GE, vnasm.CMP_GE: vnasm.CMP_LT,
    vnasm.CMP_LE: vnasm.CMP_GT, vnasm.CMP_GT: vnasm.CMP_LE,
}

_AUDIO_PREFIXES = ("play ", "stop ", "queue ", "voice ")

VN_MAX_VARS = 64

# Conservative threshold for compile_file_chunked()'s mid-file split: leaves
# headroom under import_game.py's MAXVARSIZE (65000) for the chunk
# container's own overhead (u16 code_length/string_count, plus a u16 length
# + NUL per string -- 3 bytes/string not counted by this raw code+string
# sum) and for however much more the file grows before the next top-level
# Label boundary (the only point a split is allowed) is reached.
CHUNK_SIZE_BUDGET = 58000


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
    last_sprite: dict = field(default_factory=dict)        # char id -> (base_id, overlay_id)
    last_pos: dict = field(default_factory=dict)            # char id -> pos enum
    transform_positions: dict = field(default_factory=dict) # transform name -> X (0..1280)
    skipped: list = field(default_factory=list)            # [SkipEntry]
    stats: dict = field(default_factory=dict)               # kind -> count
    _pending_scene: int | None = field(default=None, init=False)
    _gensym_counter: itertools.count = field(default_factory=itertools.count, init=False)

    # -- driver ---------------------------------------------------------------

    def compile_file(self, path: Path) -> None:
        _, top = load_rpyc(path)
        self.emit_block(top, path.name)
        self._flush_pending_scene(vnasm.TRANS_CUT)

    def compile_file_chunked(self, path: Path, chunk_id_start: int,
                             budget: int = CHUNK_SIZE_BUDGET) -> list[vnasm.Assembler]:
        """Like compile_file(), but splits @path across multiple chunks if it
        grows past @budget bytes (code + string pool) -- only one file has
        needed this so far (script-ch30, 67954 bytes combined, just over the
        65535 single-AppVar ceiling every other file measured comfortably
        under). Assumes self.asm is already the first chunk to write into
        (matching compile_file()'s calling convention in do_compile()) and
        that @chunk_id_start is that assembler's own chunk_id.

        Only ever splits right before a top-level Label -- the one place
        DDLC's own file structure treats as a safe jump target: sequential
        top-level labels already fall through into each other with no
        explicit Jump between them, so inserting one at a chosen split point
        is behaviorally identical to the fall-through it replaces, not a
        semantic change. Splitting inside a label's own block (a nested
        If/Menu/etc.) isn't attempted -- those aren't valid cross-chunk jump
        targets on their own and no single label has come close to the
        budget by itself yet.
        """
        _, top = load_rpyc(path)
        assemblers = [self.asm]

        for node in top:
            is_label = kind(node) == "Label" and isinstance(node.name, str)
            size = len(self.asm.code) + sum(len(s.encode("utf-8")) for s in self.asm.strings)
            if is_label and size > budget:
                self._flush_pending_scene(vnasm.TRANS_CUT)
                self.asm.jump(node.name)
                self.asm = vnasm.Assembler(chunk_id=chunk_id_start + len(assemblers))
                assemblers.append(self.asm)
            self.emit_node(node, path.name)

        self._flush_pending_scene(vnasm.TRANS_CUT)
        return assemblers

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

    # -- position tracking --------------------------------------------------

    def _resolve_pos(self, char: int, at_list) -> int:
        """Looks up @at_list's position transform (if any) and remembers it
        for this character; a Show/Say that doesn't reposition (a bare show,
        or a say-attribute change) just keeps wherever they already were."""
        for name in at_list or []:
            x = self.transform_positions.get(name)
            if x is not None:
                pos = _pos_from_x(x)
                self.last_pos[char] = pos
                return pos
        return self.last_pos.get(char, vnasm.POS_CENTER)

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

        if name == "poem":
            # The real body is DDLC's interactive word-picking minigame
            # (ui.textbutton/ui.interact() in a Python `while True` loop --
            # not translatable script) plus a Monika's-eyes jump-scare and a
            # word-corruption easter egg, both gated behind
            # persistent.playthrough == 2/3 -- structurally unreachable here,
            # since this project has no persistent multi-playthrough state
            # (see docs/FORMAT.md's "Poem minigame" section). Replace the
            # whole body with a call into the real C-side minigame
            # (src/poem.c) instead of walking it.
            self.asm.minigame(self._var_slot("poem_winner"))
            self.asm.ret()
            return

        self.emit_block(node.block, fname)

    def _emit_Say(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        who = node.who
        text = node.what
        if not isinstance(text, str):
            self._skip(node, fname, "non-literal Say.what")
            return
        text = _strip_text_tags(text)
        speaker = vnasm.SPEAKER_NONE
        tag = CODE_TO_TAG.get(who) if isinstance(who, str) else None
        char = TAG_TO_CHAR.get(tag) if tag else None
        if char is not None:
            speaker = char

        # `s @5c "text"` -- Ren'Py's say-with-attributes shorthand for an
        # implicit sprite change tied to this line, used for ~1 in 5 lines
        # in ch0 (more common than explicit `show` for expression changes).
        # Missing this meant every attribute-driven expression change was
        # silently dropped and the sprite looked frozen on whatever the
        # last *explicit* Show had set.
        if char is not None and node.attributes:
            imgname = (tag,) + tuple(node.attributes)
            base, overlay = self.resolver.sprite_layers(imgname)
            if base is None:
                self._skip(node, fname, f"unresolved say-attribute sprite {imgname!r}")
            else:
                self.asm.show(char, base, overlay, self._resolve_pos(char, None))
                self.last_sprite[char] = (base, overlay)

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
            base, overlay = self.last_sprite.get(char, (None, None))
            if base is None:
                self._skip(node, fname, f"bare 'show {imgname[0]}' with no prior sprite tracked; defaulted to 0")
                base, overlay = 0, None
        else:
            base, overlay = self.resolver.sprite_layers(imgname)
            if base is None:
                self._skip(node, fname, f"unresolved sprite {imgname!r}")
                return
            self.last_sprite[char] = (base, overlay)

        # Position: DDLC positions shown poses via named ATL transforms
        # (transforms.rpy: 't31', 'f22', ...) rather than the simple built-in
        # left/right/truecenter keywords. Each one turns out to be a single
        # `func(X)` call on Ren'Py's 1280-wide canvas (see
        # load_transform_positions) -- converted straight to a screen X
        # (_pos_from_x), not a full ATL property interpreter (no scaling,
        # easing, or the transform family's zoom -- see _resolve_pos and
        # render.c's speaking pop for that last part).
        at_list = node.imspec[3] if len(node.imspec) > 3 else None
        self.asm.show(char, base, overlay, self._resolve_pos(char, at_list))

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
        self.last_pos.clear()

    def _emit_With(self, node, fname: str) -> None:
        # DDLC's scene-level transitions (transforms.rpy) are all named
        # `*_scene*` and are all built the same way: a MultipleTransition that
        # dissolves/wipes out to Solid("#000"), pauses, then comes back in.
        # That black hold is the thing worth reproducing, and TRANS_FADE does
        # exactly it -- so match on the name rather than on "dissolve",
        # which would catch `dissolve_scene_full` but miss `wipeleft_scene`.
        #
        # The non-scene variants (a bare `dissolve` or `wipeleft`) are short
        # crossfades *between* images with no black, which needs alpha
        # blending the 8bpp renderer can't do -- those stay cuts.
        expr = node.expr if isinstance(node.expr, str) else ""
        trans = vnasm.TRANS_FADE if "_scene" in expr else vnasm.TRANS_CUT
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
        else_block = None
        branches: list[tuple["ast.expr", list]] = []

        for cond_str, block in entries:
            if isinstance(cond_str, str) and cond_str.strip() == "True":
                else_block = block
                break

            expr = _parse_condition_expr(cond_str) if isinstance(cond_str, str) else None
            if expr is None or not _condition_supported(expr):
                # Documented degrade: unsupported condition's branch is taken
                # unconditionally, and later entries in this chain (which can
                # only be reached if this one were false) become unreachable.
                self._skip(node, fname,
                          f"unsupported If condition {cond_str!r}; took its branch unconditionally")
                else_block = block
                break

            branches.append((expr, block))

        for expr, block in branches:
            true_label = self._gensym("if_branch")
            next_label = self._gensym("if_next")
            self._emit_condition(expr, true_label, next_label)
            self.asm.label(true_label)
            self.emit_block(block, fname)
            self.asm.jump(end_label)
            self.asm.label(next_label)

        if else_block is not None:
            self.emit_block(else_block, fname)

        self.asm.label(end_label)

    def _emit_condition(self, expr, true_label: str, false_label: str) -> None:
        """Emits branching bytecode for @p expr (validated by
        _condition_supported() first -- this assumes every node is one it
        already approved), jumping to @p true_label if it holds and
        @p false_label otherwise.

        `and`/`or` short-circuit via nested checks against gensym'd
        intermediate labels; `not` just swaps the true/false targets it
        recurses with. A leaf comparison is the one place OP_IF (jump only if
        true) is used directly -- getting the false edge out of it means
        negating the comparator and jumping to false_label unconditionally
        for the fallthrough (see _CMP_NEGATE), since OP_IF has no native
        "jump if false" form.
        """
        if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.And):
            for sub in expr.values[:-1]:
                mid = self._gensym("and")
                self._emit_condition(sub, mid, false_label)
                self.asm.label(mid)
            self._emit_condition(expr.values[-1], true_label, false_label)
            return

        if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.Or):
            for sub in expr.values[:-1]:
                mid = self._gensym("or")
                self._emit_condition(sub, true_label, mid)
                self.asm.label(mid)
            self._emit_condition(expr.values[-1], true_label, false_label)
            return

        if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
            self._emit_condition(expr.operand, false_label, true_label)
            return

        if isinstance(expr, (ast.Name, ast.Attribute)):
            # Bare flag -- `var != 0`, see _condition_supported().
            slot = self._var_slot(_ident_name(expr))
            self.asm.if_(slot, vnasm.CMP_NE, 0, true_label)
            self.asm.jump(false_label)
            return

        # A leaf ast.Compare -- the only remaining case _condition_supported()
        # approves.
        cmp = _CMP_MAP[type(expr.ops[0])]
        slot = self._var_slot(_ident_name(expr.left))
        val = _const_int(expr.comparators[0])
        self.asm.if_(slot, cmp, val, true_label)
        self.asm.jump(false_label)

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
                rows.append((_strip_text_tags(caption), block))

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


def link_chunks(assemblers: list[vnasm.Assembler]) -> list[str]:
    """Resolves every assembler's pending Jump/Call/If/Menu targets against
    the union of every chunk's labels, so a target can live in a different
    chunk than the one referencing it -- tools/import_game.py's do_compile()
    now gives each compiled file its own Assembler/chunk_id, where a single
    combined-chunk compile used to resolve everything against one local
    table (Assembler.patch_missing_labels()/resolve() with no arguments,
    still what a single-assembler caller like tools/gen_demo.py uses).

    A name missing from every chunk (content outside the compiled --files
    set) is stubbed to a local OP_END in whichever chunk references it
    first, the same "content wasn't imported" idea as
    Assembler.patch_missing_labels() -- and that stub is then registered
    into the global table, so any other chunk referencing the same missing
    name correctly cross-chunk-jumps to it instead of getting a second stub.

    Returns the sorted list of names that had to be stubbed, for logging.
    """
    global_labels: dict[str, tuple[int, int]] = {}
    for asm in assemblers:
        for name, offset in asm._labels.items():
            global_labels[name] = (asm.chunk_id, offset)

    missing: set[str] = set()
    for asm in assemblers:
        local_missing = {name for _, name in asm._patches if name not in global_labels}
        if local_missing:
            stub = len(asm.code)
            asm.end()
            for name in local_missing:
                global_labels.setdefault(name, (asm.chunk_id, stub))
            missing |= local_missing

    for asm in assemblers:
        asm.resolve(global_labels)

    return sorted(missing)


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


def _parse_condition_expr(cond_str: str) -> "ast.expr | None":
    try:
        return ast.parse(cond_str, mode="eval").body
    except SyntaxError:
        return None


def _condition_supported(expr) -> bool:
    """True if _emit_condition() (Compiler method, below) can compile @p expr:
    any and/or/not tree of `IDENT <cmp> CONST` leaves. Real Ren'Py condition
    strings are exactly this shape almost everywhere (affection/route/chapter
    gates like `poemsread < 3 or (persistent.playthrough == 0 and
    poemsread < 4)`) -- previously only a single bare comparison was
    supported, degrading every compound condition's branch to "always taken"
    and, for a loop's own exit check, an actual infinite loop rather than a
    cosmetic wrong-branch pick.

    A pure structural check (no bytecode emitted) so _emit_If can validate an
    entire condition before committing to emitting any of it -- vnasm's
    Assembler is append-only, so discovering an unsupported sub-expression
    partway through emission would leave orphaned instructions behind.
    """
    if isinstance(expr, ast.BoolOp) and isinstance(expr.op, (ast.And, ast.Or)):
        return all(_condition_supported(v) for v in expr.values)
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        return _condition_supported(expr.operand)
    if isinstance(expr, (ast.Name, ast.Attribute)):
        # A bare flag (`if s_readpoem:`, `not y_ranaway`) -- treated as
        # `var != 0`, the same truthiness a real Python `if` would use for a
        # story-tracking int/bool. Just as common in real conditions as an
        # explicit comparison (e.g. poemresponses.rpy's own item guards).
        return _ident_name(expr) is not None
    if isinstance(expr, ast.Compare) and len(expr.ops) == 1 and len(expr.comparators) == 1:
        if _CMP_MAP.get(type(expr.ops[0])) is None:
            return False
        return _ident_name(expr.left) is not None and _const_int(expr.comparators[0]) is not None
    return False
