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
failure is variable-slot overflow (VN_MAX_VARS) -- a wrong slot means a
wrong branch taken at runtime, a correctness bug, not a cosmetic gap.
"""

from __future__ import annotations

import ast
import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path

import vnasm
from rpyc_ast import flatten, kind, load_rpyc, pycode_source

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


# Every DDLC screen position has several named transform variants sharing
# one position but differing in which visual effect they carry -- confirmed
# by decompiling transforms.rpy: "t11" (base, zoom 1.00x), "f11" (focused --
# the *real* speaking effect, zoom 1.05x eased in over .25s), "h11" (hop, a
# one-shot bounce, zoom stays 1.00x), "hf11" (hop + the zoom together),
# "s11" (sink -- eases ypos from 1.03 to 1.06, i.e. drifts down ~5 screen px
# over .5s and *holds* there, unlike hop's bounce-back). The real script
# authors "at f32" for the exact line where that position's character is
# speaking and "at t32" otherwise -- confirmed by grepping every Show/Say
# `at` list in script-ch0..ch4.rpyc, which found f{pos} and t{pos} used in
# near-equal counts per position (e.g. ch2: f32=29, t32=29). Sink is rarer
# (30 uses game-wide) but lands early and often enough in ch0 (twice in the
# first 45 lines) to be one of the first things a player notices -- the
# character visibly sinks down mid-scene, then rises back to normal the next
# time they're shown under t/f (confirmed: both tcommon's and focus's own
# "replace" handler eases ypos back to 1.03 over .15s, i.e. the *recovery*
# is authored on the landing transform, not on sink itself).
#
# "l" (leftin, a horizontal entrance slide from off-screen) is a real DDLC
# effect too but out of scope here -- it's a one-time entrance animation
# rather than a per-line speaking signal, and this engine has no notion of
# "this Show is the character's first appearance this scene" to trigger it
# from. Treated as the base case (no zoom/hop/sink), matching this engine's
# existing behavior, a deliberate simplification, not a regression.
VN_FLAG_ZOOM = 1  # must match src/vn.h's VN_FLAG_ZOOM
VN_FLAG_HOP = 2   # must match src/vn.h's VN_FLAG_HOP
VN_FLAG_SINK = 4  # must match src/vn.h's VN_FLAG_SINK

_ANIM_PREFIX_FLAGS = {
    "f": VN_FLAG_ZOOM,
    "h": VN_FLAG_HOP,
    "hf": VN_FLAG_ZOOM | VN_FLAG_HOP,
    "s": VN_FLAG_SINK,
}
_ANIM_PREFIX_RE = re.compile(r"^([a-z]+?)\d*$")


def load_transform_animations(raw_dir: Path) -> dict[str, tuple[int, int]]:
    """Maps each position-carrying transform's name (e.g. "f21") to its
    (X coordinate in Ren'Py's 1280-wide canvas, OP_SHOW flags bitmask), by
    reading transforms.rpyc. Transforms that aren't a single `func(N)` call
    (e.g. "thide", a hide animation with no position of its own) are simply
    absent from the map."""
    path = raw_dir / "transforms.rpyc"
    if not path.is_file():
        return {}

    _, top = load_rpyc(path)
    animations: dict[str, tuple[int, int]] = {}
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
            if not m:
                continue
            prefix_m = _ANIM_PREFIX_RE.match(node.varname)
            prefix = prefix_m.group(1) if prefix_m else ""
            flags = _ANIM_PREFIX_FLAGS.get(prefix, 0)
            animations[node.varname] = (int(m.group(1)), flags)
    return animations


def load_variable_defaults(raw_dir: Path) -> dict[str, int | str]:
    """Maps each variable name DDLC's own `default` statement (definitions.rpy,
    compiled to definitions.rpyc's `Default` nodes) initializes, to its
    scalar value -- an int or a str, whichever _const_scalar resolves.

    definitions.rpyc is never in the compiled --files set (it declares
    variables, it doesn't advance the story), so this is a second, separate
    load, the same pattern as load_transform_animations() reading
    transforms.rpyc. Only the ~40 of 58 Default statements whose value is a
    plain literal are usable here; a handful default to a list
    (`n_poemappeal = [0, 0, 0]`, `poemwinner = ['sayori', 'sayori',
    'sayori']`) and stay out of scope until per-index variable slots exist
    (see VN_MAX_VARS's task list) to hold them -- returning nothing for
    those names is indistinguishable from "never referenced" to the caller,
    which is the correct degrade: the variable still starts at plain 0
    rather than a wrong guessed value.

    Every variable really does start at 0 without this (a fresh vars[]
    array is zeroed by vn_init) -- for most story counters that already
    matches Ren'Py's own default (`playthrough = 0`, `chapter = 0`). It
    matters where it doesn't: DDLC's own s_name/n_name/y_name/m_name default
    to the real names, not empty/zero, which is what makes "???" a real
    plot beat (the game *changes* them to "???" at specific points) rather
    than an engine bug where they're unset from the start.
    """
    path = raw_dir / "definitions.rpyc"
    if not path.is_file():
        return {}

    _, top = load_rpyc(path)
    defaults: dict[str, int | str] = {}

    def walk(nodes) -> None:
        for node in nodes or []:
            if kind(node) == "Default":
                value = _const_scalar(pycode_source(getattr(node, "code", None)) and
                                      _parse_default_value(pycode_source(node.code)))
                if value is not None:
                    defaults[node.varname] = value
            walk(getattr(node, "block", None))

    walk(top)
    return defaults


def load_label_params(raw_dir: Path, files: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Ren'Py label parameter declarations (`label showpoem(poem=None,
    music=True, ...):`) across every file in this build's --files selection,
    name -> [(param_name, default_source_text), ...] straight from the
    decompiled ParameterInfo.

    Scanned once, up front (import_game.py's do_compile(), before any file
    actually compiles), the same reasoning as load_transform_animations()/
    load_variable_defaults(): a call site is very often in a different,
    earlier-compiled file than the label it targets (showpoem is called
    from poemresponses.rpyc but declared in poems.rpyc), so _emit_Call
    can't just remember whatever _emit_Label happened to see already --
    the callee's parameter list has to be known regardless of compile
    order. See Compiler.label_params/_emit_Call.
    """
    result: dict[str, list[tuple[str, str]]] = {}
    for stem in files:
        path = raw_dir / f"{stem}.rpyc"
        if not path.is_file():
            continue
        _, top = load_rpyc(path)
        for node in flatten(top):
            if kind(node) != "Label" or not isinstance(node.name, str):
                continue
            info = getattr(node, "parameters", None)
            params = getattr(info, "parameters", None) if info is not None else None
            if params:
                result[node.name] = list(params)
    return result


def _parse_default_value(src: str):
    """definitions.rpyc's Default.code carries the value expression's source
    text (e.g. `"Sayori"`, `0`, `None`) rather than a ready AST node --
    parsed the same way pycode_source()'s callers elsewhere in this module
    already parse condition strings."""
    try:
        return ast.parse(src, mode="eval").body
    except SyntaxError:
        return None


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

VN_MAX_VARS = 256  # mirrors src/vn.h -- the u8 variable operand's ceiling

# Threshold for compile_file_chunked()'s mid-file split. The hard ceiling is
# import_game.py's MAXVARSIZE (65000), and this used to sit just under it at
# 58000 -- but the binding constraint is runtime RAM, not AppVar size. The
# resident chunk shares the calculator's ~150KB of usable RAM with graphx's
# ~77KB draw buffer, so a 58000-budget chunk (they reached 62.8KB, since a
# split is only allowed at a top-level Label boundary and the budget is
# checked before crossing one) left barely 10KB free -- too little for
# render-time scratch, which is why src/assets.c's scaled-sprite cache and
# the moving-actor backdrop plate in src/render.c would simply fail to
# allocate and silently fall back on exactly the busiest chapters.
#
# At 24000 the same Act 1 script becomes 19 chunks instead of 14, the
# largest 29KB, leaving ~44KB free. The cost is more AppVars and slightly
# more frequent chunk loads, each of which is itself proportionally cheaper
# to read.
CHUNK_SIZE_BUDGET = 24000


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
    var_strings: dict = field(default_factory=dict)       # string -> interned id (VN_STR_BASE+n)
    last_sprite: dict = field(default_factory=dict)        # char id -> (base_id, overlay_id)
    last_pos: dict = field(default_factory=dict)            # char id -> pos enum
    last_flags: dict = field(default_factory=dict)          # char id -> OP_SHOW flags
    last_chapter: int | None = None                        # see `chapter = N`
                                                             # tracking in
                                                             # _emit_python_stmt
    transform_animations: dict = field(default_factory=dict) # transform name -> (X, flags)
    # Ren'Py label parameter declarations (`label X(a=1, b=2):`), name ->
    # [(param_name, default_source), ...] -- see load_label_params() and
    # _emit_Call. Populated up front (import_game.py's do_compile(), before
    # any file actually compiles) rather than as each Label is walked,
    # since a call site is very often in a different, earlier-compiled
    # file than the label it targets (showpoem, called from
    # poemresponses.rpyc but declared in poems.rpyc).
    label_params: dict = field(default_factory=dict)
    skipped: list = field(default_factory=list)            # [SkipEntry]
    stats: dict = field(default_factory=dict)               # kind -> count
    _pending_scene: int | None = field(default=None, init=False)
    _gensym_counter: itertools.count = field(default_factory=itertools.count, init=False)

    # -- driver ---------------------------------------------------------------

    # The four character-name variables get slots 0..3, in TAG_TO_CHAR order,
    # reserved before anything else can claim them. That makes a character's
    # id also the slot holding her displayed name, so src/render.c can render
    # the name plate straight from vars[speaker] with nothing shipped to map
    # between them.
    #
    # DDLC drives the name plate through these: they default to the real
    # names, script.rpyc opens by setting them to "???" / "Girl 1" / "Girl 2"
    # / "Girl 3", ch0 assigns each real name at the introduction, and Act 2
    # sets m_name back to "???". Rendering a fixed table instead is why every
    # character used to be named from her first line.
    #
    # It is a contract with the engine, like TAG_TO_CHAR's ids themselves --
    # hence reserving them up front rather than letting allocation order
    # decide.
    NAME_VARS = ("s_name", "n_name", "y_name", "m_name")

    # A fifth reserved slot: scratch space for OP_RANDOM. Every real
    # `renpy.random.randint(a, b)` in the game is consumed immediately by
    # the comparison right next to it (`== 0`, never assigned to a story
    # variable first -- see _RANDINT_RE's docstring), so one shared slot
    # is enough regardless of how many randint() calls the script has: each
    # draw is written and read back within the same handful of instructions,
    # never read again afterward.
    RAND_SCRATCH = "$rand"

    # One persistent flag per character, tracking DDLC's own delete_character
    # mechanic (the real game deletes that character's .chr file from disk;
    # this engine has nothing to delete, so it tracks the fact instead --
    # see _emit_python_stmt's handling of delete_character/
    # restore_all_characters/delete_all_saves calls). Named with the
    # "persistent." prefix deliberately: tools/import_game.py's DPSLOT
    # packaging already collects every variable named that way, so a
    # deletion survives a New Game exactly like the real .chr file staying
    # gone would, with no separate persistence wiring needed here.
    DELETED_VARS = ("persistent.deleted_sayori", "persistent.deleted_natsuki",
                    "persistent.deleted_yuri", "persistent.deleted_monika")

    # One more reserved slot, right after DELETED_VARS: persistent.playthrough
    # is read both by splash.rpyc's ghost-menu/glitch gates (`== 2`) and by
    # script.rpy's own `label start`, which branches straight on it to pick
    # which act to jump into (0 -> ch0_main, 1 -> ch10_main, 2 -> ch20_main,
    # 3 -> ch30_main, 4 -> credits). Reserved as a fixed slot, like
    # DELETED_VARS, so vn.h's VN_PLAYTHROUGH_VAR is a real, stable address --
    # but deliberately left at its default (always 0) rather than ever
    # written by this engine: an earlier version had main.c increment it on
    # every New Game to make the ghost menu reachable, which was a real bug
    # -- it collided with `label start`'s own dispatch, so every New Game
    # after the first skipped straight into later, unfinished Act 2/3
    # content instead of restarting Act 1. See vn.h's own comment on
    # VN_PLAYTHROUGH_VAR for the full story.
    PLAYTHROUGH_VAR = "persistent.playthrough"

    def __post_init__(self) -> None:
        for name in self.NAME_VARS:
            self._var_slot(name)
        self._var_slot(self.RAND_SCRATCH)
        for name in self.DELETED_VARS:
            self._var_slot(name)
        self._var_slot(self.PLAYTHROUGH_VAR)

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

    def _intern(self, text: str) -> int:
        """The integer standing in for @p text -- see VN_STR_BASE. Stable for
        the whole build (one Compiler compiles every chunk), so an id assigned
        in one chunk still means the same string in another; the pool ships
        separately from the per-chunk dialogue strings for exactly that
        reason."""
        if text in self.var_strings:
            return self.var_strings[text]
        value = VN_STR_BASE + len(self.var_strings)
        if value > 32767:
            raise CompileError(
                f"interned string overflow: no room for {text!r} "
                f"({len(self.var_strings)} already interned, ceiling is "
                f"{32767 - VN_STR_BASE})")
        self.var_strings[text] = value
        return value

    def _const_operand(self, node) -> int | None:
        """A literal's i16 operand: a number as itself, a string as its
        interned id."""
        value = _const_scalar(node)
        return self._intern(value) if isinstance(value, str) else value

    # -- scene buffering (so `scene X \n with dissolve` gets the real trans) ---

    def _flush_pending_scene(self, trans: int) -> None:
        if self._pending_scene is not None:
            self.asm.scene(self._pending_scene, trans)
            self._pending_scene = None

    # -- position/animation tracking -----------------------------------------

    def _resolve_anim(self, char: int, at_list) -> tuple[int, int]:
        """Looks up @at_list's position+animation transform (if any) and
        remembers both for this character; a Show/Say that doesn't reposition
        (a bare show, or a say-attribute change) just keeps whatever pos/flags
        they already had -- e.g. a mid-line expression change under the same
        "at f32" the character was already speaking under.

        One lookup covering both pos and flags (rather than two separate
        walks of @at_list) guarantees they're always read off the exact same
        matched transform name, never out of sync with each other."""
        for name in at_list or []:
            anim = self.transform_animations.get(name)
            if anim is not None:
                x, flags = anim
                pos = _pos_from_x(x)
                self.last_pos[char] = pos
                self.last_flags[char] = flags
                return pos, flags
        return self.last_pos.get(char, vnasm.POS_CENTER), self.last_flags.get(char, 0)

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
            # not translatable script), followed by real conditional
            # reactions (confirmed by decompiling it: 19 nodes after the
            # game loop, including a Monika's-eyes jump-scare check and a
            # Show) that this compiler still can't reach -- see below for
            # why -- so that part is out of scope here, tracked separately
            # (this session's task list).
            #
            # DDLC's per-chapter poemwinner[]/s_poemappeal[]/n_poemappeal[]/
            # y_poemappeal[] need to know *which chapter* is calling, and a
            # single shared label has no way to know that -- every `call
            # poem` site looks identical to it. So instead of compiling this
            # label as a real callable target, every *static* `call poem`
            # site (see _emit_Call) is inlined directly with its own
            # compile-time-known chapter, and never actually calls here.
            #
            # This stub exists only for whatever can't be resolved that way
            # -- a call site with no compile-time-known chapter (found: one,
            # script-ch30's Act 3 finale, whose preceding chapter value
            # isn't provably known at compile time) -- so it isn't silently
            # dropped, just degraded to writing generic (non-chapter-
            # specific) scratch slots instead of the real per-chapter ones.
            self.asm.minigame(self._var_slot("poem_winner"),
                             self._var_slot("poem_s_appeal"),
                             self._var_slot("poem_n_appeal"),
                             self._var_slot("poem_y_appeal"))
            self.asm.ret()
            return

        if name == "splashscreen":
            # The real body (game/splash.rpy) is almost entirely things this
            # engine's C-side startup already handles its own way -- Windows
            # process/anti-cheat probing (renpy.windows-gated subprocess
            # calls, meaningless on-calc), .chr file existence checks (this
            # engine already tracks character deletion its own way, see
            # DELETED_VARS), first-run bookkeeping tied to a real
            # filesystem, and a duplicate logo/content-warning screen
            # main.c's own run_splash_screens() already shows (compiling
            # both would show the warning twice on first launch). Rather
            # than walk any of that, only the one self-contained, still-
            # meaningful piece is extracted and re-emitted here: the ghost
            # menu's trigger check. Kept under the label's real name rather
            # than a made-up synthetic one, so tools/import_game.py can look
            # up its address the same way it already does for `label start`
            # -- see main.c's run_splashscreen_check(). Everything else in
            # this label (autoload/anticheat/s_kill_early/the readonly-
            # install check) is real content, deliberately deferred -- see
            # this session's task list, not silently dropped.
            self._emit_ghost_menu_check(node.block, fname)
            return

        self.emit_block(node.block, fname)

    def _emit_ghost_menu_check(self, block, fname: str) -> None:
        """The one self-contained piece of splashscreen's real body worth
        keeping -- see _emit_Label's "splashscreen" case above for why the
        rest isn't compiled at all.

        Hand-emitted rather than routed through emit_block()/_emit_If(): the
        real body's `show black`/`show end` are bare non-character images
        (Ren'Py's built-in Solid("#000") and DDLC's own `image end:` from
        definitions.rpy), and OP_SHOW has no such thing -- its ch:u8 operand
        is a character slot, always one of the 4 cast members (see
        _emit_Show's TAG_TO_CHAR lookup, which would just skip these as an
        "unknown character tag"). A full-screen standalone image is much
        closer to what OP_SCENE already does (the background fills the
        whole scene area) than to a character overlay, so this reframes
        both Shows as scene changes instead -- confirmed sound: the real
        body never re-shows a character over either of them, and hides
        every character anyway (an implicit effect of any `scene`-equivalent
        change, see _emit_Scene's self.last_sprite.clear()).

        The condition itself is still pulled from the real decompiled
        source, not hand-typed, via the same seen_ghost_menu marker search
        _emit_Label used to use here -- the one part worth protecting
        against a silent transcription slip.
        """
        ghost_if = next(
            (stmt for stmt in block
             if kind(stmt) == "If"
             and any("seen_ghost_menu" in cond for cond, _ in stmt.entries)),
            None)
        if ghost_if is None:
            self._skip(block[0] if block else None, fname,
                      "splashscreen's ghost-menu If not found by its "
                      "seen_ghost_menu marker -- source shape changed?")
            self.asm.ret()
            return

        cond_str = ghost_if.entries[0][0]
        expr = _parse_condition_expr(cond_str) if isinstance(cond_str, str) else None
        if expr is not None:
            expr = _fold_constants(expr)
        if expr is None or not _condition_supported(expr):
            self._skip(ghost_if, fname, f"unsupported ghost-menu condition {cond_str!r}")
            self.asm.ret()
            return

        true_label = self._gensym("ghost_true")
        end_label = self._gensym("ghost_end")
        self._emit_condition(expr, true_label, end_label)
        self.asm.label(true_label)

        black = self.resolver.scene_id(("black",))
        end_img = self.resolver.scene_id(("end",))
        if black is not None:
            self.asm.scene(black, vnasm.TRANS_CUT)
        else:
            self._skip(ghost_if, fname, "ghost menu: 'black' image unresolved")
        # config.main_menu_music / renpy.music.play(): dropped, no CE audio
        # (see vn.h's OP_SOUND doc comment -- an established, already-no-op
        # convention, not a new gap).
        self.asm.set(self._var_slot("persistent.seen_ghost_menu"), 1)
        self.asm.set(self._var_slot("persistent.ghost_menu"), 1)
        self.asm.pause(1000)
        if end_img is not None:
            self.asm.scene(end_img, vnasm.TRANS_CUT)
        else:
            self._skip(ghost_if, fname, "ghost menu: 'end' image unresolved")
        self.asm.pause(3000)
        # config.allow_skipping = True: dropped, this engine has no skip
        # toggle to restore.

        self.asm.label(end_label)
        self.asm.ret()

    def _emit_Say(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        who = node.who
        text = node.what
        if not isinstance(text, str):
            self._skip(node, fname, "non-literal Say.what")
            return
        text = _strip_text_tags(text)
        if who is not None:
            # DDLC's own Character() defs (definitions.rpy: mc/s/m/n/y/ny)
            # all set what_prefix='"'/what_suffix='"' -- Ren'Py adds the
            # quote marks at render time, they're never part of Say.what
            # itself. Only the narrator (who=None) has no prefix/suffix.
            # This engine has no equivalent prefix/suffix mechanism, so the
            # simplest faithful match is baking the quotes into the text
            # here, for every attributed line -- including one this engine
            # doesn't otherwise recognize (e.g. "ny", Natsuki & Yuri's
            # combined lines, which fall through to narration-style
            # rendering below): the quotes are still real DDLC behavior for
            # it, independent of whether this engine also gives it its own
            # name plate.
            text = f'"{text}"'
        speaker = vnasm.SPEAKER_NONE
        tag = CODE_TO_TAG.get(who) if isinstance(who, str) else None
        char = TAG_TO_CHAR.get(tag) if tag else None
        if char is not None:
            speaker = char
        elif who == "mc":
            # The protagonist speaking aloud, distinct from narration (who is
            # None) -- DDLC writes these as different Character objects, and
            # only one of them should carry the player's name on the plate.
            # `char` stays None: "mc" isn't in TAG_TO_CHAR (no sprite/anim to
            # resolve for the player), so the say-attribute handling below
            # correctly still skips for this speaker, same as narration.
            # See vn.h's VN_SPEAKER_PLAYER and main.c's speaker_display_name().
            speaker = vnasm.SPEAKER_PLAYER

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
                pos, flags = self._resolve_anim(char, None)
                self.asm.show(char, base, overlay, pos, flags=flags)
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
            # Not one of the 4 cast members -- OP_SHOW's ch:u8 operand only
            # ever names one of them, so this was always a skip until now.
            # But a bare `show X` where X isn't a character tag is DDLC's
            # own idiom for a full-screen standalone image (the ghost menu's
            # `show black`/`show end`, poems_special.rpyc's 11 `show
            # poem_specialN` picture-book pages, confirmed identical shape
            # for both) -- much closer to what OP_SCENE already does than
            # to a character overlay, so it's reframed as one instead of
            # skipped. Emitted immediately rather than through the usual
            # deferred _pending_scene/_flush_pending_scene combo (see
            # _emit_Scene): a bare Show like this is very often the last
            # meaningful statement before a label's Return (both real
            # examples above are), and nothing would flush a still-pending
            # scene before that Return compiles -- it would silently land
            # as dead code after the label's own OP_RETURN instead. Any
            # `with` transition wrapping the real Show already only ever
            # compiles to a cut here regardless (_emit_With's own
            # simplification, see "Scene transitions" in FORMAT.md), so
            # immediate emission costs nothing bypassing the deferred path
            # would have bought anyway.
            scene_id = self.resolver.scene_id(imgname)
            if scene_id is None:
                self._skip(node, fname, f"unknown character tag in {imgname!r}")
                return
            self.asm.scene(scene_id, vnasm.TRANS_CUT)
            self.last_sprite.clear()
            self.last_pos.clear()
            self.last_flags.clear()
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

        # Position + animation: DDLC positions shown poses via named ATL
        # transforms (transforms.rpy: 't31', 'f22', ...) rather than the
        # simple built-in left/right/truecenter keywords. Each one turns out
        # to be a single `func(X)` call on Ren'Py's 1280-wide canvas (see
        # load_transform_animations) -- converted straight to a screen X
        # (_pos_from_x). The transform *name*'s prefix (t/f/h/hf/s) also
        # carries the real per-line speaking/movement signal -- see
        # _resolve_anim and render.c's zoom/hop/sink handling.
        at_list = node.imspec[3] if len(node.imspec) > 3 else None
        pos, flags = self._resolve_anim(char, at_list)
        self.asm.show(char, base, overlay, pos, flags=flags)

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
        self.last_flags.clear()

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

    def _dynamic_target_var(self, expr_src: str) -> str | None:
        """The variable name to compile `jump/call expression <expr_src>`
        against, or None if @expr_src isn't one -- see OP_JUMP_VAR.

        Only a bare identifier resolves: DDLC's own dynamic targets are
        overwhelmingly a single variable holding a previously-assigned
        label-name string (`nextscene`, `persistent.autoload`). A handful
        build the name by concatenating a literal prefix onto a runtime
        value (`"ch30_" + str(persistent.current_monikatopic)`) -- that
        needs enumerating the value's possible range and isn't attempted
        here, so those stay correctly unsupported rather than guessed.
        """
        expr = _parse_condition_expr(expr_src)
        return _ident_name(expr) if expr is not None else None

    def _emit_Jump(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        if getattr(node, "expression", False):
            var = self._dynamic_target_var(node.target)
            if var is None:
                self._skip(node, fname, "dynamic jump target unsupported")
                self.asm.nop()
                return
            self.asm.jump_var(self._var_slot(var))
            return
        if not isinstance(node.target, str):
            self._skip(node, fname, "dynamic jump target unsupported")
            self.asm.nop()
            return
        self.asm.jump(node.target)

    def _emit_Call(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        if getattr(node, "expression", False):
            var = self._dynamic_target_var(node.label)
            if var is None:
                self._skip(node, fname, "dynamic call target unsupported")
                self.asm.nop()
                return
            self.asm.call_var(self._var_slot(var))
            return
        if not isinstance(node.label, str):
            self._skip(node, fname, "dynamic call target unsupported")
            self.asm.nop()
            return
        if node.label == "poem" and self.last_chapter is not None:
            # Inline, not a real call -- see _emit_Label's "poem" case for
            # why a shared label can't carry per-chapter state. Each
            # (name, chapter) pair gets its own slot via the same indexed-
            # variable mechanism `poemwinner[N]` already uses (_var_slot()
            # just sees an ordinary string key), so repeat visits to the
            # same chapter -- which shouldn't happen in practice, but this
            # doesn't assume it won't -- correctly reuse the same slots
            # rather than silently allocating new ones.
            ch = self.last_chapter
            self.asm.minigame(self._var_slot(f"poemwinner[{ch}]"),
                             self._var_slot(f"s_poemappeal[{ch}]"),
                             self._var_slot(f"n_poemappeal[{ch}]"),
                             self._var_slot(f"y_poemappeal[{ch}]"))
            return
        params = self.label_params.get(node.label)
        if params:
            self._emit_call_args(node, params, fname)
        self.asm.call(node.label)

    def _emit_call_args(self, node, params: list, fname: str) -> None:
        """Binds a parameterized Call's arguments onto the callee's declared
        slots before the real OP_CALL -- see Compiler.label_params.

        This engine's variables are flat global slots, not a real per-call
        stack frame, so every declared parameter is set on every call
        (defaults included, not just the ones this call overrides) --
        otherwise a parameter this call leaves at its default would read
        whatever a PREVIOUS, different call happened to leave behind, since
        nothing else resets it between calls.
        """
        info = getattr(node, "arguments", None)
        args = list(info.arguments) if info is not None and info.arguments else []
        positional_names = [name for name, _ in params]

        bound: dict[str, str] = {}
        pos_i = 0
        for kw, val_src in args:
            if kw is None:
                if pos_i < len(positional_names):
                    bound[positional_names[pos_i]] = val_src
                pos_i += 1
            else:
                bound[kw] = val_src

        for name, default_src in params:
            src = bound.get(name, default_src)
            expr = _parse_condition_expr(src)
            value = self._const_operand(expr) if expr is not None else None
            if value is None:
                # Not a literal -- e.g. showpoem's `where=i11`, a transform
                # name, not a constant this compiler can bind to a variable.
                # Left at whatever the slot already holds rather than
                # guessed; every other parameter on this same call still
                # binds correctly.
                self._skip(node, fname, f"call argument {name}={src!r} not a constant")
                continue
            self.asm.set(self._var_slot(name), value)

    def _emit_Return(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        self.asm.ret()

    def _emit_UserStatement(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        line = node.line if isinstance(node.line, str) else ""
        stripped = line.strip()
        if stripped.startswith(_AUDIO_PREFIXES):
            self.asm.sound(0)
            return
        if stripped == "hide screen tear":
            self.asm.tear_hide()
            return
        if stripped.startswith("show screen tear("):
            call = _parse_tear_call(stripped)
            if call is not None:
                self.asm.tear_show(*call)
                return
        # `window hide`/`window hide(...)`/`window hide(config....)` all
        # mean the same thing here regardless of the transition argument
        # (this engine has no cross-fade to run one under) -- matched by
        # prefix rather than exact string for that reason. `window auto`
        # and an explicit `window show(...)` both compile to the same
        # opcode too -- see OP_WINDOW_SHOW's own comment in vn.h for why
        # that's a real equivalence here, not a shortcut.
        if stripped == "window hide" or stripped.startswith("window hide("):
            self.asm.window_hide()
            return
        if stripped == "window auto" or stripped.startswith("window show("):
            self.asm.window_show()
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
        if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                and _ident_name(stmt.value.func) == "pause"):
            # DDLC's own pause(seconds) helper (a thin renpy.pause()
            # wrapper) -- not Ren'Py's built-in `pause` statement, which
            # never actually shows up in the compiled game; this is a plain
            # function call, hence living here in the Python-statement
            # dispatcher rather than as its own node kind in emit_node().
            ms = _pause_ms(stmt.value)
            if ms is not None:
                self.asm.pause(ms)
                return True
            return False
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            fn = _ident_name(stmt.value.func)
            if fn == "delete_character" and len(stmt.value.args) == 1:
                tag = _const_scalar(stmt.value.args[0])
                if isinstance(tag, str) and tag in TAG_TO_CHAR:
                    self.asm.set(self._var_slot(self.DELETED_VARS[TAG_TO_CHAR[tag]]), 1)
                    return True
                return False
            if fn == "restore_all_characters" and not stmt.value.args:
                for name in self.DELETED_VARS:
                    self.asm.set(self._var_slot(name), 0)
                return True
            if fn == "delete_all_saves" and not stmt.value.args:
                # Genuinely destructive -- erases every DSAVEn slot on the
                # calculator, no undo. Confirmed explicitly before this
                # opcode existed at all (see git history / OP_DELETE_SAVES's
                # own comment in vn.h); not a default this compiler takes on
                # its own.
                self.asm.delete_saves()
                return True
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            var = _ident_name(stmt.targets[0])
            val = self._const_operand(stmt.value)
            if var is not None and val is not None:
                self.asm.set(self._var_slot(var), val)
                if var == "chapter":
                    # DDLC's own script always writes `chapter = <literal>`
                    # immediately before `call poem` -- confirmed by reading
                    # script.rpyc's actual compiled sequence, not assumed --
                    # so tracking the most recently assigned literal here is
                    # enough to know, at compile time, which chapter a given
                    # `call poem` site belongs to, with no runtime variable
                    # read needed. See _emit_Call's use of this.
                    #
                    # val < VN_STR_BASE excludes the (never actually
                    # occurring, but worth guarding) case of an interned
                    # string id: _const_operand() returns one plain int
                    # either way, and a string id is still `isinstance(...,
                    # int)` True despite meaning something completely
                    # different from a chapter number.
                    self.last_chapter = (int(val) if isinstance(val, int)
                                        and val < VN_STR_BASE else None)
                return True
        if isinstance(stmt, ast.AugAssign) and isinstance(stmt.op, (ast.Add, ast.Sub)):
            var = _ident_name(stmt.target)
            # _const_scalar, not _const_int: it accepts the one shape this
            # engine needs a float for, an exact half-integer literal like
            # `ac += 0.5` (pre-doubled to an int -- see its docstring). Guard
            # the result to int explicitly since _const_scalar can also
            # return a str (a plain `x += "..."` isn't a numeric delta and
            # was never int-only to begin with; nothing in the real game
            # does this, but the guard keeps it that way rather than assuming).
            val = _const_scalar(stmt.value)
            if var is not None and isinstance(val, int):
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
            if expr is not None:
                expr = _fold_constants(expr)
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

        if isinstance(expr, ast.Constant) and isinstance(expr.value, bool):
            # Compile-time-resolved (see _fold_constants()) -- no comparison
            # to run, just an unconditional jump to whichever side holds.
            self.asm.jump(true_label if expr.value else false_label)
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
        bounds = _randint_call(expr.left)
        if bounds is not None:
            # `renpy.random.randint(lo, hi) == N` -- draw fresh into the
            # shared scratch slot right here, immediately before comparing
            # it, so the draw is consumed exactly once and nothing needs to
            # persist it (see RAND_SCRATCH). Mirrors what the equivalent
            # Python would actually do at this point in execution: evaluate
            # randint() now, use the result once.
            lo, hi = bounds
            slot = self._var_slot(self.RAND_SCRATCH)
            self.asm.random(slot, lo, hi)
        else:
            slot = self._var_slot(_ident_name(expr.left))
        val = self._const_operand(expr.comparators[0])
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
    if isinstance(node, ast.Subscript):
        # DDLC keeps per-chapter state in lists -- poemwinner[1],
        # n_poemappeal[0] -- rather than one variable per chapter. The VM has
        # no notion of a list or of indexing at runtime, but every real use
        # found across the whole game indexes with a literal int (never a
        # variable), so there's no need for one: "poemwinner[1]" becomes its
        # own distinct variable name here, as if the script had actually
        # written a variable by that name, and _var_slot()/_intern() (which
        # only ever see strings, never knowing this one came from a
        # subscript) give it a slot like any other.
        #
        # A non-constant index (`poemwinner[chapter]`) can't resolve to one
        # fixed name at compile time -- returning None here degrades it the
        # same as any other unsupported expression (skipped, branch taken
        # unconditionally), which is correct: making it up would silently
        # read the wrong chapter's slot.
        base = _ident_name(node.value)
        index = _const_int(node.slice)
        if base is None or index is None:
            return None
        # A leading underscore is Ren'Py's own convention for an internal
        # list, not story state -- `_history_list[-1].what = "..."` is how
        # DDLC retroactively rewrites a line already shown on the History
        # screen (found in one exclusive scene). Nothing in this engine
        # tracks or displays history, so there's nothing to rewrite: turning
        # it into a variable would silently fabricate an always-empty slot
        # for something meaningless here, worse than the honest "skipped"
        # it degraded to before this whole branch existed. Every real
        # DDLC list this is meant for (poemwinner, s/n/y/m_poemappeal)
        # starts with a letter, never an underscore.
        if base.split(".", 1)[0].split("[", 1)[0].startswith("_"):
            return None
        return f"{base}[{index}]"
    return None


# String-valued variables, without runtime strings.
#
# The VM's variables are int16 and nothing else, which is why every
# `nextscene = "..."` was dropped and every `ch2_winner == "Natsuki"` was
# taken unconditionally. But DDLC never does arithmetic on these -- it
# assigns a string and later compares it for equality, and that is exactly
# what an integer can do if each distinct string gets a distinct integer.
# So string literals are interned at compile time (Compiler._intern) and the
# assignment becomes a plain OP_SET, the comparison a plain OP_IF.
#
# Interned ids start at VN_STR_BASE rather than 0 so a string-valued variable
# never collides with the small integers real numeric variables hold. Nothing
# in the game compares one variable against both kinds, so this is belt and
# braces -- but it also makes a wrong value obvious on sight when reading a
# trace, instead of looking like a plausible counter.
VN_STR_BASE = 16384  # must match src/vn.h's VN_STR_BASE


def _const_scalar(node):
    """An integer literal as an int, a string literal as a str, else None.

    The string case is what _const_int (below, still used where only a number
    makes sense) deliberately refuses.

    A float literal is accepted too, but only an exact half-integer (n*0.5) --
    the one place the real game needs it (script-exclusives2-yuri.rpyc's `ac`,
    an affection counter incremented by 0/0.5/1/2 -- confirmed by exhaustive
    search that nothing anywhere in the compiled game ever reads or compares
    it, so there's no un-doubled reference it could drift out of sync with).
    Returned pre-doubled (v*2) so the variable's effective unit becomes
    "halves" and stays a plain int16 -- exact, no VM change, no float support
    needed anywhere else. This makes 0.5 a genuine value this compiler can
    represent, not a special case for that one name: any float assignment or
    comparison against the SAME variable has to consistently go through this
    same doubling, which holds here because it's the only literal `ac` is
    ever written or (per that search) read against.
    """
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _const_scalar(node.operand)
        return -inner if isinstance(inner, int) else None
    if isinstance(node, ast.Constant):
        if node.value is None:
            # Ren'Py label parameter defaults lean on this heavily
            # (`label showpoem(poem=None, track=None, img=None, ...)`) --
            # every VM variable is a plain int16 with no separate "unset"
            # state, and 0 is already what one starts at (vn_init()'s
            # memset), so this is the natural match, not a special case
            # invented for this one caller.
            return 0
        if isinstance(node.value, str):
            return node.value
        if isinstance(node.value, int):
            v = int(node.value)
            return v if -32768 <= v <= 32767 else None
        if isinstance(node.value, float):
            doubled = node.value * 2
            if doubled != int(doubled):
                return None      # not a half-integer -- out of scope, skip
            v = int(doubled)
            return v if -32768 <= v <= 32767 else None
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


def _pure_number(node) -> float | int | None:
    """Evaluates @p node if it's built entirely from numeric literals and
    +/-/*//, else None. Deliberately not ast.literal_eval (which refuses
    BinOp even between two literals) -- DDLC's own pause() call sites are
    plain numbers almost everywhere, but a couple are one literal arithmetic
    step away (`1.0 + 0.5`-shaped), and it costs nothing extra to fold those
    too rather than leave them skipped for a technicality.

    Purely a duration helper: unlike _const_scalar, this never touches a
    variable slot and is never asked to preserve a fractional value's real
    magnitude across separate references, so ordinary floats are fine as-is
    -- no half-integer restriction applies here."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _pure_number(node.operand)
        return -inner if inner is not None else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        left, right = _pure_number(node.left), _pure_number(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        return left / right if right != 0 else None
    return None


def _pause_ms(call: ast.Call) -> int | None:
    """The ms:u16 OP_PAUSE should carry for DDLC's own `pause(...)` helper
    call, or None if the duration can't be resolved at compile time (e.g.
    `pause(len(text) / 30.0 + 0.5)`, a real per-line duration scaled by the
    text just shown -- genuinely dynamic, correctly left unsupported rather
    than guessed).

    A bare `pause()` (no args) is DDLC's "wait for a click, no timeout" --
    OP_PAUSE's own ms=0 sentinel, so this returns 0 for that case rather
    than None (0 is a real, meaningful answer here, not "unresolved").
    A resolved duration that rounds to exactly 0ms (a sub-millisecond
    pause, i.e. essentially decorative) is bumped to 1ms instead, so it
    doesn't collide with that same sentinel and silently turn into an
    indefinite wait."""
    if call.args or call.keywords:
        if len(call.args) != 1 or call.keywords:
            return None
        seconds = _pure_number(call.args[0])
        if seconds is None or seconds < 0:
            return None
        ms = round(seconds * 1000)
        if ms > 65535:
            return None
        return max(ms, 1)
    return 0


def _randint_call(node) -> tuple[int, int] | None:
    """(lo, hi) if @p node is `renpy.random.randint(lo, hi)` with literal int
    bounds, else None.

    Every real use in the whole game is this exact shape, immediately
    compared for equality -- `renpy.random.randint(0, N) == 0`, DDLC's own
    way of writing "1 in (N+1) chance" (the ghost menu's 1/64, Monika's eyes
    in the poem game at 1/6, several Act 2 scene variants). None assign the
    draw to a variable first, which is why Compiler.RAND_SCRATCH exists
    instead of this needing to resolve an arbitrary target name."""
    if (isinstance(node, ast.Call) and _ident_name(node.func) == "renpy.random.randint"
            and len(node.args) == 2 and not node.keywords):
        lo, hi = _const_int(node.args[0]), _const_int(node.args[1])
        return (lo, hi) if lo is not None and hi is not None else None
    return None


# Real defaults for the `tear` screen's parameters (effects.rpy's own
# `screen tear(number=10, offtimeMult=1, ontimeMult=1, offsetMin=0,
# offsetMax=50, srf=None)`), used by _parse_tear_call() below for whichever
# ones a given call site leaves unspecified.
_TEAR_DEFAULTS = {"number": 10, "offtimeMult": 1, "ontimeMult": 1,
                  "offsetMin": 0, "offsetMax": 50}
_TEAR_PARAM_ORDER = ("number", "offtimeMult", "ontimeMult", "offsetMin", "offsetMax")

# The `tear` displayable itself is a custom Python class (effects.rpy), not
# preserved in any compiled .rpyc this pipeline reads -- there is no exact
# pixel algorithm to recover here. offtimeMult/ontimeMult scale some base
# on/off cadence this compiler has no access to either; folding both into
# one re-roll period (how often a displaced band picks a fresh offset) is a
# faithful reinterpretation of "the glitch flickers faster/slower", not a
# claim of matching the original frame-for-frame. TEAR_BASE_MS is the unit
# that scaling is read against.
TEAR_BASE_MS = 50


def _parse_tear_call(stmt: str) -> tuple[int, int, int, int] | None:
    """(chunks, offset_min, offset_max, period_ms) for a `show screen
    tear(...)` statement's exact source text, or None if its arguments
    aren't literal constants. @p stmt still has its `show screen ` prefix;
    only the `tear(...)` call itself is parsed."""
    call_src = stmt[len("show screen "):]
    try:
        node = ast.parse(call_src, mode="eval").body
    except SyntaxError:
        return None
    if not isinstance(node, ast.Call):
        return None

    # _pure_number(), not _const_scalar(): tear's offtimeMult/ontimeMult are
    # genuinely arbitrary floats (0.1, 0.3, ...), not values meant to live in
    # a story variable -- _const_scalar()'s half-integer restriction (built
    # for `ac += 0.5`, see its docstring) doesn't apply to a value that's
    # consumed immediately at compile time and never stored, same reasoning
    # as pause()'s own duration parsing.
    values = dict(_TEAR_DEFAULTS)
    for name, arg in zip(_TEAR_PARAM_ORDER, node.args):
        v = _pure_number(arg)
        if v is None:
            return None
        values[name] = v
    for kw in node.keywords:
        if kw.arg not in _TEAR_DEFAULTS:
            continue   # `srf`, or anything else -- irrelevant to a
                       # parameter-based reinterpretation, not an error
        v = _pure_number(kw.value)
        if v is None:
            return None
        values[kw.arg] = v

    chunks = int(values["number"])
    if not (0 < chunks <= 255):
        return None
    offset_min = round(values["offsetMin"])
    offset_max = round(values["offsetMax"])
    period_ms = round(max(values["offtimeMult"], values["ontimeMult"]) * TEAR_BASE_MS)
    period_ms = max(1, min(period_ms, 65535))
    return chunks, offset_min, offset_max, period_ms


def _is_music_get_playing_call(node) -> bool:
    """True for `renpy.music.get_playing(...)` (any args/kwargs). This
    engine has no audio -- OP_SOUND is a permanent no-op (see vn.h) -- so
    nothing is *ever* currently playing here. Every real condition gating on
    this call is provably always the same answer on this engine, not merely
    unsupported syntax, which is why _fold_constants() below resolves it at
    compile time instead of leaving it to degrade like a genuinely unknown
    condition would."""
    return isinstance(node, ast.Call) and _ident_name(node.func) == "renpy.music.get_playing"


def _fold_constants(expr):
    """Rewrites any recognized always-constant sub-expression (currently
    just renpy.music.get_playing(...) and comparisons against it -- see
    _is_music_get_playing_call) to a literal ast.Constant(bool), recursing
    through and/or/not so a fold anywhere in the tree is found regardless of
    nesting. Run once, before _condition_supported()/_emit_condition() ever
    see the expression, so neither has to know about this specific call
    pattern -- they just treat the result as an ordinary constant leaf.
    """
    if isinstance(expr, ast.BoolOp):
        return ast.BoolOp(op=expr.op, values=[_fold_constants(v) for v in expr.values])
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        return ast.UnaryOp(op=expr.op, operand=_fold_constants(expr.operand))
    if _is_music_get_playing_call(expr):
        # A bare boolean use, e.g. `if renpy.music.get_playing():`.
        return ast.Constant(value=False)
    if (isinstance(expr, ast.Compare) and len(expr.ops) == 1
            and isinstance(expr.ops[0], (ast.Eq, ast.NotEq))
            and _is_music_get_playing_call(expr.left)):
        # `renpy.music.get_playing(...) == X` -- always None on this engine,
        # and None never equals a real audio-track reference.
        return ast.Constant(value=isinstance(expr.ops[0], ast.NotEq))
    return expr


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
    if isinstance(expr, ast.Constant) and isinstance(expr.value, bool):
        # A compile-time-resolved leaf, e.g. from _fold_constants() --
        # never present in a raw, unfolded condition string.
        return True
    if isinstance(expr, (ast.Name, ast.Attribute)):
        # A bare flag (`if s_readpoem:`, `not y_ranaway`) -- treated as
        # `var != 0`, the same truthiness a real Python `if` would use for a
        # story-tracking int/bool. Just as common in real conditions as an
        # explicit comparison (e.g. poemresponses.rpy's own item guards).
        return _ident_name(expr) is not None
    if isinstance(expr, ast.Compare) and len(expr.ops) == 1 and len(expr.comparators) == 1:
        if _CMP_MAP.get(type(expr.ops[0])) is None:
            return False
        if _randint_call(expr.left) is not None:
            # `renpy.random.randint(a, b) == N` -- see _randint_call().
            return _const_int(expr.comparators[0]) is not None
        # _const_scalar, not _const_int: a string comparand is fine, it just
        # becomes an interned id at emit time (see Compiler._const_operand).
        return (_ident_name(expr.left) is not None
                and _const_scalar(expr.comparators[0]) is not None)
    return False
