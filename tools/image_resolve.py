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
import json
import re
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

_HEX_COLOR = re.compile(r"^#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")

SPRITE_TARGET_HEIGHT = 176   # tuned against render.c's scene area (320x180)
BG_SIZE = (320, 180)
CG_SIZE = (320, 180)


# --- image definition table --------------------------------------------------

@dataclass
class Layer:
    x: int
    y: int
    path: str


@dataclass
class ImageDef:
    imgname: tuple
    kind: str                      # 'solid' | 'path' | 'composite' | 'unsupported'
    color: Optional[tuple] = None  # (r,g,b) for 'solid'
    path: Optional[str] = None     # for 'path'
    canvas: Optional[tuple] = None # (w,h) for 'composite'
    layers: list = field(default_factory=list)  # [Layer, ...] for 'composite'
    reason: Optional[str] = None   # for 'unsupported'


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
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    return node.value

        elif k == "RawChoice" and stmt.choices:
            _weight, block = stmt.choices[0]
            found = _first_atl_string(block)
            if found is not None:
                return found

        elif k == "RawParallel" and getattr(stmt, "blocks", None):
            found = _first_atl_string(stmt.blocks[0])
            if found is not None:
                return found

        elif k == "RawBlock":
            # A bare nested block, e.g. monika g2's animation wraps a
            # RawChoice one level deeper than club_day2's did -- recurse the
            # same way rather than assuming one fixed nesting depth.
            found = _first_atl_string(stmt)
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
            rgb = tuple(int(hex_digits[i:i + 2], 16) for i in (0, 2, 4))
            return ImageDef(imgname, "solid", color=rgb)
        return ImageDef(imgname, "path", path=value)

    if isinstance(node, ast.Call):
        parsed = _parse_composite(node)
        if parsed is not None:
            canvas, layers = parsed
            return ImageDef(imgname, "composite", canvas=canvas, layers=layers)
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

    return table


# --- rendering ----------------------------------------------------------------

_IMAGE_EXT = re.compile(r"\.(png|jpg|jpeg)$", re.IGNORECASE)


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

        self.sprites: list = []      # manifest rows, index == id
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
        idx = len(self.sprites)
        self.sprites.append(entry)
        self._sprite_ids[imgname] = idx
        return idx

    def scene_id(self, imgname: tuple) -> Optional[int]:
        if imgname in self._scene_ids:
            return self._scene_ids[imgname]

        is_bg = bool(imgname) and imgname[0] == "bg"
        defn = self.table.get(imgname)
        if defn is not None and defn.kind == "solid":
            is_bg = True  # solid colors are cheap enough to render at bg size/palette

        entry = self._bake_flat(imgname, BG_SIZE if is_bg else CG_SIZE, fit=not is_bg)
        if entry is None:
            return None
        entry["palette"] = "shared" if is_bg else "own"
        # Which pal_cg_NNN convimg emits for this scene (matches
        # convert_images.py's `own_scenes` enumeration order) -- None for
        # "shared" scenes, which use pal_game instead. Packaging needs this
        # to know which palette AppVar a given scene id pairs with.
        entry["cg_palette_index"] = None if is_bg else self._own_scene_count
        if not is_bg:
            self._own_scene_count += 1

        idx = len(self.scenes)
        self.scenes.append(entry)
        self._scene_ids[imgname] = idx
        return idx

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
            for layer in defn.layers:
                img = self._render_ref(layer.path, depth)
                if img is None:
                    return None
                canvas.alpha_composite(img, (layer.x, layer.y))
            return canvas

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

        scale = SPRITE_TARGET_HEIGHT / cropped.height
        size = (max(1, round(cropped.width * scale)), SPRITE_TARGET_HEIGHT)
        scaled = cropped.resize(size, PILImage.LANCZOS)

        name = "_".join(imgname).replace("/", "-") or "sprite"
        filename = f"sprite_{len(self.sprites):03d}_{name}.png"
        scaled.save(self.img_dir / filename)

        return {"imgname": list(imgname), "file": filename,
                "w": size[0], "h": size[1],
                "origin_x": bbox[0], "origin_y": bbox[1]}

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

        name = "_".join(imgname).replace("/", "-") or "scene"
        filename = f"{'cg' if fit else 'bg'}_{len(self.scenes):03d}_{name}.png"
        canvas.save(self.img_dir / filename)

        return {"imgname": list(imgname), "file": filename, "w": size[0], "h": size[1]}

    # -- output ---------------------------------------------------------------

    def manifest(self) -> dict:
        return {
            "sprites": self.sprites,
            "scenes": self.scenes,
            "unsupported": [{"imgname": list(name), "reason": reason}
                            for name, reason in self.unsupported],
        }

    def write_manifest(self) -> Path:
        path = self.build_dir / "manifest.json"
        path.write_text(json.dumps(self.manifest(), indent=2))
        return path
