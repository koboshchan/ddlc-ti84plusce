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
# import_game.py's MAXVARSIZE (65000), but the real binding constraint is
# runtime RAM, not AppVar size -- and the actual number is much smaller than
# it looks.
#
# assets_load_chunk() mallocs one contiguous buffer sized to hold the whole
# chunk (code + string pool) at once. The heap it mallocs from is bounded by
# the Makefile's BSSHEAP_HIGH -- NOT the calculator's whole ~150KB of usable
# RAM (that figure includes graphx's own separately-mapped draw buffer,
# which never touches this heap at all). Confirmed empirically: with the
# stock CEdev BSSHEAP_HIGH, this program's own .bss (string-pool tables,
# static scratch buffers, the vn VM struct -- see src/vn.h) already eats
# ~52KB of a ~59KB combined bss+heap region, leaving only ~7KB of real free
# heap -- nowhere near enough for a chunk anywhere near the size this budget
# used to allow (measured up to 30KB per chunk at a 24000-byte budget, since
# a split is only allowed at a top-level Label boundary and the budget is
# checked before crossing one, so real chunk sizes routinely overshoot it).
# See the Makefile's own BSSHEAP_HIGH comment for the other half of this fix
# (reclaiming spare stack space into the heap) -- the two have to be tuned
# together. Even with that heap enlarged to ~29.5KB, a single chunk still
# has to share it with whatever's concurrently allocated (sprite/scene
# decompression scratch, persistent-save buffers, ...), so the real per-
# chunk budget needs to sit well under the heap ceiling, not right at it.
#
# 12000 still wasn't low enough in practice: a scene with a real 12.6KB
# resident chunk plus one further sprite decompress (a body atom well under
# half the ~31.5KB heap on its own) still failed to malloc(), confirmed
# live and reproduced deterministically -- so whatever headroom "well under
# half the heap" bought wasn't the whole story; treat the true per-chunk
# budget as needing to leave *most* of the heap free, not just half.
CHUNK_SIZE_BUDGET = 8000


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
    time_anchor_var: str | None = None   # see _match_absolute_pause's own
    time_anchor_ms: int = 0              # comment -- credits.rpyc's `pause(
                                          # TARGET - (datetime.datetime.now()
                                          # - VAR).total_seconds())` idiom
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
    # The background id actually in effect on screen, tracked alongside
    # _pending_scene/last_sprite so a `hide white`/`hide black` (see
    # _emit_Hide) knows what to restore -- real Ren'Py's `show white` /
    # `hide white` is a full-screen flash *overlay* on a layer above the
    # scene, not a real background change, but this engine has no overlay
    # concept, so _emit_Show's bare-tag fallback (below) has to fake it by
    # actually swapping the background out and back.
    _current_bg: int | None = field(default=None, init=False)
    _flash_tag: str | None = field(default=None, init=False)   # 'white'/'black' currently "up", or None
    _pre_flash_bg: int | None = field(default=None, init=False)  # _current_bg from just before it went up
    # Characters actually on screen right now (a Show adds, a real Hide
    # removes, any real background change clears it) -- separate from
    # last_sprite/last_pos/last_flags, which remember a character's *last*
    # pose even after they've been hidden, so a flash restore (_emit_Hide)
    # knows who to actually redraw instead of resurrecting someone the
    # script hid earlier in the same scene.
    visible_chars: set = field(default_factory=set, init=False)
    _gensym_counter: itertools.count = field(default_factory=itertools.count, init=False)
    # variable name -> (prefix, chapter, separator) for a Python Assign
    # this compiler recognized as DDLC's own poem-winner dispatch idiom
    # (`nextscene = poemwinner[N] + "_exclusive_" + str(eval(...))`) but
    # deferred instead of emitting -- the real bytecode is a compile-time
    # enumerable dispatch, only buildable once the matching dynamic Call is
    # reached. See _match_poemwinner_dispatch()/_emit_poemwinner_dispatch().
    _pending_dispatch: dict = field(default_factory=dict, init=False)

    # Global counter behind new_chunk_id() -- see that method's own comment.
    _next_chunk_id: int = field(default=0, init=False)
    # Chunks created by a mid-label split (_emit_Label's own budget check,
    # not compile_file_chunked()'s top-level one) -- compile_file_chunked()
    # collects these into its own return value after each file. See
    # _split_label_block()'s comment for why label bodies need their own
    # split point separate from the file-level one.
    _extra_chunks: list = field(default_factory=list, init=False)

    def new_chunk_id(self) -> int:
        """The next globally-unique chunk_id, shared by every place a new
        Assembler gets created (do_compile()'s one per file, and any
        mid-file/mid-label budget split) -- a single counter instead of
        each call site computing its own from a local list length, so a
        split inside a label's body (see _split_label_block()) can't
        collide with one between top-level labels or between files.
        """
        cid = self._next_chunk_id
        self._next_chunk_id += 1
        return cid

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
        grows past @budget bytes (code + string pool). Assumes self.asm is
        already the first chunk to write into (matching compile_file()'s
        calling convention in do_compile()); @chunk_id_start is unused now
        (kept for call-site compatibility) -- every new chunk, here and in
        _split_label_block() below, gets its id from the shared
        new_chunk_id() counter instead, so the two splitters can't collide.

        Splits right before a top-level Label -- the one place DDLC's own
        file structure treats as a safe jump target: sequential top-level
        labels already fall through into each other with no explicit Jump
        between them, so inserting one at a chosen split point is
        behaviorally identical to the fall-through it replaces, not a
        semantic change. A label whose own body alone exceeds the budget
        (common enough in practice to matter, not just a theoretical case)
        needs a second, finer-grained split *inside* that body -- see
        _emit_Label's call to _split_label_block().
        """
        _, top = load_rpyc(path)
        assemblers = [self.asm]

        for node in top:
            is_label = kind(node) == "Label" and isinstance(node.name, str)
            size = len(self.asm.code) + sum(len(s.encode("utf-8")) for s in self.asm.strings)
            if is_label and size > budget:
                self._flush_pending_scene(vnasm.TRANS_CUT)
                self.asm.jump(node.name)
                self.asm = vnasm.Assembler(chunk_id=self.new_chunk_id())
                assemblers.append(self.asm)
            self.emit_node(node, path.name)
            if self._extra_chunks:
                assemblers.extend(self._extra_chunks)
                self._extra_chunks = []

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
        # s_kill_early's own real setter is `if persistent.playthrough == 0:
        # try: renpy.file("../characters/sayori.chr") except: s_kill_early =
        # True` (splash.rpyc) -- a bare-file-existence check via try/except,
        # which this compiler doesn't walk (no exception-handling support at
        # all). Aliased to the SAME slot as persistent.deleted_sayori
        # instead of left unset: this engine already tracks "has Sayori's
        # character file been deleted" for real (see DELETED_VARS/#41's own
        # delete_character() mechanic), which is exactly what the real
        # try/except is checking for, just through a filesystem probe this
        # platform has no equivalent of. Every real reference to
        # s_kill_early -- both this dead setter (harmlessly skipped, not
        # aliased away) and the live `if s_kill_early:` gate in
        # splashscreen's own body -- reads/writes through this one slot, so
        # the alternate CG sequence it gates correctly triggers exactly when
        # Sayori's file is actually gone.
        if name == "s_kill_early":
            name = self.DELETED_VARS[TAG_TO_CHAR["sayori"]]
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
            self._current_bg = self._pending_scene
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
            # DDLC's real `label splashscreen` (splash.rpyc) runs all the
            # way through the title screen's own entrance (`Show intro`)
            # and a disclaimer variant (`Show splash_warning "[...]"`) --
            # both already implemented separately, and better, by
            # src/main.c: render_title_screen() (its own dedicated
            # pal_title palette, DDLC's real entrance timing) and
            # run_splash_screens() respectively. Compiling those here too
            # would draw the same content a second time under the *wrong*
            # palette (the shared game one, since assets_use_title_palette()
            # hasn't run yet at this point) -- confirmed via a real
            # playtest report as the cause of a garbled-looking screen
            # appearing right before the title screen actually loads.
            #
            # Everything *before* `Show intro` is real, non-duplicated
            # content this compiler already handles correctly (the
            # tos/tos2 content-warning screens with their real narrated
            # text, the age/consent Menu, the ghost-menu and s_kill_early
            # checks) -- stop emitting as soon as the walk reaches it
            # rather than skipping the label's body wholesale.
            prefix = []
            for stmt in node.block or []:
                if (kind(stmt) == "Show" and stmt.imspec and stmt.imspec[0] == ("intro",)):
                    break
                prefix.append(stmt)
            self.emit_block(prefix, fname)
            self.asm.ret()
            return

        self._split_label_block(node.block, fname)

    def _split_label_block(self, nodes, fname: str,
                           budget: int = CHUNK_SIZE_BUDGET) -> None:
        """Like emit_block(), but for a label's own direct body -- splits
        into a new chunk if the body alone grows past @budget bytes, same
        reasoning as compile_file_chunked()'s top-level split (see its own
        comment): two sequential sibling statements at the same block level
        already execute in order with no explicit Jump between them, so
        replacing that implicit fall-through with an explicit one plus a
        synthetic label is behaviorally identical, not a semantic change.

        Needed because compile_file_chunked() alone only splits *between*
        top-level Labels -- a single label whose own body exceeds the
        budget (DDLC has several long unbroken narration/dialogue runs
        that do) would otherwise still produce one oversized chunk no
        matter how low that budget is set.

        Only ever splits between two of the label's own direct top-level
        statements -- never inside a nested If/Menu/etc., which isn't a
        valid split point (its own control flow assumes falling straight
        back out to this block, not into an unrelated Jump target). New
        chunks land in self._extra_chunks for compile_file_chunked() to
        collect after this label returns.
        """
        for node in nodes or []:
            size = len(self.asm.code) + sum(len(s.encode("utf-8")) for s in self.asm.strings)
            if size > budget:
                self._flush_pending_scene(vnasm.TRANS_CUT)
                split_label = self._gensym("chunksplit")
                self.asm.jump(split_label)
                self.asm = vnasm.Assembler(chunk_id=self.new_chunk_id())
                self.asm.label(split_label)
                self._extra_chunks.append(self.asm)
            self.emit_node(node, fname)

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
                self.visible_chars.add(char)

        self.asm.say(speaker, text)

    # The real default warning DDLC's splash.rpyc shows over `splash_warning`
    # (its own `splash_message_default` literal, confirmed by decompiling
    # splash.rpyc's Init block) -- see _emit_Show's "splash_warning" case.
    _SPLASH_MESSAGE_DEFAULT = ("This game is not suitable for children\n"
                               "or those who are easily disturbed.")

    def _emit_Show(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        imgname = tuple(node.imspec[0]) if node.imspec and node.imspec[0] else None
        if not imgname:
            self._skip(node, fname, "Show with empty imspec")
            return

        if len(imgname) == 1:
            credits_cg = _match_credits_cg_tag(imgname[0])
            if credits_cg is not None:
                imgname = (credits_cg,)

        if imgname[0] == "splash_warning":
            # DDLC's own `show splash_warning "..."` idiom (splash.rpyc,
            # script-poemresponses2.rpyc) -- a centered warning-style text
            # overlay, not a real image at all (Ren'Py ParameterizedText,
            # no art file behind it, which is why this needs its own case
            # rather than falling into the generic scene_id() path below).
            # A literal caption (e.g. "Just Monika.") compiles straight to
            # narration like any other line. The one dynamic case
            # (splash.rpyc's own "[splash_message]", a runtime pick across
            # 11 corrupted variants + a default -- see splash.rpyc's own
            # Init block) simplifies to always showing the real default
            # warning text instead: every OP_SAY text operand is a
            # compile-time-literal index, and this value is picked at
            # runtime, so reproducing the random pick faithfully would need
            # a new opcode for a rarely-seen (1-in-4, Act 2+ only) cosmetic
            # line -- not worth it next to this project's existing
            # precedent for simplifying comparably low-value cosmetic
            # content (the tear effect, dropped title-screen zoom).
            text_expr = (_parse_condition_expr(imgname[1]) if len(imgname) > 1
                        and isinstance(imgname[1], str) else None)
            if not (isinstance(text_expr, ast.Constant) and isinstance(text_expr.value, str)):
                self._skip(node, fname, f"unsupported splash_warning text {imgname!r}")
                return
            text = (self._SPLASH_MESSAGE_DEFAULT if text_expr.value == "[splash_message]"
                   else text_expr.value)
            self.asm.narrate(_strip_text_tags(text))
            return

        if imgname[0] in ("credits_header", "credits_text") and len(imgname) > 1:
            # credits.rpyc's own `show credits_header "..."`/`show
            # credits_text "..."` idiom -- same ParameterizedText reasoning
            # as splash_warning above, always a literal caption in every
            # real use (unlike splash_warning's one dynamic case).
            text_expr = (_parse_condition_expr(imgname[1])
                        if isinstance(imgname[1], str) else None)
            if isinstance(text_expr, ast.Constant) and isinstance(text_expr.value, str):
                self.asm.narrate(_strip_text_tags(text_expr.value))
                return
            self._skip(node, fname, f"unsupported {imgname[0]} text {imgname!r}")
            return

        text_literal = _text_call_literal(imgname[0]) if len(imgname) == 1 else None
        if text_literal is not None:
            # DDLC's own `show Text("...")` idiom -- an anonymous displayable
            # used directly as a Show target (s_kill_early's own "Now
            # everyone can be happy." closing line, splash.rpyc lines
            # ~360-379), same reasoning as "splash_warning" above: no art
            # file, so it can't go through scene_id() below. Always a
            # literal string in the one real use found, unlike
            # splash_warning's one dynamic case.
            self.asm.narrate(_strip_text_tags(text_literal))
            return

        if len(imgname) == 1:
            defn = self.resolver.table.get(imgname)
            if defn is not None and defn.kind == "text":
                # The named-image form of the same idiom just above --
                # `image fake_exception = Text("...")` then `show
                # fake_exception` (s_kill_early's fake crash-screen scare,
                # script-ch5.rpyc's fake_exception/fake_exception2). Reached
                # through resolver.table instead of _text_call_literal()
                # since imgname[0] here is the symbolic tag, not the literal
                # Text(...) call source -- see image_resolve.py's
                # _resolve_source() for where this ImageDef comes from.
                self.asm.narrate(_strip_text_tags(defn.text))
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
            variants = self.resolver.condswitch_variants(imgname) if len(imgname) == 1 else None
            if variants is not None:
                # Act 3's y_kill: DDLC picks the portrait at runtime off a
                # ConditionSwitch ladder (persistent.yuri_kill's corruption
                # tier), not one fixed image -- see
                # _emit_condswitch_dispatch().
                if not self._emit_condswitch_dispatch(variants):
                    self._skip(node, fname, f"unsupported condswitch condition in {imgname!r}")
                    return
                self.last_sprite.clear()
                self.last_pos.clear()
                self.last_flags.clear()
                self.visible_chars.clear()
                self._current_bg = None  # runtime-picked; no single compile-time id to remember
                self._flash_tag = None
                self._pre_flash_bg = None
                return

            scene_id = self.resolver.scene_id(imgname)
            if scene_id is None:
                self._skip(node, fname, f"unknown character tag in {imgname!r}")
                return
            # 'white'/'black' are DDLC's own full-screen flash idiom (real
            # Ren'Py shows them on a layer above the scene, then a later
            # `hide white`/`hide black` removes just the overlay) -- since
            # this engine has no overlay layer, faking the flash means
            # actually swapping the background out here and remembering
            # what it was, so _emit_Hide can swap it back.
            #
            # Every bare tag gets this tracked now, not just white/black --
            # an earlier version assumed any other bare tag (`end`,
            # poem_specialN, ...) was a real terminal image with no
            # matching Hide expected, but that's false for at least 4 real
            # sites found live (ch22's `y_glitch_head`/`blood_eye`/
            # `blood_eye2`, poemresponses2's `darkred`): DDLC uses this
            # same overlay-flash idiom for other full-screen effects too,
            # not just white/black specifically, and the old assumption
            # left their `hide` calls skipped ("unknown character tag"),
            # which would have stuck the scene on that image forever with
            # no way back -- the exact same bug class the white/black fix
            # addressed, just for tags that fix didn't cover. If a bare
            # tag genuinely has no matching Hide (a real terminal image),
            # this bookkeeping just sits unused until the next real scene
            # change resets it (_emit_Scene) -- harmless either way.
            self._flash_tag = imgname[0]
            self._pre_flash_bg = self._current_bg
            self.asm.scene(scene_id, vnasm.TRANS_CUT)
            self._current_bg = scene_id
            self.last_sprite.clear()
            self.last_pos.clear()
            self.last_flags.clear()
            self.visible_chars.clear()
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
        self.visible_chars.add(char)

    def _emit_Hide(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        imgname = node.imspec[0] if node.imspec else None
        char = TAG_TO_CHAR.get(imgname[0]) if imgname else None
        if char is None:
            tag = imgname[0] if imgname else None
            # The other half of _emit_Show's white/black flash fake: this
            # Hide is the real Ren'Py signal that the overlay comes back
            # down, so undo the background swap now, then replay a Show for
            # every character still on screen -- OP_SCENE (which restoring
            # the background needs) clears the VM's whole actor list, so
            # without this they'd vanish even though they were never really
            # hidden (real Ren'Py never touched the scene layer they're on).
            if tag is not None and tag == self._flash_tag and self._pre_flash_bg is not None:
                self.asm.scene(self._pre_flash_bg, vnasm.TRANS_CUT)
                self._current_bg = self._pre_flash_bg
                for c in sorted(self.visible_chars):
                    # .get(), not [] -- a bare `show natsuki` with no prior
                    # sprite tracked (see the "defaulted to 0" skip above)
                    # marks a character visible without ever populating
                    # last_sprite for them.
                    base, overlay = self.last_sprite.get(c, (0, None))
                    self.asm.show(c, base, overlay, self.last_pos.get(c, vnasm.POS_CENTER),
                                  flags=self.last_flags.get(c, 0))
                self._flash_tag = None
                self._pre_flash_bg = None
                return
            self._skip(node, fname, f"unknown character tag in Hide {imgname!r}")
            return
        self.asm.hide(char)
        self.visible_chars.discard(char)

    def _emit_Scene(self, node, fname: str) -> None:
        self._flush_pending_scene(vnasm.TRANS_CUT)
        imgname = tuple(node.imspec[0]) if node.imspec and node.imspec[0] else None
        if not imgname:
            self._skip(node, fname, "Scene with empty imspec")
            return

        # A top-level-ATL-RawChoice-backed background (e.g. `bg club_day2`'s
        # real 1-in-6 poster-swap variant, definitions.rpyc -- see
        # ImageResolver.choice_variants()) needs a runtime random pick, not
        # scene_id()'s single compile-time-known id -- emitted immediately
        # (always TRANS_CUT, same simplification _emit_condswitch_dispatch()
        # already makes for its own multi-branch case) rather than going
        # through the deferred _pending_scene/_flush_pending_scene combo
        # every other Scene uses.
        defn = self.resolver.table.get(imgname)
        if defn is not None and defn.kind == "choice":
            scene_ids = self.resolver.choice_variants(imgname)
            if scene_ids is not None:
                self._emit_choice_dispatch(scene_ids)
                self.last_sprite.clear()
                self.last_pos.clear()
                self.last_flags.clear()
                self.visible_chars.clear()
                self._current_bg = None  # runtime-picked; no single compile-time id to remember
                self._flash_tag = None
                self._pre_flash_bg = None
                return
            # Baking a branch failed -- fall through to the normal path
            # below, which hits the same ImageDef and produces an accurate
            # "unresolved" skip instead of silently dropping the statement.

        scene_id = self.resolver.scene_id(imgname)
        if scene_id is None:
            self._skip(node, fname, f"unresolved scene image {imgname!r}")
            return
        # A real `scene` statement is an unambiguous "here's the new
        # background", so it supersedes any white/black flash bookkeeping
        # still pending -- a `hide white` reached after this should not try
        # to restore a background that a real Scene already replaced.
        self._flash_tag = None
        self._pre_flash_bg = None
        self._pending_scene = scene_id
        self.last_sprite.clear()  # OP_SCENE clears all actors in the VM too
        self.last_pos.clear()
        self.last_flags.clear()
        self.visible_chars.clear()

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
            dispatch = (self._pending_dispatch.pop(node.label, None)
                       if isinstance(node.label, str) else None)
            if dispatch is not None:
                if dispatch[0] == "poemwinner":
                    self._emit_poemwinner_dispatch(dispatch[1:], fname)
                else:
                    self._emit_chapter_opinion_dispatch(dispatch[1:], fname)
                return
            special_var = (_match_special_poem_call(node.label)
                          if isinstance(node.label, str) else None)
            if special_var is not None:
                self._emit_special_poem_dispatch(special_var)
                return
            prefixed = (_match_prefixed_int_call(node.label)
                       if isinstance(node.label, str) else None)
            if prefixed is not None:
                prefix, prefixed_var = prefixed
                if prefix == "ch30_" and prefixed_var == "persistent.current_monikatopic":
                    self._emit_monikatopic_dispatch(prefixed_var)
                    return
                if prefix == "ch30_reload_" and prefixed_var == "persistent.monika_reload":
                    self._emit_monika_reload_dispatch(prefixed_var)
                    return
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
            winner_slot = self._var_slot(f"poemwinner[{ch}]")
            self.asm.minigame(winner_slot,
                             self._var_slot(f"s_poemappeal[{ch}]"),
                             self._var_slot(f"n_poemappeal[{ch}]"),
                             self._var_slot(f"y_poemappeal[{ch}]"))
            # DDLC's real `label poem:` body -- never walked, since every
            # resolvable call site is inlined here instead (see above) --
            # does `exec(poemwinner[chapter][0] + "_appeal += 1")`
            # immediately after computing the winner: a plain per-character
            # win counter (s_appeal/n_appeal/y_appeal, confirmed by
            # decompiling script-poemgame.rpyc), read downstream by the
            # poem-winner exclusive-scene dispatch (see
            # _match_poemwinner_dispatch/_emit_poemwinner_dispatch). Since
            # the inlined OP_MINIGAME already IS that "just won" moment,
            # replicating the increment here is exact, not approximated --
            # it's the same event, just not reached by walking the body it
            # lives in.
            self._emit_appeal_increment(winner_slot)
            self._emit_eyes_check()
            return
        params = self.label_params.get(node.label)
        if params:
            self._emit_call_args(node, params, fname)
        self.asm.call(node.label)

    # TAG_TO_CHAR order (0 sayori, 1 natsuki, 2 yuri) -- monika can't win
    # the poem minigame under this engine's scoring (see "Poem minigame" in
    # FORMAT.md), so there's no m_appeal counterpart to maintain here.
    _APPEAL_VARS = ("s_appeal", "n_appeal", "y_appeal")
    _WINNER_TAGS = ("sayori", "natsuki", "yuri")

    def _emit_appeal_increment(self, winner_slot: int) -> None:
        end_label = self._gensym("appeal_end")
        for winner_id, appeal_name in enumerate(self._APPEAL_VARS):
            next_label = self._gensym("appeal_next")
            match_label = self._gensym("appeal_match")
            self.asm.if_(winner_slot, vnasm.CMP_EQ, winner_id, match_label)
            self.asm.jump(next_label)
            self.asm.label(match_label)
            self.asm.add(self._var_slot(appeal_name), 1)
            self.asm.jump(end_label)
            self.asm.label(next_label)
        self.asm.label(end_label)

    # Monika's eyes: DDLC's own real gate, verbatim -- reused here as
    # literal Python source rather than hand-rolled AST, so it goes through
    # the exact same _condition_supported()/_emit_condition() machinery any
    # ordinary compiled `if` does (persistent.playthrough == 2 is a plain
    # var==literal leaf, persistent.seen_eyes == None folds to == 0 via
    # _const_scalar's None handling, and the randint() call is the existing
    # leaf case -- nothing new needed at the condition-compiler level).
    _EYES_CONDITION = ("persistent.playthrough == 2 and persistent.seen_eyes == None"
                       " and renpy.random.randint(0,5) == 0")

    def _emit_eyes_check(self) -> None:
        """Relocates the Monika's-eyes jump-scare out of `label poem:`'s own
        body (script-poemgame.rpyc lines ~318-345) to every inlined `call
        poem` site, the same relocation `_emit_appeal_increment` already
        does for the win-count increment right above this call -- that body
        is unreachable by construction once every real call site is inlined
        (see _emit_Call's "poem" case), so its jump-scare and reaction logic
        has to live at the call sites instead.

        The condition is checked at runtime exactly like the real game
        checks it (see _EYES_CONDITION) -- it's never true outside
        `persistent.playthrough == 2` (Act 2+), so this is a correct no-op
        at every other inlined call site rather than something that needs
        gating on which chapter/context reached it.
        """
        expr = _fold_constants(_parse_condition_expr(self._EYES_CONDITION))
        true_label = self._gensym("eyes_branch")
        end_label = self._gensym("eyes_end")
        self._emit_condition(expr, true_label, end_label)
        self.asm.label(true_label)
        self.asm.sound(0)
        self.asm.set(self._var_slot("persistent.seen_eyes"), 1)
        self._flush_pending_scene(vnasm.TRANS_CUT)
        black = self.resolver.scene_id(("black",))
        eyes = self.resolver.scene_id(("bg", "eyes"))
        eyes_move = self.resolver.scene_id(("bg", "eyes_move"))
        # Real sequence (script-poemgame.rpyc): scene black, show eyes_move
        # (1.2s), show eyes (0.5s), show eyes_move (1.25s) -- each Show of a
        # bare background tag is its own OP_SCENE here (see _emit_Show's
        # same reasoning for a bare non-character Show), so the Hides in
        # between need no bytecode of their own.
        if black is not None:
            self.asm.scene(black, vnasm.TRANS_CUT)
        if eyes_move is not None:
            self.asm.scene(eyes_move, vnasm.TRANS_CUT)
        self.asm.pause(1200)
        if eyes is not None:
            self.asm.scene(eyes, vnasm.TRANS_CUT)
        self.asm.pause(500)
        if eyes_move is not None:
            self.asm.scene(eyes_move, vnasm.TRANS_CUT)
        self.asm.pause(1250)
        self.last_sprite.clear()
        self.last_pos.clear()
        self.last_flags.clear()
        self.visible_chars.clear()
        self._current_bg = eyes_move if eyes_move is not None else self._current_bg
        self._flash_tag = None
        self._pre_flash_bg = None
        self.asm.label(end_label)

    def _emit_poemwinner_dispatch(self, dispatch: tuple, fname: str) -> None:
        """The real bytecode for a recognized poem-winner dispatch (see
        _match_poemwinner_dispatch) -- a compile-time enumerable N-way
        (character) x M-way (win count) dispatch to real, statically known
        labels (e.g. "sayori_exclusive_1"), instead of constructing the
        target name at runtime the way DDLC's own script does (something
        this VM's variables, plain int16s, can't represent -- there's no
        way to hold "sayori" distinctly from the number 0). A combination
        that isn't a real compiled label (an appeal count higher than DDLC
        ever actually authored a variant for) falls through to the existing
        "call to a label outside the compiled set" stub -- the same safe,
        silent degrade every other unresolvable target in this compiler
        already gets, not a new failure mode.
        """
        prefix, chapter, sep = dispatch
        winner_slot = self._var_slot(f"poemwinner[{chapter}]")
        end_label = self._gensym("poemwinner_end")
        for winner_id, (tag, appeal_name) in enumerate(zip(self._WINNER_TAGS, self._APPEAL_VARS)):
            appeal_slot = self._var_slot(appeal_name)
            char_next = self._gensym("poemwinner_next")
            char_match = self._gensym("poemwinner_match")
            self.asm.if_(winner_slot, vnasm.CMP_EQ, winner_id, char_match)
            self.asm.jump(char_next)
            self.asm.label(char_match)
            # DDLC only ever authored up to 3 win-count variants for any
            # character (confirmed: m_sayori_1/2/3, the highest-numbered
            # real labels found using this pattern) -- trying a fixed 1..3
            # regardless of which prefix/chapter this call site uses is
            # simpler than tracking each site's real bound, and costs
            # nothing when a given (character, count) combination isn't a
            # real label: it just stubs, like any other missing target.
            for appeal in range(1, 4):
                appeal_next = self._gensym("poemwinner_appeal_next")
                appeal_match = self._gensym("poemwinner_appeal_match")
                self.asm.if_(appeal_slot, vnasm.CMP_EQ, appeal, appeal_match)
                self.asm.jump(appeal_next)
                self.asm.label(appeal_match)
                self.asm.call(f"{prefix}{tag}{sep}{appeal}")
                self.asm.jump(end_label)
                self.asm.label(appeal_next)
            self.asm.jump(end_label)
            self.asm.label(char_next)
        self.asm.label(end_label)

    # bad/med/good -- confirmed the only 3 values script-poemresponses.rpyc's
    # own poemopinion variable is ever assigned (a literal "med" up front,
    # then optionally overwritten by the BASE[chapter-1] comparison -- see
    # _chapter_indexed_base/_emit_chapter_condition).
    _OPINIONS = ("bad", "med", "good")

    def _emit_chapter_opinion_dispatch(self, dispatch: tuple, fname: str) -> None:
        """The real bytecode for a recognized chapter-opinion dispatch (see
        _match_chapter_dispatch) -- same reasoning as
        _emit_poemwinner_dispatch: `chapter` (1..3) and `poemopinion`
        (bad/med/good, already a real runtime string by this point --
        poemopinion's own literal assignments compile normally, nothing
        special needed there) are both small and enumerable, so this
        dispatches straight to the real target label (e.g. "ch2_n_good")
        instead of ever constructing the name at runtime.
        """
        suffix, opinion_var = dispatch
        chapter_slot = self._var_slot("chapter")
        end_label = self._gensym("chopinion_end")
        for n in (1, 2, 3):
            ch_next = self._gensym("chopinion_next")
            ch_match = self._gensym("chopinion_match")
            self.asm.if_(chapter_slot, vnasm.CMP_EQ, n, ch_match)
            self.asm.jump(ch_next)
            self.asm.label(ch_match)
            if opinion_var is None:
                self.asm.call(f"ch{n}{suffix}")
                self.asm.jump(end_label)
            else:
                opinion_slot = self._var_slot(opinion_var)
                for opinion in self._OPINIONS:
                    op_next = self._gensym("chopinion_op_next")
                    op_match = self._gensym("chopinion_op_match")
                    self.asm.if_(opinion_slot, vnasm.CMP_EQ, self._intern(opinion), op_match)
                    self.asm.jump(op_next)
                    self.asm.label(op_match)
                    self.asm.call(f"ch{n}{suffix}{opinion}")
                    self.asm.jump(end_label)
                    self.asm.label(op_next)
                self.asm.jump(end_label)
            self.asm.label(ch_next)
        self.asm.label(end_label)

    # DDLC's real 11 special poems (poems_special.rpyc), each a one-shot
    # picture-book page. Real gate: `persistent.special_poems`, 3 slots
    # picked once (splashscreen.rpyc's own reject-and-remove loop over
    # 1..11, unreachable -- see #57) and read back at 3 real call sites
    # (script-ch20/22/23.rpyc, one slot each) via
    # `Call("poem_special_" + str(persistent.special_poems[N]))`.
    _SPECIAL_POEM_COUNT = 11

    def _emit_special_poem_init(self) -> None:
        """Ensures persistent.special_poems[0..2] hold 3 distinct values in
        1..11 before any dispatch reads them -- DDLC's own reject-and-remove
        pick, reproduced as a real backward-jumping retry loop in emitted
        bytecode (this VM has no loop construct in its *input* language, but
        nothing stops hand-emitted bytecode from looping). Gated on slot 0's
        own value (0 meaning "never rolled", since a real pick is always
        1..11) so it only actually runs once per save -- persistent.*
        survives New Game, matching the real game picking these once and
        keeping them.

        OP_IF only compares a variable against a compile-time literal, never
        another variable, so distinctness can't be checked as "does slot 1
        equal slot 0" directly. Instead each retry dispatches on the fresh
        draw across all 11 possible literal values (mirroring
        _emit_poemwinner_dispatch's own enumerable-dispatch shape) and, once
        inside the branch for a specific literal k, checks each
        already-picked slot against that same literal k -- a var-vs-literal
        comparison the VM does support.
        """
        slot0 = self._var_slot("persistent.special_poems[0]")
        slot1 = self._var_slot("persistent.special_poems[1]")
        slot2 = self._var_slot("persistent.special_poems[2]")
        rand = self._var_slot(self.RAND_SCRATCH)
        done = self._gensym("special_poems_init_done")

        self.asm.if_(slot0, vnasm.CMP_NE, 0, done)
        self.asm.random(slot0, 1, self._SPECIAL_POEM_COUNT)

        for slot, avoid in ((slot1, (slot0,)), (slot2, (slot0, slot1))):
            retry = self._gensym("special_poems_retry")
            slot_done = self._gensym("special_poems_slot_done")
            self.asm.label(retry)
            self.asm.random(rand, 1, self._SPECIAL_POEM_COUNT)
            for k in range(1, self._SPECIAL_POEM_COUNT + 1):
                match = self._gensym("special_poems_match")
                nxt = self._gensym("special_poems_next")
                self.asm.if_(rand, vnasm.CMP_EQ, k, match)
                self.asm.jump(nxt)
                self.asm.label(match)
                for other in avoid:
                    self.asm.if_(other, vnasm.CMP_EQ, k, retry)
                self.asm.set(slot, k)
                self.asm.jump(slot_done)
                self.asm.label(nxt)
            self.asm.label(slot_done)

        self.asm.label(done)

    def _emit_special_poem_dispatch(self, varname: str) -> None:
        """The real bytecode for a recognized special-poem dispatch (see
        _match_special_poem_call) -- an enumerable 11-way dispatch straight
        to the real poem_special_K label, same reasoning as
        _emit_poemwinner_dispatch."""
        self._emit_special_poem_init()
        slot = self._var_slot(varname)
        end_label = self._gensym("special_poem_end")
        for k in range(1, self._SPECIAL_POEM_COUNT + 1):
            match = self._gensym("special_poem_match")
            nxt = self._gensym("special_poem_next")
            self.asm.if_(slot, vnasm.CMP_EQ, k, match)
            self.asm.jump(nxt)
            self.asm.label(match)
            self.asm.call(f"poem_special_{k}")
            self.asm.jump(end_label)
            self.asm.label(nxt)
        self.asm.label(end_label)

    # Act 3's day-topic loop (script-ch30.rpyc). Real DDLC picks
    # persistent.current_monikatopic without replacement from a shrinking
    # bag (persistent.monikatopics, refilled from range(1,57) minus
    # {14,25,26} -- no ch30_14/25/26 label exists at all -- and minus 27
    # while persistent.seen_colors_poem is unset) via a real Python list.
    # This VM has no list/array structure and OP_IF can't compare two
    # variables, so an exact reproduction (a used-bitmask plus bitwise ops)
    # would need real new opcodes for what's ultimately a "no immediate
    # repeat" polish detail. Simplified to a uniform pick *with*
    # replacement every visit instead (_emit_monikatopic_init, the same
    # reject-and-retry bytecode loop as _emit_special_poem_init) --
    # excludes 27 unconditionally rather than tracking
    # persistent.seen_colors_poem's own trigger, one topic out of 56 traded
    # away for not needing a second gate. Every other real day-topic body
    # is still reachable, just not guaranteed non-repeating.
    MONIKATOPIC_VALUES = tuple(n for n in range(1, 57) if n not in (14, 25, 26, 27))
    MONIKA_RELOAD_VALUES = (0, 1, 2, 3, 4)

    def _emit_monikatopic_init(self) -> None:
        slot = self._var_slot("persistent.current_monikatopic")
        rand = self._var_slot(self.RAND_SCRATCH)
        retry = self._gensym("monikatopic_retry")
        done = self._gensym("monikatopic_done")
        self.asm.label(retry)
        self.asm.random(rand, 1, 56)
        for k in self.MONIKATOPIC_VALUES:
            match = self._gensym("monikatopic_match")
            nxt = self._gensym("monikatopic_next")
            self.asm.if_(rand, vnasm.CMP_EQ, k, match)
            self.asm.jump(nxt)
            self.asm.label(match)
            self.asm.set(slot, k)
            self.asm.jump(done)
            self.asm.label(nxt)
        self.asm.jump(retry)  # landed on an excluded value (14/25/26/27) -- draw again
        self.asm.label(done)

    def _emit_monikatopic_dispatch(self, varname: str) -> None:
        """The real bytecode for `Call("ch30_" +
        str(persistent.current_monikatopic))` -- draws a fresh topic (see
        _emit_monikatopic_init) and dispatches straight to the real
        ch30_<N> label, same enumerable-dispatch shape as
        _emit_special_poem_dispatch."""
        self._emit_monikatopic_init()
        slot = self._var_slot(varname)
        end_label = self._gensym("monikatopic_end")
        for k in self.MONIKATOPIC_VALUES:
            match = self._gensym("monikatopic_match")
            nxt = self._gensym("monikatopic_next")
            self.asm.if_(slot, vnasm.CMP_EQ, k, match)
            self.asm.jump(nxt)
            self.asm.label(match)
            self.asm.call(f"ch30_{k}")
            self.asm.jump(end_label)
            self.asm.label(nxt)
        self.asm.label(end_label)

    def _emit_monika_reload_dispatch(self, varname: str) -> None:
        """`Call("ch30_reload_" + str(persistent.monika_reload))` --
        persistent.monika_reload counts reloads during Act 3's ending
        sequence (already a real, working AugAssign, see #36) and only 5
        real ch30_reload_N labels exist, so anything >= 4 lands on
        ch30_reload_4 -- the same "cap rather than crash" choice
        _emit_chapter_condition makes for chapter outside 1..3."""
        slot = self._var_slot(varname)
        end_label = self._gensym("monika_reload_end")
        for k in self.MONIKA_RELOAD_VALUES[:-1]:
            match = self._gensym("monika_reload_match")
            nxt = self._gensym("monika_reload_next")
            self.asm.if_(slot, vnasm.CMP_EQ, k, match)
            self.asm.jump(nxt)
            self.asm.label(match)
            self.asm.call(f"ch30_reload_{k}")
            self.asm.jump(end_label)
            self.asm.label(nxt)
        self.asm.call(f"ch30_reload_{self.MONIKA_RELOAD_VALUES[-1]}")
        self.asm.label(end_label)

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
        if stripped.startswith("call screen confirm("):
            confirm = _parse_confirm_call(stripped)
            if confirm is not None:
                self._emit_confirm_screen(*confirm)
                return
        if stripped.startswith("call screen dialog("):
            # DDLC's own one-button acknowledgement idiom -- credits.rpyc's
            # postcredits_loop ("Error: Script file is missing or corrupt",
            # ok_action=Quit(...)) is the only site with ok_action=Quit;
            # every other real site (ch40's s_kill_early climax,
            # poemgame.rpyc's instructions, splash.rpyc's skip-button hint)
            # uses ok_action=Return() -- acknowledge and keep playing, not
            # quit -- see _parse_dialog_call.
            dialog = _parse_dialog_call(stripped)
            if dialog is not None:
                message, quits = dialog
                self.asm.narrate(_strip_text_tags(message))
                if quits:
                    self.asm.end()
                return
        if stripped == "pause":
            # Ren'Py's own bare `pause` statement (distinct from DDLC's
            # `pause(seconds)` helper in _emit_python_stmt) -- s_kill_early's
            # own closing beat waits here for a click before renpy.quit().
            # ms=0 is OP_PAUSE's own "wait for a click, no timeout" sentinel,
            # same as a no-args `pause()` call.
            self.asm.pause(0)
            return
        if stripped.startswith("pause "):
            # Ren'Py's own `pause N` statement (a literal duration, unlike
            # the bare `pause` above) -- credits.rpyc's own `pause 9.3`/
            # `pause 41`/`pause 0.5` between the opening beats, before the
            # scrolling credits' own absolute-clock pauses take over (see
            # _match_absolute_pause).
            try:
                seconds = float(stripped[len("pause "):].strip())
            except ValueError:
                seconds = None
            if seconds is not None:
                self.asm.pause(max(1, round(seconds * 1000)))
                return
        self._skip(node, fname, f"unsupported statement: {line!r}")
        self.asm.nop()

    def _emit_confirm_screen(self, message: str, yes_value: bool, no_value: bool) -> None:
        """Compiles DDLC's own `call screen confirm(message, Return(a),
        Return(b))` idiom (see _parse_confirm_call) to a real 2-option
        OP_MENU, writing the player's pick into a "_return" story variable
        the same way Ren'Py's own call-screen mechanism would -- the one
        real gate in front of each of the 11 special poems (see
        _match_special_poem_call). When both buttons carry the same value
        (script-ch23.rpyc's own variant, an empty-message "press any button
        to continue" rather than a real choice), there's nothing to ask --
        this just sets the variable directly instead of showing a
        degenerate 2-option menu whose options don't actually differ."""
        slot = self._var_slot("_return")
        if yes_value == no_value:
            self.asm.set(slot, int(yes_value))
            return
        if message:
            self.asm.narrate(_strip_text_tags(message))
        yes_label = self._gensym("confirm_yes")
        no_label = self._gensym("confirm_no")
        end_label = self._gensym("confirm_end")
        self.asm.menu([("Yes", yes_label), ("No", no_label)])
        self.asm.label(yes_label)
        self.asm.set(slot, int(yes_value))
        self.asm.jump(end_label)
        self.asm.label(no_label)
        self.asm.set(slot, int(no_value))
        self.asm.jump(end_label)
        self.asm.label(end_label)

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
            absolute = _match_absolute_pause(stmt.value)
            if absolute is not None and absolute[1] == self.time_anchor_var:
                # credits.rpyc's own absolute-wall-clock-target pause -- see
                # _match_absolute_pause's own comment. Each one advances the
                # tracked "virtual clock" forward to its own target instead
                # of emitting a raw duration, so the real schedule (each
                # beat landing at a fixed elapsed time, not a fixed gap from
                # the previous one) survives compilation exactly.
                target_ms = round(absolute[0] * 1000)
                delta_ms = max(1, target_ms - self.time_anchor_ms)
                self.asm.pause(delta_ms)
                self.time_anchor_ms = target_ms
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
            if fn == "renpy.quit" and not stmt.value.args:
                # s_kill_early's own closing beat -- OP_END is exactly
                # "finish now, unconditionally", which is what real Ren'Py
                # quitting to the OS means for a player who's just reached
                # the true end of what this compiled build has to show them.
                self.asm.end()
                return True
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            var = _ident_name(stmt.targets[0])
            if var is not None:
                if (isinstance(stmt.value, ast.Call)
                        and _ident_name(stmt.value.func) == "datetime.datetime.now"
                        and not stmt.value.args and not stmt.value.keywords):
                    # `VAR = datetime.datetime.now()` -- resets the virtual
                    # clock _match_absolute_pause's own handling (above,
                    # this same function) measures against. No bytecode:
                    # pure compile-time bookkeeping, the same way
                    # `chapter = N` tracking below needs no runtime read.
                    self.time_anchor_var = var
                    self.time_anchor_ms = 0
                    return True
                glitch_bounds = _match_glitchtext_call(stmt.value)
                if glitch_bounds is not None:
                    # `VAR = glitchtext(...)` -- VAR itself never gets a real
                    # slot: every real use immediately interpolates it into
                    # dialogue as "[gtext]"/"[s_name]"/"[m_name]"/"[ntext]"
                    # and never reads it any other way (confirmed across
                    # every real call site), so there's nothing to name a
                    # variable for -- just refill the one shared buffer.
                    self.asm.glitchtext(*glitch_bounds)
                    return True
                dispatch = _match_poemwinner_dispatch(stmt.value)
                if dispatch is not None:
                    # No bytecode here -- the real dispatch is a compile-time
                    # enumerable N-way (character) x M-way (win count) call,
                    # only buildable once the matching dynamic Call is
                    # reached (see _emit_Call's use of _pending_dispatch).
                    self._pending_dispatch[var] = ("poemwinner",) + dispatch
                    return True
                chapter_dispatch = _match_chapter_dispatch(stmt.value)
                if chapter_dispatch is not None:
                    # Same idea, different idiom -- see
                    # _emit_chapter_opinion_dispatch.
                    self._pending_dispatch[var] = ("chapter_opinion",) + chapter_dispatch
                    return True
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
            base = _chapter_indexed_base(expr.left)
            if base is not None:
                val = self._const_operand(expr.comparators[0])
                self._emit_chapter_condition(base, cmp, val, true_label, false_label)
                return
            slot = self._var_slot(_ident_name(expr.left))
        val = self._const_operand(expr.comparators[0])
        self.asm.if_(slot, cmp, val, true_label)
        self.asm.jump(false_label)

    def _emit_chapter_condition(self, base: str, cmp: int, val: int,
                                true_label: str, false_label: str) -> None:
        """`BASE[chapter - 1] <cmp> val` -- see _chapter_indexed_base's own
        comment for why this needs its own emission instead of a plain
        slot lookup: `chapter` is a runtime variable here (1..3 at these
        call sites), so this dispatches on chapter's real value instead,
        comparing whichever BASE[N-1] slot that value selects.
        """
        chapter_slot = self._var_slot("chapter")
        for n in (1, 2, 3):
            next_label = self._gensym("chcond_next")
            match_label = self._gensym("chcond_match")
            self.asm.if_(chapter_slot, vnasm.CMP_EQ, n, match_label)
            self.asm.jump(next_label)
            self.asm.label(match_label)
            slot = self._var_slot(f"{base}[{n - 1}]")
            self.asm.if_(slot, cmp, val, true_label)
            self.asm.jump(false_label)
            self.asm.label(next_label)
        # chapter outside 1..3 -- shouldn't happen in practice; degrade to
        # false rather than leaving the label chain dangling.
        self.asm.jump(false_label)

    def _emit_condswitch_dispatch(self, variants: list) -> bool:
        """Emits an if/elif/else chain over @p variants (from
        ImageResolver.condswitch_variants(), declared order -- ConditionSwitch
        itself picks the first true condition, so this must too), each
        landing on its own OP_SCENE. Every condition seen in real data so far
        is a single leaf comparison (`persistent.yuri_kill >= N`), handled
        directly with the same _emit_condition() machinery `if` statements
        use, rather than a hand-rolled var-vs-literal loop like
        _emit_chapter_condition's -- unlike chapter dispatch this doesn't
        need to pick a slot dynamically, just evaluate one already-fixed
        comparison per branch. Returns False (nothing emitted) if a
        condition turns out not to be one _condition_supported() approves,
        so the caller can skip cleanly instead of emitting a partial chain.
        """
        parsed = []
        for cond_src, scene_id in variants:
            if cond_src is None:
                parsed.append((None, scene_id))
                continue
            expr = _parse_condition_expr(cond_src)
            if expr is None or not _condition_supported(expr):
                return False
            parsed.append((expr, scene_id))

        end_label = self._gensym("condswitch_end")
        for expr, scene_id in parsed:
            if expr is None:  # the unconditional "True" fallback, always last
                self.asm.scene(scene_id, vnasm.TRANS_CUT)
                self.asm.jump(end_label)
                continue
            match_label = self._gensym("condswitch_match")
            next_label = self._gensym("condswitch_next")
            self._emit_condition(expr, match_label, next_label)
            self.asm.label(match_label)
            self.asm.scene(scene_id, vnasm.TRANS_CUT)
            self.asm.jump(end_label)
            self.asm.label(next_label)
        self.asm.label(end_label)
        return True

    def _emit_choice_dispatch(self, scene_ids: list) -> None:
        """Emits a uniform-random pick among @p scene_ids (from
        ImageResolver.choice_variants(), declared order) -- an ATL
        RawChoice's own real semantics, exact rather than approximated
        whenever every branch is equally weighted (every real case found
        so far -- see _top_level_choice_frames()). Same self.asm.random()
        -plus-enumerable-dispatch shape as _emit_special_poem_init()'s
        draw, just without that one's reject-and-retry distinctness loop:
        each visit here is independent, nothing to avoid repeating.
        """
        rand = self._var_slot(self.RAND_SCRATCH)
        end_label = self._gensym("choice_end")
        self.asm.random(rand, 0, len(scene_ids) - 1)
        for k, scene_id in enumerate(scene_ids):
            match_label = self._gensym("choice_match")
            next_label = self._gensym("choice_next")
            self.asm.if_(rand, vnasm.CMP_EQ, k, match_label)
            self.asm.jump(next_label)
            self.asm.label(match_label)
            self.asm.scene(scene_id, vnasm.TRANS_CUT)
            self.asm.jump(end_label)
            self.asm.label(next_label)
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
            if not isinstance(caption, str):
                continue
            if block:
                rows.append((_strip_text_tags(caption), block))
            else:
                # A caption-only entry (no block at all) -- Ren'Py's own way
                # of attaching narration text to a Menu, e.g. splash.rpyc's
                # age/content-consent screen: a long consent paragraph
                # followed by one real choice, "I agree.". Not a selectable
                # option -- narrate it so it stays on screen instead of
                # silently vanishing (host_menu() shows scene->text behind
                # the choice list, same as any other narration -- see its
                # own comment in main.c).
                self.asm.narrate(_strip_text_tags(caption))

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
    set) is stubbed to a local OP_RETURN in whichever chunk references it
    first, the same "content wasn't imported, resume after it" idea as
    Assembler.patch_missing_labels() (see its own comment on why OP_RETURN,
    not OP_END, is the correct stub -- a Call needs to resume its caller,
    not end the session) -- and that stub is then registered into the
    global table, so any other chunk referencing the same missing name
    correctly cross-chunk-jumps to it instead of getting a second stub.

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
            asm.ret()
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


def _match_poemwinner_dispatch(node) -> tuple[str, int, str] | None:
    """Recognizes DDLC's own poem-winner dispatch idiom -- confirmed in two
    real shapes: `poemwinner[N] + "_exclusive_" + str(eval(poemwinner[N][0]
    + "_appeal"))` (script-ch1/ch2.rpyc, picking the winning character's own
    bonus scene) and `"m_" + poemwinner[N] + "_" + str(eval(...))`
    (script-poemresponses.rpyc, Monika's own reaction) -- a literal prefix,
    `poemwinner[N]` (the winner's tag name), a literal separator, then the
    SAME character's cumulative win count (`s_appeal`/`n_appeal`/`y_appeal`,
    incremented once per real poem game -- see the "poem" Call special case
    in _emit_Call, which replicates the increment DDLC's own `label poem:`
    body does since that body is otherwise never walked).

    Returns (prefix, chapter, separator) if @p node is this shape (any
    literal prefix, including none), else None. Both real values are
    strings this compiler can't represent as-is (a variable can't hold a
    "sayori"-vs-"natsuki" distinction, only the numeric TAG_TO_CHAR winner
    id OP_MINIGAME already stores) -- recognizing the shape at compile time
    sidesteps needing to, by building a small enumerable N-way (character)
    x M-way (win count) dispatch to the real, compile-time-known target
    labels instead of trying to construct the name at runtime.
    """
    if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)):
        return None

    # Flatten the left-associative `a + b + c + ...` chain into source order.
    terms = []
    cur = node
    while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Add):
        terms.append(cur.right)
        cur = cur.left
    terms.append(cur)
    terms.reverse()

    if len(terms) < 3:
        return None
    appeal_term, sep_term, winner_terms = terms[-1], terms[-2], terms[:-2]
    if not (isinstance(sep_term, ast.Constant) and isinstance(sep_term.value, str)):
        return None

    winner_idx = chapter = None
    for i, t in enumerate(winner_terms):
        if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                and t.value.id == "poemwinner" and isinstance(t.slice, ast.Constant)
                and isinstance(t.slice.value, int)):
            winner_idx, chapter = i, t.slice.value
            break
    if winner_idx is None:
        return None

    prefix = ""
    for i, t in enumerate(winner_terms):
        if i == winner_idx:
            continue
        if isinstance(t, ast.Constant) and isinstance(t.value, str):
            prefix += t.value
        else:
            return None   # an extra term this shape doesn't account for

    # str(eval(poemwinner[chapter][0] + "_appeal"))
    if not (isinstance(appeal_term, ast.Call) and isinstance(appeal_term.func, ast.Name)
            and appeal_term.func.id == "str" and len(appeal_term.args) == 1):
        return None
    inner = appeal_term.args[0]
    if not (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
            and inner.func.id == "eval" and len(inner.args) == 1):
        return None
    expr = inner.args[0]
    if not (isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add)
            and isinstance(expr.right, ast.Constant) and expr.right.value == "_appeal"):
        return None
    letter = expr.left
    if not (isinstance(letter, ast.Subscript) and isinstance(letter.slice, ast.Constant)
            and letter.slice.value == 0):
        return None
    base = letter.value
    if not (isinstance(base, ast.Subscript) and isinstance(base.value, ast.Name)
            and base.value.id == "poemwinner" and isinstance(base.slice, ast.Constant)
            and base.slice.value == chapter):
        return None

    return (prefix, chapter, sep_term.value)


def _match_special_poem_call(expr_src: str) -> str | None:
    """Recognizes `"poem_special_" + str(persistent.special_poems[N])` --
    script-ch20/22/23.rpyc's real `call expression` target for DDLC's 11
    optional special poems (poems_special.rpyc). Returns the
    "persistent.special_poems[N]" variable name to dispatch on (see
    _ident_name's Subscript handling, which already renders it exactly that
    way), or None if @p expr_src isn't this shape.
    """
    expr = _parse_condition_expr(expr_src)
    if not (isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add)):
        return None
    if not (isinstance(expr.left, ast.Constant) and expr.left.value == "poem_special_"):
        return None
    call = expr.right
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id == "str" and len(call.args) == 1):
        return None
    var = _ident_name(call.args[0])
    if var is None or not var.startswith("persistent.special_poems["):
        return None
    return var


def _match_prefixed_int_call(expr_src: str) -> tuple[str, str] | None:
    """Recognizes `"PREFIX" + str(VAR)` -- DDLC's other dynamic-call idiom
    besides special-poem's own (_match_special_poem_call): a literal string
    prefix concatenated onto an integer variable's runtime value. Used by
    Act 3's day-topic loop (script-ch30.rpyc: `"ch30_" +
    str(persistent.current_monikatopic)`, `"ch30_reload_" +
    str(persistent.monika_reload)`) -- the exact shape
    _dynamic_target_var's own docstring already named as unattempted.
    Returns (prefix, varname), or None if @p expr_src isn't this shape.
    """
    expr = _parse_condition_expr(expr_src)
    if not (isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add)):
        return None
    if not (isinstance(expr.left, ast.Constant) and isinstance(expr.left.value, str)):
        return None
    call = expr.right
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id == "str" and len(call.args) == 1):
        return None
    var = _ident_name(call.args[0])
    if var is None:
        return None
    return expr.left.value, var


def _match_credits_cg_tag(expr_src: str) -> str | None:
    """Recognizes credits.rpyc's own `("credits_cgN" + lockedtext)` idiom --
    a literal CG name concatenated with a runtime "_locked"/"_clearall"
    suffix tracking Ren'Py's own per-scene completion gallery
    (persistent.clear[]/persistent.clearall, set by `lockedtext = "" if
    persistent.clear[imagenum] else "_locked"` right before each Show).
    This engine has no gallery/completion tracking at all -- reproducing it
    faithfully would mean a new persistent array plus writing to it from
    every real "you've seen this ending" site across the whole game, for a
    credits-screen-only cosmetic. Always resolves to the literal unlocked
    name instead: every credits CG shows in full, never a locked
    silhouette -- a documented simplification, not a missing feature.
    Returns the literal prefix, or None if @p expr_src isn't this shape."""
    expr = _parse_condition_expr(expr_src)
    if not (isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add)):
        return None
    if not (isinstance(expr.left, ast.Constant) and isinstance(expr.left.value, str)
            and expr.left.value.startswith("credits_cg")):
        return None
    if not (isinstance(expr.right, ast.Name) and expr.right.id == "lockedtext"):
        return None
    return expr.left.value


def _match_absolute_pause(call: ast.Call) -> tuple[float, str] | None:
    """Recognizes credits.rpyc's own `pause(TARGET -
    (datetime.datetime.now() - VAR).total_seconds())` idiom -- an
    absolute-wall-clock-target pause used throughout the credits scroll to
    keep each beat landing on a fixed schedule regardless of how long the
    statements before it actually took (VAR is set once by `VAR =
    datetime.datetime.now()` at the top of the sequence -- see
    Compiler._emit_python_stmt's own handling of that assignment).
    Returns (TARGET, VAR), or None if @p call isn't this shape."""
    if len(call.args) != 1 or call.keywords:
        return None
    expr = call.args[0]
    if not (isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Sub)):
        return None
    target = _pure_number(expr.left)
    if target is None:
        return None
    right = expr.right
    if not (isinstance(right, ast.Call) and isinstance(right.func, ast.Attribute)
            and right.func.attr == "total_seconds" and not right.args and not right.keywords):
        return None
    inner = right.func.value
    if not (isinstance(inner, ast.BinOp) and isinstance(inner.op, ast.Sub)):
        return None
    now_call = inner.left
    if not (isinstance(now_call, ast.Call) and _ident_name(now_call.func) == "datetime.datetime.now"
            and not now_call.args and not now_call.keywords):
        return None
    var = _ident_name(inner.right)
    if var is None:
        return None
    return (target, var)


def _chapter_indexed_base(node) -> str | None:
    """Recognizes `BASE[chapter - 1]` -- BASE one of poemwinner/
    s_poemappeal/n_poemappeal/y_poemappeal/m_poemappeal, the arrays the
    chapter-aware poem minigame already indexes by literal chapter number
    (0, 1, 2). `chapter - 1` is the one real shape script-poemresponses.rpyc
    uses to read back what the poem minigame just wrote for whichever
    chapter is currently running: `chapter` is a 1-indexed runtime
    variable (1, 2, 3 at this call site -- script.rpy's label start's own
    chapter=N/Call poemresponse_start sites), unlike every other indexed-
    variable access in this compiler, which needs a compile-time-literal
    index. Returns BASE, or None if @p node isn't this exact shape."""
    if not (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)):
        return None
    idx = node.slice
    if not (isinstance(idx, ast.BinOp) and isinstance(idx.op, ast.Sub)
            and isinstance(idx.left, ast.Name) and idx.left.id == "chapter"
            and isinstance(idx.right, ast.Constant) and idx.right.value == 1):
        return None
    return node.value.id


def _match_chapter_dispatch(node) -> tuple[str, str | None] | None:
    """Recognizes DDLC's `"ch" + pt + str(chapter) + SUFFIX [+ OPINION_VAR]`
    idiom (script-poemresponses.rpyc's per-chapter reaction dispatch, one
    block per character: `poemopinion = "med"; if BASE[chapter-1] < 0:
    poemopinion = "bad" elif BASE[chapter-1] > 0: poemopinion = "good";
    nextscene = "ch" + pt + str(chapter) + "_s_" + poemopinion` for the
    opinion-driven reaction, or `nextscene = "ch" + pt + str(chapter) +
    "_s_end"` for the follow-up with no opinion involved).

    `pt` is dropped entirely -- like poemwinner's own tag name, this
    compiler dispatches straight to the real target label rather than
    ever constructing the name, so `pt`'s own meaning (still untraced)
    doesn't matter here. `chapter` only ever takes 1..3 at these call
    sites, same reasoning as _chapter_indexed_base.

    Returns (suffix, opinion_var_name): opinion_var_name is None for the
    plain "_end"/"_start" shape (a fixed final term, no variable), or the
    variable name for the "_s_" + poemopinion shape.
    """
    if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)):
        return None
    terms = []
    cur = node
    while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Add):
        terms.append(cur.right)
        cur = cur.left
    terms.append(cur)
    terms.reverse()

    if len(terms) not in (4, 5):
        return None
    if not (isinstance(terms[0], ast.Constant) and terms[0].value == "ch"):
        return None
    if not isinstance(terms[1], ast.Name):   # `pt` -- dropped, see above
        return None
    str_chapter = terms[2]
    if not (isinstance(str_chapter, ast.Call) and isinstance(str_chapter.func, ast.Name)
            and str_chapter.func.id == "str" and len(str_chapter.args) == 1
            and isinstance(str_chapter.args[0], ast.Name)
            and str_chapter.args[0].id == "chapter"):
        return None

    if len(terms) == 4:
        suffix = terms[3]
        if isinstance(suffix, ast.Constant) and isinstance(suffix.value, str):
            return (suffix.value, None)
        return None

    suffix, opinion = terms[3], terms[4]
    if (isinstance(suffix, ast.Constant) and isinstance(suffix.value, str)
            and isinstance(opinion, ast.Name)):
        return (suffix.value, opinion.id)
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


def _match_glitchtext_call(node) -> tuple[int, int] | None:
    """(lo, hi) if @p node is `glitchtext(N)` (a literal count -- most real
    calls, e.g. glitchtext(48)) or `glitchtext(renpy.random.randint(lo,
    hi))` (script-ch23.rpyc's two dynamic-length calls), else None. See
    OP_GLITCHTEXT -- both compile to the same lo/hi draw, lo==hi for the
    literal case."""
    if not (isinstance(node, ast.Call) and _ident_name(node.func) == "glitchtext"
            and len(node.args) == 1 and not node.keywords):
        return None
    n = _const_int(node.args[0])
    if n is not None:
        return (n, n)
    return _randint_call(node.args[0])


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


def _parse_confirm_call(stripped: str) -> tuple[str, bool, bool] | None:
    """(message, yes_value, no_value) for DDLC's own `call screen
    confirm(message, Return(a), Return(b))` idiom -- the one real gate in
    front of each of the 11 special poems (script-ch20/22/23.rpyc, right
    before the dynamic Call _match_special_poem_call recognizes). Both real
    call sites confirmed: script-ch20/22.rpyc's real accept-or-decline
    prompt (Return(True), Return(False)) and script-ch23.rpyc's own variant
    with an empty message and the SAME action on both buttons
    (Return(True), Return(True)) -- effectively just "press any button to
    continue" rather than a real yes/no choice, which is why both values
    are returned instead of assuming True/False, letting
    _emit_confirm_screen degrade that case to an unconditional set instead
    of a real menu. None if @p stripped isn't this shape at all. @p stripped
    still has its `call screen ` prefix; only the `confirm(...)` call
    itself is parsed.
    """
    prefix = "call screen confirm("
    if not stripped.startswith(prefix) or not stripped.endswith(")"):
        return None
    try:
        call = ast.parse(f"confirm({stripped[len(prefix):-1]})", mode="eval").body
    except SyntaxError:
        return None
    if not (isinstance(call, ast.Call) and len(call.args) == 3):
        return None
    message, yes_action, no_action = call.args
    if not (isinstance(message, ast.Constant) and isinstance(message.value, str)):
        return None

    def _return_value(node):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "Return" and len(node.args) == 1
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, bool)):
            return node.args[0].value
        return None

    yes_value, no_value = _return_value(yes_action), _return_value(no_action)
    if yes_value is None or no_value is None:
        return None
    return (message.value, yes_value, no_value)


def _parse_dialog_call(stripped: str) -> tuple[str, bool] | None:
    """(message, quits) for DDLC's own `call screen dialog(...)` one-button
    acknowledgement idiom. The message is either a `message="..."` keyword
    (credits.rpyc's postcredits_loop, its only use) or the first positional
    argument (every other real site: ch40's s_kill_early climax,
    poemgame.rpyc's instructions, splash.rpyc's skip-button hint). @p quits
    is True only for `ok_action=Quit(...)` (credits.rpyc again) -- every
    other site uses `ok_action=Return()`, an acknowledge-and-keep-playing
    that must NOT end the script; conflating the two used to silently drop
    ch40's climactic dialogue entirely (this whole idiom fell through to
    "unsupported statement" whenever the message was positional, and even
    a matched Return() site would have wrongly ended the story). None if
    @p stripped isn't this shape. @p stripped still has its `call screen `
    prefix; only the `dialog(...)` call itself is parsed."""
    prefix = "call screen "
    if not stripped.startswith(prefix):
        return None
    try:
        call = ast.parse(stripped[len(prefix):], mode="eval").body
    except SyntaxError:
        return None
    if not (isinstance(call, ast.Call) and _ident_name(call.func) == "dialog"):
        return None
    message = None
    for kw in call.keywords:
        if (kw.arg == "message" and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)):
            message = kw.value.value
    if message is None and call.args and isinstance(call.args[0], ast.Constant) \
            and isinstance(call.args[0].value, str):
        message = call.args[0].value
    if message is None:
        return None
    quits = any(kw.arg == "ok_action" and isinstance(kw.value, ast.Call)
                and _ident_name(kw.value.func) == "Quit"
                for kw in call.keywords)
    return message, quits


def _text_call_literal(src: str) -> str | None:
    """The literal message from DDLC's own `Text("...", style="...")` idiom,
    used as a bare Show target (see _emit_Show). Returns None if @p src
    isn't a `Text(...)` call with a literal string as its first argument."""
    try:
        node = ast.parse(src, mode="eval").body
    except SyntaxError:
        return None
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "Text" and node.args
            and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
        return None
    return node.args[0].value


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
        if _chapter_indexed_base(expr.left) is not None:
            # `BASE[chapter - 1] <cmp> N` -- see _chapter_indexed_base().
            return _const_int(expr.comparators[0]) is not None
        # _const_scalar, not _const_int: a string comparand is fine, it just
        # becomes an interned id at emit time (see Compiler._const_operand).
        return (_ident_name(expr.left) is not None
                and _const_scalar(expr.comparators[0]) is not None)
    return False
