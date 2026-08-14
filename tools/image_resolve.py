"""Resolve Ren'Py symbolic image names to pixels.

Show/Scene AST nodes reference symbolic names like ('sayori', '4p') or
('bg', 'residential_day'), not filenames. The mapping lives in `image`
statement declarations (renpy.ast.Image nodes), whose `code` field is a
PyExpr (Python source text, see rpyc_ast.py) for one of:

  - a plain string literal            -> one art file, e.g. "bg/residential.png"
  - a "#RRGGBB" string literal        -> a solid color, no art file at all
  - im.Composite((w,h), (x,y), path, (x2,y2), path2, ...) -> a layered sprite
  - an ATL RawBlock (CGs use this for crossfade animation) -> take the first
    quoted-string literal as the static resting frame; the engine has no
    animated-background support to lose

Source expressions are parsed with Python's `ast` module and structurally
matched -- never eval()/exec()'d.

Because composite layer counts vary (2-3+ observed) rather than being a
fixed body+face split, sprites are pre-baked here with Pillow: composited
onto their declared canvas, cropped to content, and scaled down to one flat
PNG. This is what let OP_SHOW drop its runtime `face` operand (docs/FORMAT.md).

Resolution is on-demand (sprite_id()/scene_id()), not eager, so only the
~239 combos actually referenced by the imported chapters get baked -- not
the full cartesian product of bodies x faces.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image as PILImage

from rpyc_ast import flatten, kind, load_rpyc, pycode_source

# Files known to declare `image` statements. Scanning every .rpyc for Image
# nodes (cheap; they're plain dict lookups) is more robust than hardcoding
# this list, so build_image_table() does that instead of trusting it -- kept
# here only as documentation of where definitions actually live.
_KNOWN_IMAGE_FILES = ("definitions.rpyc", "cgs.rpyc", "poems.rpyc", "effects.rpyc")

_HEX_COLOR = re.compile(r"^#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")

# DDLC's real character scale, not a tuned one. Every character transform in
# transforms.rpy (`tcommon`/`focus`/`hop`/`sink`, all of them) takes `z=0.80`
# and applies `zoom z*1.00`, so a sprite's 960x960 canvas is drawn at 768x768
# on Ren'Py's 1280x720 screen. That screen maps to this engine's scene area at
# 0.25 (BG_SIZE below, 1280x720 -> 320x180), so canvas px reach the calculator
# at 0.80 * 0.25.
#
# This is applied as a *fixed* scale, deliberately. An earlier version instead
# normalized every character to one on-screen height (176px, the scene area
# less a margin), which silently threw away DDLC's real height differences --
# Natsuki's art is 781 canvas px tall against Yuri's 899, so normalizing drew
# them the same height and lined all four heads up along the top of the
# screen. It also had to guess an anchor, and put every character ~5px too
# high and up to ~10px off horizontally (see _bake_layer_atom).
SPRITE_SCALE = 0.80 * 0.25
# Half the real scene area (320x180) on each axis -- 4x fewer raw pixels to
# store/compress. src/assets.c's assets_scene() decompresses into this same
# small size, then 2x nearest-neighbor upscales into the real 320x180 draw
# buffer at read time, so render.c and everything above it still sees an
# ordinary full-size scene; only the archived source resolution actually
# drops. A real, deliberate quality tradeoff (chosen over trimming credits
# content or splitting the bundle) to close the archive-budget overage
# credits.rpyc's own new art pushed the full-game build over.
BG_SIZE = (160, 90)
POEM_BG_SIZE = (320, 240)   # full screen -- the poem minigame has no dialogue
                            # box reserving the bottom 60px, unlike an
                            # ordinary VN scene (see poem_background())

# The dialogue box's own screen real estate (SCREEN_W x (SCREEN_H-SCENE_H),
# src/render.h) -- 320px wide alone is already over gfx_rletsprite_t's
# uint8_t width field (255px max), so textbox/namebox art can't go through
# the ordinary RLET sprite pipeline the way a character atom does (see
# ui_box_art()). 320 wide would also be an unusually large single sprite to
# scale/composite per-frame even if the dimension cap didn't rule it out.
TEXTBOX_SIZE = (320, 60)
# DDLC's own namebox.png is 168x39 (~4.3:1) -- sized independently here
# (not derived from TEXTBOX_SIZE's own scale factor) to a close aspect
# match that fits comfortably in the box's top-left corner alongside it.
NAMEBOX_SIZE = (72, 17)
CG_SIZE = BG_SIZE  # same half-resolution treatment -- see BG_SIZE's own comment

# --- title screen ------------------------------------------------------------
#
# DDLC's title screen is authored on its own 1280x720 canvas and shares no art
# with the dialogue sprites -- it has dedicated `gui/menu_art_*.png` files, a
# logo bitmap, a nav panel, and a tiling background (see docs/FORMAT.md).
#
# TITLE_SCALE maps that canvas onto the calculator uniformly: x 1280 -> 320
# exactly, with no cropping and no stretching. The screen is 4:3 rather than
# 16:9, so the extra vertical room simply reveals *more* of each character --
# their source art is 1080 tall and already cut off by DDLC's own frame bottom,
# so showing further down is what a taller frame should do.
TITLE_SCALE = 0.25
TITLE_SCREEN = (320, 240)

# DDLC's frame is 16:9 and the calculator's is 4:3, so a width-matching 0.25
# scale leaves 60 rows spare. Those rows go *above* the cast: the girls are
# composed standing on the frame's bottom edge, so anchoring them downward
# keeps their feet at the screen bottom instead of leaving them floating over
# a strip of empty background. The logo and nav panel are anchored to the
# screen instead (they're UI, not art) and don't take this offset.
CAST_DY = TITLE_SCREEN[1] - int(720 * TITLE_SCALE)   # 60

# The logo is scaled to the *screen*, not the art: at a proportional
# 0.25 it would be 77px across and the wordmark inside it unreadable.
LOGO_PX = 104
LOGO_POS = (8, -12)   # top-clipped, matching DDLC's own ~11% crop of the badge

# gfx_rletsprite_t stores width/height as uint8_t, so nothing may bake taller
# or wider than this. Monika lands at 270px before cropping -- see title_art().
SPRITE_MAX_DIM = 255

# gui/menu_bg.png is a heart lattice that repeats exactly (verified: zero pixel
# difference at both a 200px horizontal and a 200px vertical shift), which is
# what makes the scrolling background affordable at all: instead of a 320x240
# screen-sized image (76,800 bytes -- over the 65,535-byte AppVar ceiling), one
# period is baked and repeated into a short strip. 200 source px scales to
# exactly 50 calculator px, so the repeat stays seamless. The strip is one tile
# wider than the screen so any horizontal scroll offset of 0..TILE_W-1 is still
# one flat memcpy per row; the vertical wrap is a modulo on the source row
# (src/assets.c's assets_title_bg).
BG_TILE_PERIOD = 200                              # source px, one full repeat
BG_TILE_W = int(BG_TILE_PERIOD * TITLE_SCALE)     # 50
BG_STRIP_SIZE = (TITLE_SCREEN[0] + BG_TILE_W, BG_TILE_W)      # 370x50


# --- image definition table --------------------------------------------------

@dataclass
class Layer:
    x: int
    y: int
    path: str


@dataclass
class ImageDef:
    imgname: tuple
    kind: str                      # 'solid' | 'path' | 'composite' | 'condswitch' | 'unsupported'
    color: Optional[tuple] = None  # (r,g,b) for 'solid'
    path: Optional[str] = None     # for 'path'
    canvas: Optional[tuple] = None # (w,h) for 'composite'
    layers: list = field(default_factory=list)  # [Layer, ...] for 'composite'
    reason: Optional[str] = None   # for 'unsupported'
    variants: list = field(default_factory=list)  # [(cond_src, path), ...] for 'condswitch',
                                                    # declared order (first true wins, matching
                                                    # ConditionSwitch's own semantics) -- see
                                                    # compile_script.py's _emit_condswitch_dispatch


def _find_path_in_call(node: ast.Call, depth: int = 0) -> Optional[str]:
    """Recursively searches @p node's positional args for the first string
    constant that looks like a real image path (has an image extension),
    descending into nested Calls (im.Crop(...), im.FactorScale(...), etc)
    -- see _first_atl_string's own comment on why this is worth doing
    (Monika's face-pixelation effect nests a real path two Call levels
    deep). depth guards against unreasonable recursion; real ATL call
    trees are never more than 2-3 levels deep."""
    if depth > 6:
        return None
    for arg in node.args:
        if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                and _IMAGE_EXT.search(arg.value)):
            return arg.value
        if isinstance(arg, ast.Call):
            found = _find_path_in_call(arg, depth + 1)
            if found is not None:
                return found
    return None


def _first_atl_string(atl_node) -> Optional[str]:
    """Pull the first quoted-string literal out of an ATL RawBlock (CG/bg frames).

    Blocks can nest: a RawChoice (random alternatives, e.g. `bg club_day2`'s
    weighted variants) holds `.choices = [(weight, RawBlock), ...]`, and a
    RawParallel holds multiple concurrent RawBlocks. Since the engine has no
    animation/randomization to lose, we deterministically take the first
    branch of either rather than trying to preserve the choice.
    """
    statements = getattr(atl_node, "statements", None)
    if not isinstance(statements, list):
        return None

    for stmt in statements:
        k = kind(stmt)

        if k == "RawMultipurpose":
            for expr in stmt.expressions or []:
                src = expr[0] if isinstance(expr, tuple) else expr
                if not isinstance(src, str):
                    continue
                try:
                    node = ast.parse(src, mode="eval").body
                except SyntaxError:
                    continue
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and not _HEX_COLOR.match(node.value)):
                    # Skip a bare hex color rather than returning it as if it
                    # were a real frame -- confirmed real, not hypothetical:
                    # natsuki_ghost_blood's own ATL starts with a fully
                    # transparent "#00000000" (an ImageDissolve's start
                    # state) *before* its real static frame
                    # ("natsuki/ghost_blood.png", a real shipped file) a
                    # couple statements later in the same block. Returning
                    # early on the color would resolve to a nonexistent
                    # "#00000000" file instead of the real picture one
                    # statement further on.
                    return node.value
                # `.sm`/`.dm` (Ren'Py's displayable-manager accessors) wrap
                # the real Call one Attribute level up -- confirmed real:
                # `RectStatic(...).sm`, `Blood(...).sm`, `RectCluster(...)
                # .sm` all parse as Attribute(Call(...), 'sm'), not a bare
                # Call, so unwrap it before searching args.
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Call):
                    node = node.value
                if isinstance(node, ast.Call) and node.args:
                    # A displayable call referencing a real image path
                    # somewhere in its arguments, e.g. Act 3's veins effect
                    # -- AnimatedMask("images/bg/veinmask.png", "mask",
                    # ...) -- or, nested a level deeper, Monika's own
                    # face-pixelation effect -- RectStatic(im.FactorScale(
                    # im.Crop("gui/logo.png", (100,100,128,128)), 0.25), 2,
                    # 32, 32) -- the crop/scale/pixelate steps aren't
                    # reproducible, but the underlying source picture is a
                    # reasonable static stand-in (uncropped, since this
                    # engine has no crop-region concept in an ImageDef) --
                    # same "drop the transform, keep the real picture" call
                    # as the zoom/dizzy cases below.
                    found = _find_path_in_call(node)
                    if found is not None:
                        return found

        elif k == "RawChoice" and stmt.choices:
            _weight, block = stmt.choices[0]
            found = _first_atl_string(block)
            if found is not None:
                return found

        elif k == "RawParallel" and getattr(stmt, "blocks", None):
            found = _first_atl_string(stmt.blocks[0])
            if found is not None:
                return found

        elif k in ("RawBlock", "RawChild"):
            # RawBlock: a bare nested block, e.g. monika g2's animation wraps
            # a RawChoice one level deeper than club_day2's did. RawChild:
            # ATL's `contains` form (e.g. Act 3's s_kill_zoom family, a real
            # dizzy/zoom wrapper one level around the actual picture, itself
            # referenced by name rather than inline) -- its payload lives in
            # .children instead of .statements. Recurse either way rather
            # than assuming one fixed nesting shape; a symbolic name this
            # returns (not a literal path) is resolved against the rest of
            # the image table later, in ImageResolver._render_ref().
            children = getattr(stmt, "children", None)
            block = children[0] if k == "RawChild" and children else stmt
            found = _first_atl_string(block)
            if found is not None:
                return found

    return None


def _parse_composite(call: ast.Call) -> Optional[tuple]:
    """Match im.Composite((w,h), (x,y), "path", ...). Returns (canvas, layers) or None."""
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name not in ("Composite", "LiveComposite"):
        return None
    if len(call.args) < 3:
        return None

    def as_const(node):
        return node.value if isinstance(node, ast.Constant) else None

    canvas_node = call.args[0]
    if not isinstance(canvas_node, ast.Tuple) or len(canvas_node.elts) != 2:
        return None
    w, h = as_const(canvas_node.elts[0]), as_const(canvas_node.elts[1])
    if not isinstance(w, int) or not isinstance(h, int):
        return None

    layers = []
    rest = call.args[1:]
    for i in range(0, len(rest) - 1, 2):
        off_node, path_node = rest[i], rest[i + 1]
        if not isinstance(off_node, ast.Tuple) or len(off_node.elts) != 2:
            return None
        x, y = as_const(off_node.elts[0]), as_const(off_node.elts[1])
        path = as_const(path_node)
        if not isinstance(x, int) or not isinstance(y, int) or not isinstance(path, str):
            return None
        layers.append(Layer(x=x, y=y, path=path))

    if not layers:
        return None
    return (w, h), layers


def _resolve_source(imgname: tuple, src: str) -> ImageDef:
    try:
        node = ast.parse(src, mode="eval").body
    except SyntaxError as e:
        return ImageDef(imgname, "unsupported", reason=f"unparseable expression: {e}")

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        value = node.value
        if _HEX_COLOR.match(value):
            hex_digits = value[1:]
            if len(hex_digits) == 3:
                hex_digits = "".join(c * 2 for c in hex_digits)
            # An 8-digit form (#RRGGBBAA, e.g. Act 3's "dark"/"darkred"
            # dimming overlays) carries an alpha byte this engine's blits
            # can't use -- render.c has no per-pixel alpha compositing (see
            # render.h's file comment). Drop it and bake the color fully
            # opaque rather than reject the whole definition: a documented
            # fidelity loss (a translucent tint becomes a solid one), not a
            # missing image.
            rgb = tuple(int(hex_digits[i:i + 2], 16) for i in (0, 2, 4))
            return ImageDef(imgname, "solid", color=rgb)
        return ImageDef(imgname, "path", path=value)

    if isinstance(node, ast.Call):
        parsed = _parse_composite(node)
        if parsed is not None:
            canvas, layers = parsed
            return ImageDef(imgname, "composite", canvas=canvas, layers=layers)

        # `Image("path.png")` -- Ren'Py's explicit constructor for a plain
        # image, functionally identical to just writing the path string
        # (`image X = "path.png"`) for anything that isn't animated. Found
        # in DDLC's own definitions.rpy for a couple of the Act 2 "ghost"
        # sprites, written this way rather than as a bare string for no
        # reason that matters to how it renders.
        func = node.func
        fname = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if (fname in ("Image", "LiveTile") and len(node.args) >= 1 and not node.keywords
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            # Image("path.png") -- Ren'Py's explicit constructor for a plain
            # image, functionally identical to just writing the path string
            # (`image X = "path.png"`) for anything that isn't animated.
            # Found in DDLC's own definitions.rpy for a couple of the Act 2
            # "ghost" sprites, written this way rather than as a bare string
            # for no reason that matters to how it renders.
            #
            # LiveTile("path.jpg", ...) -- Act 2's `bg glitch` effect
            # (definitions.rpyc), a real background tiled/re-randomized live
            # every frame. The tiling itself isn't reproducible, but the
            # source picture makes a perfectly good static "glitched"
            # background on its own -- same "drop the transform, keep the
            # real picture" reasoning as _find_path_in_call's ATL cases.
            return ImageDef(imgname, "path", path=node.args[0].value)

        if (fname == "ConditionSwitch" and not node.keywords and len(node.args) >= 2
                and len(node.args) % 2 == 0
                and all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in node.args)):
            # Act 3's y_kill portrait: a threshold ladder over
            # persistent.yuri_kill, e.g. ConditionSwitch("persistent.yuri_kill
            # >= 1380", "images/cg/y_kill/3a.png", ..., "True",
            # "images/cg/y_kill/1a.png"). Ren'Py evaluates these in
            # declaration order and uses the first true condition -- kept as
            # a raw (condition source, path) list rather than parsed here so
            # compile_script.py's own condition machinery (already handling
            # comparisons like this for `if` statements) does the parsing,
            # not a second copy of it.
            values = [a.value for a in node.args]
            variants = list(zip(values[0::2], values[1::2]))
            return ImageDef(imgname, "condswitch", variants=variants)

        return ImageDef(imgname, "unsupported", reason=f"unsupported call: {ast.dump(node)[:80]}")

    return ImageDef(imgname, "unsupported", reason=f"unsupported expression: {type(node).__name__}")


def build_image_table(raw_dir: Path) -> dict:
    """Scan every extracted .rpyc for Image nodes and resolve each to an ImageDef."""
    table: dict = {}

    for path in sorted(raw_dir.glob("*.rpyc")):
        try:
            _, top = load_rpyc(path)
        except Exception:
            continue  # not a script container (e.g. a non-.rpyc extra file)

        for node in flatten(top):
            if kind(node) != "Image":
                continue
            imgname = node.imgname

            code = getattr(node, "code", None)
            atl = getattr(node, "atl", None)

            if code is not None:
                src = pycode_source(code)
                table[imgname] = (_resolve_source(imgname, src) if src
                                   else ImageDef(imgname, "unsupported", reason="no source captured"))
            elif atl is not None:
                frame = _first_atl_string(atl)
                table[imgname] = (ImageDef(imgname, "path", path=frame) if frame
                                   else ImageDef(imgname, "unsupported", reason="no static frame in ATL block"))
            else:
                table[imgname] = ImageDef(imgname, "unsupported", reason="no code or atl")

    # Ren'Py ships a couple of built-in images no project ever has to
    # declare itself -- "black"/"white" (plain Solid colors) are the two
    # DDLC's own content leans on (splash.rpyc's ghost menu: `show black`).
    # No Image node for either exists in any of DDLC's own .rpyc files (they
    # really are framework-internal, not project content) -- confirmed by
    # this same scan finding neither. setdefault() rather than an
    # unconditional write, so a real project definition (not the case
    # today, but not assumed impossible) would still take precedence.
    for name, rgb in (("black", (0, 0, 0)), ("white", (255, 255, 255))):
        table.setdefault((name,), ImageDef((name,), "solid", color=rgb))

    return table


# --- rendering ----------------------------------------------------------------

_IMAGE_EXT = re.compile(r"\.(png|jpg|jpeg)$", re.IGNORECASE)


_UNSAFE_FILENAME_CHARS = re.compile(r'[^A-Za-z0-9_.-]+')


def _safe_filename_part(imgname: tuple) -> str:
    """A filesystem- and convimg-yaml-safe fragment for @p imgname, used to
    build a human-readable baked filename. Most imgnames are already plain
    identifiers, but a direct `show "gui/x.png"` reference's own tag is the
    literal quoted Python source text (quotes and all -- see
    _quoted_literal_path) -- baking that in verbatim once produced a
    filename like `cg_073_"gui-poem_dismiss.png".png`, which convimg's own
    output naming doesn't preserve byte-for-byte the same way this code's
    own `canvas.save()` does, so the two sides silently disagreed on what
    the packaged .bin should be called. Stripping everything outside
    `[A-Za-z0-9_.-]` avoids relying on convimg's specific quoting/escaping
    rules matching this code's own -- the safest guarantee is not needing
    either side to handle those characters at all.

    Also strips a trailing image extension (.png/.jpg/.jpeg) from @p imgname
    itself before the caller appends its own real one -- a direct file
    reference's tag already ends in one (the path literal), so without this
    the result was a double extension (`....png.png`), which convimg's own
    output naming collapses differently than tools/import_game.py's
    packaging step expects (confirmed: a second, different real crash after
    the quoting one above was fixed, same root cause -- the two tools
    computing the .bin's name two different ways from an ambiguous .png.png
    input).
    """
    name = _UNSAFE_FILENAME_CHARS.sub("-", "_".join(imgname)).strip("-")
    return _IMAGE_EXT.sub("", name)


def _quoted_literal_path(s: str) -> Optional[str]:
    """The unquoted path from Ren'Py's `show "gui/x.png"` idiom -- a direct
    file reference with no `image` statement behind it at all (confirmed
    real: splash.rpyc's ghost-menu easter egg does `show
    "gui/menu_art_m_ghost.png"`). Ren'Py stores the imspec tag as the
    literal Python source text for this case (quotes included), distinct
    from a plain symbolic tag name like `sayori`. Returns the inner path,
    or None if @p s isn't a quoted string literal.
    """
    try:
        node = ast.parse(s, mode="eval").body
    except SyntaxError:
        return None
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_file_path(s: str) -> bool:
    """Distinguish a real art file path from a symbolic image-name reference.

    Composite layers and ATL frames can name *another declared image*
    instead of a file -- confirmed in real data: `im.Composite(..., (0, 0),
    "n_rects_mouth", ...)` and an ATL frame `"bg club_day"` both reference
    other `image` statements, not files on disk. Real paths always carry an
    image extension; symbolic references never do.
    """
    return bool(_IMAGE_EXT.search(s))


def _find_art_file(raw_dir: Path, rel_path: str) -> Optional[Path]:
    """Ren'Py resolves bare paths like "bg/x.png" against an implicit images/
    search directory, but definitions also reference "images/cg/x.png"
    directly -- try both rather than hardcoding the convention."""
    candidates = [raw_dir / rel_path, raw_dir / "images" / rel_path]
    for c in candidates:
        if c.is_file():
            return c
    return None


class ImageResolver:
    def __init__(self, raw_dir: Path, build_dir: Path):
        self.raw_dir = raw_dir
        self.build_dir = build_dir
        self.img_dir = build_dir / "img"
        self.img_dir.mkdir(parents=True, exist_ok=True)

        self.table = build_image_table(raw_dir)

        self._sprite_ids: dict = {}
        self._scene_ids: dict = {}
        self._scene_content: dict = {}  # (pixel-content hash, fit) -> existing scene id, see _bake_flat
        self._sprite_content: dict = {}  # pixel-content hash -> existing sprite id, see _bake_sprite
        self._layer_result: dict = {}   # imgname -> (base_id, overlay_id) -- sprite_layers()
        self._layer_ids: dict = {}      # dedup key -> sprite id -- see _bake_layer_atom()

        self.sprites: list = []      # manifest rows, index == id
        self.title_items: list = [] # title screen art, index == id (see title_art())
        self.title_bg: dict = {}     # the scrolling background strip (see title_bg_strip())
        self.poem_bg: dict = {}      # the poem minigame's notebook (see poem_background())
        self.textbox: dict = {}      # the real dialogue box art (see ui_box_art())
        self.namebox: dict = {}      # the real speaker namebox art (see ui_box_art())
        # backgrounds and CGs share ONE id space here: OP_SCENE has a single
        # `bg:u8` operand with no room for a bucket discriminator, so the
        # engine (Milestone 3) tells them apart by each entry's "palette"
        # field ("shared" -> pal_game, "own" -> swap in a per-CG palette)
        # rather than by a split id range.
        self.scenes: list = []
        self.unsupported: list = []  # (imgname, reason)
        self._own_scene_count = 0

    # -- public resolution API, called from compile_script.py --------------

    def sprite_id(self, imgname: tuple) -> Optional[int]:
        if imgname in self._sprite_ids:
            return self._sprite_ids[imgname]
        entry = self._bake_sprite(imgname)
        if entry is None:
            return None
        if isinstance(entry, int):
            # _bake_sprite found this content already baked under a
            # different tag -- see _bake_flat's own comment, same reasoning.
            self._sprite_ids[imgname] = entry
            return entry
        idx = len(self.sprites)
        self._sprite_content[entry.pop("_content_hash")] = idx
        self.sprites.append(entry)
        self._sprite_ids[imgname] = idx
        return idx

    def sprite_layers(self, imgname: tuple) -> tuple:
        """Resolve @p imgname to (base_id, overlay_id) -- overlay_id is None
        when the result is a single flattened bake (the old sprite_id()
        behavior).

        Most DDLC character sprites are `Composite((w,h), (0,0), body,
        (0,0), body, (0,0), expression)`: the ~10 body layers and ~30
        expression layers a character has combine multiplicatively into
        300+ Show-able combos, and flattening each combo into its own full
        bake ships the same body pixels over and over (see docs/FORMAT.md's
        "Layered sprites"). When every layer sits at the composite's (0, 0)
        origin -- true for the large majority, confirmed by direct
        measurement -- this bakes each distinct body ("base": every layer
        but the last) and expression ("overlay": the last layer alone) atom
        exactly once instead, and src/render.c draws them stacked at
        runtime. Anything that doesn't fit that shape (a non-composite
        image, a single-layer composite, or any layer positioned off
        (0, 0) -- a handful of accessory overlays are) falls back to one
        full flattened bake, unchanged from before.
        """
        if imgname in self._layer_result:
            return self._layer_result[imgname]

        result = self._resolve_layers(imgname)
        self._layer_result[imgname] = result
        return result

    def _resolve_layers(self, imgname: tuple) -> tuple:
        defn = self.table.get(imgname)
        if defn is None:
            self._log_unsupported(imgname, "no Image definition found")
            return (None, None)

        simple = (defn.kind == "composite" and len(defn.layers) >= 2 and
                  all(layer.x == 0 and layer.y == 0 for layer in defn.layers))
        if not simple:
            base = self.sprite_id(imgname)
            return (base, None)

        char = imgname[0]
        base_key = (char, tuple(layer.path for layer in defn.layers[:-1]))
        overlay_key = (char, defn.layers[-1].path)

        base_canvas = PILImage.new("RGBA", defn.canvas, (0, 0, 0, 0))
        for layer in defn.layers[:-1]:
            img = self._render_ref(layer.path)
            if img is None:
                self._log_unsupported(imgname, f"could not render layer {layer.path!r}")
                return (None, None)
            base_canvas.alpha_composite(img, (layer.x, layer.y))

        overlay_img = self._render_ref(defn.layers[-1].path)
        if overlay_img is None:
            self._log_unsupported(imgname, f"could not render layer {defn.layers[-1].path!r}")
            return (None, None)
        overlay_canvas = PILImage.new("RGBA", defn.canvas, (0, 0, 0, 0))
        overlay_canvas.alpha_composite(overlay_img, (0, 0))

        base_id = self._bake_layer_atom(base_key, base_canvas, char)
        overlay_id = self._bake_layer_atom(overlay_key, overlay_canvas, char)
        if base_id is None and overlay_id is None:
            self._log_unsupported(imgname, "fully transparent composite")
            return (None, None)
        if base_id is None:
            # Body layers alone happened to be empty for this combo (no
            # observed case in the real game, but cheap to handle) -- the
            # overlay is the only visible content, so ship it as the sole
            # (primary) sprite rather than dropping the Show entirely.
            return (overlay_id, None)

        return (base_id, overlay_id)

    def _canvas_offsets(self, canvas_size: tuple, bbox: tuple,
                        size: tuple) -> tuple:
        """The (dx, dy) that makes src/assets.c's `center_x - w/2 + dx,
        feet_y - h + dy` anchor math place a cropped, scaled atom exactly
        where it sits inside its own (scaled) composite canvas.

        Everything is measured against the *canvas*, not against the
        character's content box, because that is what Ren'Py positions:
        `xcenter` centers the displayable -- the full 960-wide canvas --
        and `yanchor 1.0` pins its bottom edge, both regardless of where
        the drawn pixels happen to fall inside it. Measuring from content
        instead (as this used to) drifts by however much the art is
        off-center in its canvas, which for these sprites runs to ~50
        canvas px horizontally, and re-derives a different anchor for every
        character.

        It also means atoms of one character need no shared per-character
        reference to line up: two atoms cropped out of the same canvas land
        correctly relative to each other because both are placed relative
        to that canvas.
        """
        cw = max(1, round(canvas_size[0] * SPRITE_SCALE))
        ch = max(1, round(canvas_size[1] * SPRITE_SCALE))
        dx = -(cw // 2) + round(bbox[0] * SPRITE_SCALE) + size[0] // 2
        dy = -ch + round(bbox[1] * SPRITE_SCALE) + size[1]
        return (dx, dy)

    def _bake_layer_atom(self, key: tuple, canvas: "PILImage.Image", char: str) -> Optional[int]:
        """Crop @p canvas (a full composite-canvas-sized render of just one
        atom -- a base's flattened layers, or one overlay layer alone) to
        its own content, scale it by DDLC's real SPRITE_SCALE, and register
        it as a sprite with a baked-in (dx, dy) draw offset -- derived here
        so the on-calc side needs no per-character state, just a per-sprite
        (dx, dy) it adds unconditionally.

        Returns None if @p canvas is fully transparent (e.g. an overlay
        layer that happens to be blank for this combo).
        """
        if key in self._layer_ids:
            return self._layer_ids[key]

        bbox = canvas.getbbox()
        if bbox is None:
            return None

        cropped = canvas.crop(bbox)
        size = (max(1, round(cropped.width * SPRITE_SCALE)),
                max(1, round(cropped.height * SPRITE_SCALE)))
        if size[0] > SPRITE_MAX_DIM or size[1] > SPRITE_MAX_DIM:
            return None
        scaled = cropped.resize(size, PILImage.LANCZOS)
        dx, dy = self._canvas_offsets(canvas.size, bbox, size)

        paths = key[1]
        flat_paths = paths if isinstance(paths, tuple) else (paths,)
        name = char + "_" + "-".join(Path(p).stem for p in flat_paths)
        filename = f"sprite_{len(self.sprites):03d}_{name}.png"
        scaled.save(self.img_dir / filename)

        idx = len(self.sprites)
        self.sprites.append({"imgname": [char, name], "file": filename,
                             "w": size[0], "h": size[1], "dx": dx, "dy": dy})
        self._layer_ids[key] = idx
        return idx

    def scene_id(self, imgname: tuple) -> Optional[int]:
        if imgname in self._scene_ids:
            return self._scene_ids[imgname]

        # `show "gui/x.png"` -- a direct file reference with no `image`
        # statement to look up in self.table at all (see
        # _quoted_literal_path). Synthesized once, then falls through the
        # normal lookup below exactly like a real declared one.
        if len(imgname) == 1 and imgname not in self.table:
            literal_path = _quoted_literal_path(imgname[0])
            if literal_path is not None:
                self.table[imgname] = ImageDef(imgname=imgname, kind="path", path=literal_path)

        # DDLC's own script sometimes names a background by its bare tag
        # (`scene bedroom`) even where the only `image` statement defines it
        # as `image bg bedroom` -- confirmed real, not a guess: script-ch4.rpy
        # itself uses both `scene bg bedroom` and bare `scene bedroom` for
        # the identical background. Real Ren'Py resolves a bare tag against
        # any image whose attribute list it's a subset of; this engine has
        # no such general search, but a background is always exactly
        # `('bg', name)` or nothing, so trying that one extra shape covers
        # every real case without a broad, riskier attribute-matching
        # search. Only applies when the bare name has no definition of its
        # own, so it can never shadow a real non-bg image accidentally
        # sharing a name with a background.
        if imgname and imgname[0] != "bg" and imgname not in self.table:
            prefixed = ("bg",) + imgname
            if prefixed in self.table:
                imgname = prefixed

        is_bg = bool(imgname) and imgname[0] == "bg"
        defn = self.table.get(imgname)
        if defn is not None and defn.kind == "solid":
            is_bg = True  # solid colors are cheap enough to render at bg size/palette

        entry = self._bake_flat(imgname, BG_SIZE if is_bg else CG_SIZE, fit=not is_bg)
        if entry is None:
            return None
        if isinstance(entry, int):
            # _bake_flat found this content already baked under a different
            # tag (see its own comment) -- reuse that scene id rather than
            # shipping a duplicate AppVar (and, for an "own" CG, a duplicate
            # palette).
            self._scene_ids[imgname] = entry
            return entry
        entry["palette"] = "shared" if is_bg else "own"
        # Which pal_cg_NNN convimg emits for this scene (matches
        # convert_images.py's `own_scenes` enumeration order) -- None for
        # "shared" scenes, which use pal_game instead. Packaging needs this
        # to know which palette AppVar a given scene id pairs with.
        entry["cg_palette_index"] = None if is_bg else self._own_scene_count
        if not is_bg:
            self._own_scene_count += 1

        idx = len(self.scenes)
        self._scene_content[(entry.pop("_content_hash"), not is_bg)] = idx
        self.scenes.append(entry)
        self._scene_ids[imgname] = idx
        return idx

    def condswitch_variants(self, imgname: tuple) -> Optional[list]:
        """For a ConditionSwitch-backed image (e.g. y_kill's corruption-tier
        portrait -- see _resolve_source), bakes each distinct real PNG it
        references as its own scene and returns [(condition_source_or_None,
        scene_id), ...] in declared order (condition_source is None for the
        unconditional "True" fallback). None if @p imgname isn't one.

        Each variant is registered under a synthetic table key derived from
        its path, not imgname + index, so two thresholds that happen to
        share a picture (confirmed real: y_kill's >=1380 and >=920 both
        point at 3a.png) bake and cache to the same scene id via scene_id()'s
        own dedup rather than shipping the same art twice.
        """
        defn = self.table.get(imgname)
        if defn is None or defn.kind != "condswitch":
            return None

        out = []
        for cond_src, path in defn.variants:
            synth = (f"{imgname[0]}$cond${path}",)
            self.table.setdefault(synth, ImageDef(imgname=synth, kind="path", path=path))
            sid = self.scene_id(synth)
            if sid is None:
                continue
            out.append((None if cond_src.strip() == "True" else cond_src, sid))
        return out or None

    # -- title screen ---------------------------------------------------------

    def _title_entry(self, name: str, rel_path: str, img, x: int, y: int,
                     dx: int, dy: int) -> int:
        """Saves a baked title image and records its resting position and
        entrance offset. Positions are computed here rather than hardcoded in
        render.c because alpha-cropping shifts them -- src/assets.c reads them
        back from the DTILPOS AppVar so the two can't drift apart."""
        idx = len(self.title_items)
        filename = f"title_{idx:02d}_{name}.png"
        img.save(self.img_dir / filename)

        if img.width > SPRITE_MAX_DIM or img.height > SPRITE_MAX_DIM:
            raise ValueError(
                f"title art {name!r} baked to {img.width}x{img.height}, over "
                f"gfx_rletsprite_t's {SPRITE_MAX_DIM}px uint8_t dimension limit")

        self.title_items.append({"name": name, "source": rel_path, "file": filename,
                               "w": img.width, "h": img.height,
                               "x": x, "y": y, "dx": dx, "dy": dy})
        return idx

    def title_art(self, name: str, rel_path: str,
                  xcenter: int, ycenter: int, zoom: float) -> Optional[int]:
        """Bakes one title-screen cast member (a plain `gui/...` path, not a
        Ren'Py image name -- no dialogue references these, so sprite_id() and
        scene_id() never reach them). Returns its id.

        @xcenter/@ycenter/@zoom are DDLC's own image-level ATL constants from
        splash.rpyc, against the 1280x720 canvas, anchoring the *zoomed* size.
        """
        art = self.raw_dir / rel_path
        if not art.is_file():
            self._log_unsupported((rel_path,), "title art file not found")
            return None

        img = PILImage.open(art).convert("RGBA")
        full_w, full_h = img.size

        # Crop the empty margin: these are 1080-tall sheets with the figure
        # occupying only part of the canvas, and the untrimmed sheet would
        # both waste flash and (for Monika) blow the 255px sprite limit.
        bbox = img.getchannel("A").getbbox()
        if bbox is None:
            self._log_unsupported((rel_path,), "title art fully transparent")
            return None
        img = img.crop(bbox)

        # Where the cropped region lands, in screen px: the sheet is centered
        # on (xcenter, ycenter) at `zoom`, so its top-left corner sits at
        # center - full*zoom/2, and the crop starts bbox*zoom further in.
        x = round(TITLE_SCALE * (xcenter - full_w * zoom / 2 + bbox[0] * zoom))
        y = round(TITLE_SCALE * (ycenter - full_h * zoom / 2 + bbox[1] * zoom)) + CAST_DY

        scale = zoom * TITLE_SCALE
        w = max(1, round(img.width * scale))
        h = max(1, round(img.height * scale))

        # Trim anything below the screen edge rather than baking pixels that
        # can never be drawn -- this is also what keeps Monika (270px tall
        # untrimmed) inside the uint8_t sprite height limit.
        if y + h > TITLE_SCREEN[1]:
            h = TITLE_SCREEN[1] - y
            img = img.crop((0, 0, img.width, max(1, round(h / scale))))

        scaled = img.resize((w, max(1, h)), PILImage.LANCZOS)

        # DDLC's entrance: the cast rises from +1200*zoom while sliding
        # horizontally from (740 - xcenter) * zoom * 0.5 (see menu_art_move).
        dy = round(1200 * zoom * TITLE_SCALE)
        dx = round((740 - xcenter) * zoom * 0.5 * TITLE_SCALE)
        return self._title_entry(name, rel_path, scaled, x, y, dx, dy)

    def title_logo(self, rel_path: str = "gui/logo.png") -> Optional[int]:
        """Bakes the logo badge. Unlike the cast this is scaled to the
        *screen*, not proportionally to the art: at a proportional 0.25 it
        would be 77px across and the wordmark inside it illegible."""
        art = self.raw_dir / rel_path
        if not art.is_file():
            self._log_unsupported((rel_path,), "title logo not found")
            return None

        img = PILImage.open(art).convert("RGBA").resize(
            (LOGO_PX, LOGO_PX), PILImage.LANCZOS)
        # Drops in from above (menu_logo_move: yoffset -300 -> 0).
        return self._title_entry("logo", rel_path, img, LOGO_POS[0], LOGO_POS[1],
                                 0, -round(300 * TITLE_SCALE))

    def explicit_bg_scene(self, rel_path: str) -> Optional[int]:
        """Bakes a full-screen background frame as an ordinary scene:
        letterboxed onto BG_SIZE, centered, quantized against the shared game
        palette. Used for a background no dialogue references by name -- the
        startup splash (`bg/splash.png`) -- so scene_id() never reaches it.
        The poem minigame's notebook background is a separate case (see
        poem_background() below): it fills the whole screen, not just the
        scene area an ordinary dialogue background shares with the dialogue
        box, so it can't reuse BG_SIZE/the DSCNn scene pipeline.

        Deliberately reuses the existing DSCNn/DPALGAME pipeline rather than
        the title's own -- these need no title-style positioning or their own
        palette, so riding assets_scene() as-is means zero new C code.

        Must be called before any dialogue is compiled (see do_compile()), in
        the same order every time, so each id is deterministic -- callers
        hardcode the resulting index rather than threading a lookup through
        to src/main.c/src/poem.c.
        """
        art = _find_art_file(self.raw_dir, rel_path)
        if art is None:
            self._log_unsupported((rel_path,), "background art not found")
            return None

        img = PILImage.open(art).convert("RGBA")
        canvas = PILImage.new("RGBA", BG_SIZE, (255, 255, 255, 255))
        ratio = min(BG_SIZE[0] / img.width, BG_SIZE[1] / img.height)
        size = (max(1, round(img.width * ratio)), max(1, round(img.height * ratio)))
        scaled = img.resize(size, PILImage.LANCZOS)
        canvas.alpha_composite(scaled, ((BG_SIZE[0] - size[0]) // 2,
                                        (BG_SIZE[1] - size[1]) // 2))

        idx = len(self.scenes)
        filename = f"bg_{idx:03d}_explicit_{Path(rel_path).stem}.png"
        canvas.save(self.img_dir / filename)

        self.scenes.append({"imgname": ["__explicit__", rel_path], "file": filename,
                            "w": BG_SIZE[0], "h": BG_SIZE[1],
                            "palette": "shared", "cg_palette_index": None})
        return idx

    def poem_background(self, rel_path: str = "bg/notebook.png") -> Optional[dict]:
        """Bakes the poem minigame's notebook background full-screen
        (POEM_BG_SIZE, 320x240) rather than through the DSCNn scene pipeline
        (BG_SIZE, 320x180): the poem screen is its own full-screen UI with no
        dialogue box reserving the bottom 60px, unlike every real dialogue
        background, and letterboxing it into the shorter BG_SIZE canvas (the
        original approach) both left a blank strip at the bottom and threw
        off the ruled-line art's alignment with the word grid drawn over it.

        Quantized against the shared game palette (like an ordinary
        background, not the title's own) since the poem screen runs under
        the normal in-game palette. Returned as its own dict, like
        title_bg_strip(), rather than appended to self.scenes -- it's not
        addressable through OP_SCENE, so it doesn't belong in that id space.
        """
        art = _find_art_file(self.raw_dir, rel_path)
        if art is None:
            self._log_unsupported((rel_path,), "poem background art not found")
            return None

        img = PILImage.open(art).convert("RGBA")
        canvas = PILImage.new("RGBA", POEM_BG_SIZE, (255, 255, 255, 255))
        ratio = min(POEM_BG_SIZE[0] / img.width, POEM_BG_SIZE[1] / img.height)
        size = (max(1, round(img.width * ratio)), max(1, round(img.height * ratio)))
        scaled = img.resize(size, PILImage.LANCZOS)
        canvas.alpha_composite(scaled, ((POEM_BG_SIZE[0] - size[0]) // 2,
                                        (POEM_BG_SIZE[1] - size[1]) // 2))

        filename = f"poem_bg_{Path(rel_path).stem}.png"
        canvas.save(self.img_dir / filename)

        entry = {"source": rel_path, "file": filename,
                 "w": POEM_BG_SIZE[0], "h": POEM_BG_SIZE[1]}
        self.poem_bg = entry
        return entry

    def ui_box_art(self, rel_path: str, size: tuple) -> Optional[dict]:
        """Bakes a fixed-size UI element (the real textbox.png/namebox.png)
        for a direct full-block blit, the same reasoning as
        poem_background() but generalized to an arbitrary @p size instead of
        always full-screen.

        Composited onto a solid COL_BOX_FILL-matching backdrop (#5A1030,
        see convert_images.py's FIXED_ENTRIES) rather than kept transparent:
        this engine's scene/background blits are always opaque (no
        per-pixel alpha compositing at draw time -- see assets_scene()'s own
        doc comment), so the real art's alpha channel (rounded corners,
        soft edges) can only be *resolved* here at bake time, not carried
        through to the calculator. The result reads as slightly squared-off
        corners against the box's own real fill color rather than a hard
        rectangle against whatever's behind it -- a deliberate, visible
        simplification in the same spirit as this project's other
        alpha-flattening bakes (poem_background()'s own white backdrop,
        _bake_flat()'s CG letterboxing), not a silent one.

        Real width/height for either real source image is well over
        gfx_rletsprite_t's uint8_t 255px dimension cap once scaled to fill
        anywhere near this engine's own 320px-wide screen (see TEXTBOX_SIZE's
        own comment) -- which is why this bakes to a plain full-block raw
        image (like a scene) rather than through the RLET sprite pipeline
        every character atom and the namebox's real rounded shape would
        otherwise be a natural fit for.
        """
        art = _find_art_file(self.raw_dir, rel_path)
        if art is None:
            self._log_unsupported((rel_path,), "UI box art not found")
            return None

        img = PILImage.open(art).convert("RGBA")
        canvas = PILImage.new("RGBA", size, (0x5A, 0x10, 0x30, 255))
        ratio = min(size[0] / img.width, size[1] / img.height)
        scaled_size = (max(1, round(img.width * ratio)), max(1, round(img.height * ratio)))
        scaled = img.resize(scaled_size, PILImage.LANCZOS)
        canvas.alpha_composite(scaled, ((size[0] - scaled_size[0]) // 2,
                                        (size[1] - scaled_size[1]) // 2))

        filename = f"ui_{Path(rel_path).stem}.png"
        canvas.save(self.img_dir / filename)

        return {"source": rel_path, "file": filename, "w": size[0], "h": size[1]}

    def title_bg_strip(self, rel_path: str = "gui/menu_bg.png") -> Optional[dict]:
        """Bakes the scrolling title background as a short repeating strip.

        Takes one BG_TILE_PERIOD-sized period of the (exactly periodic) source
        pattern, scales it to BG_TILE_W, and repeats it horizontally to
        BG_STRIP_SIZE -- wide enough that any horizontal scroll offset up to
        one tile can be read as a single flat row copy. See BG_TILE_PERIOD.
        """
        art = self.raw_dir / rel_path
        if not art.is_file():
            self._log_unsupported((rel_path,), "title background not found")
            return None

        src = PILImage.open(art).convert("RGB")
        # Downscale a 3x3 block of periods and keep the middle tile, rather
        # than resizing a lone period: LANCZOS clamps at the image edge, and
        # that edge error would leave a 1px seam that is very visible once the
        # background starts scrolling. Sampling from the middle means every
        # output pixel sees real neighbouring pattern on all sides.
        block = BG_TILE_PERIOD * 3
        period = src.crop((0, 0, block, block)).resize(
            (BG_TILE_W * 3, BG_TILE_W * 3), PILImage.LANCZOS)
        tile = period.crop((BG_TILE_W, BG_TILE_W, BG_TILE_W * 2, BG_TILE_W * 2))

        strip = PILImage.new("RGB", BG_STRIP_SIZE)
        for x in range(0, BG_STRIP_SIZE[0], BG_TILE_W):
            for y in range(0, BG_STRIP_SIZE[1], BG_TILE_W):
                strip.paste(tile, (x, y))

        filename = "title_bg_strip.png"
        strip.save(self.img_dir / filename)
        entry = {"source": rel_path, "file": filename,
                 "w": BG_STRIP_SIZE[0], "h": BG_STRIP_SIZE[1], "tile": BG_TILE_W}
        self.title_bg = entry
        return entry

    # -- baking --------------------------------------------------------------

    def _log_unsupported(self, imgname: tuple, reason: str) -> None:
        self.unsupported.append((imgname, reason))

    def _render_ref(self, ref: str, depth: int = 0) -> Optional[PILImage.Image]:
        """Render a layer/frame reference: a real file path, or (confirmed in
        real data -- e.g. Composite layer "n_rects_mouth", ATL frame "bg
        club_day") a symbolic reference to another declared `image`."""
        if depth > 8:  # guards against a reference cycle between Image defs
            return None

        if _is_file_path(ref):
            art = _find_art_file(self.raw_dir, ref)
            return PILImage.open(art).convert("RGBA") if art else None

        defn = self.table.get(tuple(ref.split()))
        return self._render_def(defn, depth + 1) if defn else None

    def _render_def(self, defn: ImageDef, depth: int = 0) -> Optional[PILImage.Image]:
        if defn.kind == "solid":
            return PILImage.new("RGBA", (64, 64), defn.color + (255,))

        if defn.kind == "path":
            return self._render_ref(defn.path, depth)

        if defn.kind == "composite":
            canvas = PILImage.new("RGBA", defn.canvas, (0, 0, 0, 0))
            any_layer = False
            for layer in defn.layers:
                img = self._render_ref(layer.path, depth)
                if img is None:
                    # A decorative layer this engine can't resolve (e.g. Act
                    # 3's n_cg1b overlays procedural RectCluster specks with
                    # no static frame) shouldn't sink an otherwise-good base
                    # CG -- drop just that layer and keep going, same
                    # "approximate rather than refuse" precedent as the
                    # zoom/dizzy ATL cases above.
                    continue
                canvas.alpha_composite(img, (layer.x, layer.y))
                any_layer = True
            return canvas if any_layer else None

        return None

    def _bake_sprite(self, imgname: tuple) -> Optional[dict]:
        defn = self.table.get(imgname)
        if defn is None:
            self._log_unsupported(imgname, "no Image definition found")
            return None

        canvas = self._render_def(defn)
        if canvas is None:
            self._log_unsupported(imgname, defn.reason or "could not render (missing or unresolvable referenced art)")
            return None

        bbox = canvas.getbbox()
        if bbox is None:
            self._log_unsupported(imgname, "fully transparent composite")
            return None
        cropped = canvas.crop(bbox)

        size = (max(1, round(cropped.width * SPRITE_SCALE)),
                max(1, round(cropped.height * SPRITE_SCALE)))
        if size[0] > SPRITE_MAX_DIM or size[1] > SPRITE_MAX_DIM:
            self._log_unsupported(
                imgname, f"scaled to {size[0]}x{size[1]}, over gfx_rletsprite_t's "
                         f"{SPRITE_MAX_DIM}px uint8_t dimension limit")
            return None
        scaled = cropped.resize(size, PILImage.LANCZOS)
        dx, dy = self._canvas_offsets(canvas.size, bbox, size)

        # Same dedup as _bake_flat's, but the draw offset (derived from
        # where this content sat in its own, possibly differently-sized
        # source canvas) has to match too, not just pixels -- two tags with
        # identical cropped content from differently shaped canvases would
        # otherwise reuse an id whose dx/dy means something else.
        content_hash = hashlib.sha256(scaled.tobytes() + struct.pack("<hh", dx, dy)).digest()
        existing = self._sprite_content.get(content_hash)
        if existing is not None:
            return existing

        name = _safe_filename_part(imgname) or "sprite"
        filename = f"sprite_{len(self.sprites):03d}_{name}.png"
        scaled.save(self.img_dir / filename)

        return {"imgname": list(imgname), "file": filename,
                "w": size[0], "h": size[1], "dx": dx, "dy": dy,
                "_content_hash": content_hash}

    def _bake_flat(self, imgname: tuple, size: tuple, fit: bool) -> Optional[dict]:
        defn = self.table.get(imgname)
        if defn is None:
            self._log_unsupported(imgname, "no Image definition found")
            return None

        if defn.kind == "solid":
            canvas = PILImage.new("RGBA", size, defn.color + (255,))
        else:
            art = self._render_def(defn)
            if art is None:
                self._log_unsupported(imgname, defn.reason or "could not render (missing or unresolvable referenced art)")
                return None
            if fit:
                canvas = PILImage.new("RGBA", size, (0, 0, 0, 255))
                ratio = min(size[0] / art.width, size[1] / art.height)
                scaled_size = (max(1, round(art.width * ratio)), max(1, round(art.height * ratio)))
                art = art.resize(scaled_size, PILImage.LANCZOS)
                canvas.alpha_composite(art, ((size[0] - scaled_size[0]) // 2,
                                             (size[1] - scaled_size[1]) // 2))
            else:
                canvas = art.resize(size, PILImage.LANCZOS).convert("RGBA")

        # Several real Act 3 tags bake to pixel-identical content under this
        # engine's own simplifications -- confirmed real, not hypothetical:
        # a zoom/dizzy ATL wrapper (s_kill_zoom et al) resolves to the exact
        # same picture as its un-zoomed base once the zoom transform is
        # dropped (no per-frame transform support at all, see
        # _first_atl_string's RawChild case above), a weighted RawChoice
        # background variant (bg club_day/club_day2) always takes the first
        # branch (no randomization), and more than one "dark overlay" tag
        # (black/vignette/dark) quantizes to the same flat color. Baking and
        # shipping each as its own AppVar (plus, for an "own"-palette CG, its
        # own duplicate palette) is pure waste -- reuse the existing scene id
        # instead of a fresh bake whenever the *rendered* content (not the
        # tag) already matches one, keyed on `fit` too since a CG and a
        # background can't share a palette bucket even from identical pixels.
        content_hash = hashlib.sha256(canvas.tobytes()).digest()
        existing = self._scene_content.get((content_hash, fit))
        if existing is not None:
            return existing

        name = _safe_filename_part(imgname) or "scene"
        filename = f"{'cg' if fit else 'bg'}_{len(self.scenes):03d}_{name}.png"
        canvas.save(self.img_dir / filename)

        return {"imgname": list(imgname), "file": filename, "w": size[0], "h": size[1],
                "_content_hash": content_hash}

    # -- output ---------------------------------------------------------------

    def manifest(self) -> dict:
        return {
            "sprites": self.sprites,
            "scenes": self.scenes,
            "title_art": self.title_items,
            "title_bg": self.title_bg,
            "poem_bg": self.poem_bg,
            "textbox": self.textbox,
            "namebox": self.namebox,
            "unsupported": [{"imgname": list(name), "reason": reason}
                            for name, reason in self.unsupported],
        }

    def write_manifest(self) -> Path:
        path = self.build_dir / "manifest.json"
        path.write_text(json.dumps(self.manifest(), indent=2))
        return path
