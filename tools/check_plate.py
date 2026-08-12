#!/usr/bin/env python3
"""Checks src/render.c's moving-actor plate against a ground-truth full redraw.

The plate (see docs/FORMAT.md, "The moving-actor plate") is what lets an
animating frame skip the background decode: the rectangle an actor can move
within is saved once, then pasted back and only that actor -- plus whatever
draws in front of it -- is redrawn. If the rectangle is even slightly too
small, or the z-order split is wrong, the result is smeared edges or a
character drawn through a neighbour, which is exactly the kind of bug that is
miserable to chase on a calculator screen.

Nothing about that reasoning needs hardware, though: it's pure geometry over
the baked sprites. So this mirrors actor_rect() / draw_actor() /
render_scene_moving() here and compares a plate frame with the full redraw it
is supposed to be indistinguishable from, pixel for pixel, across every mover
slot, every reachable animation offset, zoomed and not, in scenes of two to
four overlapping characters.

Run after an import (it reads build/manifest.json and build/img/):

    python3 tools/check_plate.py [--build-dir build]

Exits non-zero on any mismatch, printing the offending frame and diff box.
"""
import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageChops

# --- constants, mirrored from src/render.c and src/render.h ------------------
SCREEN_W, SCREEN_H, SCENE_H = 320, 240, 180
ACTOR_BASELINE = (SCENE_H * 103) // 100           # DDLC's ypos 1.03
HOP_PX, SPEAK_POP_PX, SINK_PX = 5, 4, 5
PLATE_SLACK = HOP_PX + SPEAK_POP_PX + SINK_PX
ZOOM_NUM, ZOOM_DEN = 21, 20

# The offsets draw_actor() can actually produce: hop and the fallback rise
# both pull up, sink pushes down, and they can stack.
OFFSET_RANGE = range(-(HOP_PX + SPEAK_POP_PX), SINK_PX + 1)

# Offsets walked in order off a single capture, up then down then up again,
# so every frame has to clean up after a predecessor at a different position
# -- both directions, and both extremes adjacent to the resting offset.
TRAJECTORY = (list(OFFSET_RANGE) + list(reversed(OFFSET_RANGE))
              + [0, SINK_PX, 0, -(HOP_PX + SPEAK_POP_PX), 0])

# Characters to stage, as (canvas X from transforms.rpy, base atom, overlay
# atom). The X values are DDLC's own four-character positions, so the sprites
# overlap the way they really do.
CAST_DEF = [
    (200,  ("sayori", "sayori_2l-2r"),   ("sayori", "sayori_a")),
    (493,  ("yuri", "yuri_1l-1r"),       ("yuri", "yuri_a")),
    (786,  ("natsuki", "natsuki_2l-2r"), ("natsuki", "natsuki_c")),
    (1080, ("monika", "monika_1l-1r"),   ("monika", "monika_a")),
]

# A mover clamped at neither end of the scene area. Every real character's
# base atom reaches the baseline, so its rectangle is clamped to SCENE_H at
# the bottom, and that clamp alone happens to absorb the whole range of
# motion -- meaning the current cast cannot detect a missing PLATE_SLACK.
# This one (an expression atom standing alone, spanning roughly y 5..61) is
# free at both ends, so it fails immediately if the slack is dropped. It
# keeps the constant honest against art that doesn't reach the floor.
FLOATING_DEF = (640, ("yuri", "yuri_a"), None)


def zdim(v):
    return (v * ZOOM_NUM + ZOOM_DEN // 2) // ZOOM_DEN


def zoff(v):
    """Round-half-away-from-zero, as assets.c's zoom_scale_off()."""
    return ((v * ZOOM_NUM + ZOOM_DEN // 2) // ZOOM_DEN if v >= 0
            else -((-v * ZOOM_NUM + ZOOM_DEN // 2) // ZOOM_DEN))


class Stage:
    def __init__(self, build_dir: Path):
        self.img_dir = build_dir / "img"
        self.sprites = json.loads((build_dir / "manifest.json").read_text())["sprites"]
        self._cache = {}
        bg = next(s for s in json.loads((build_dir / "manifest.json").read_text())["scenes"]
                  if "class" in s["file"])
        self.bg_file = bg["file"]

    def sprite_id(self, *parts):
        for i, s in enumerate(self.sprites):
            if s["imgname"] == list(parts):
                return i
        raise KeyError(parts)

    def image(self, sid):
        """Binary alpha, matching the real pipeline: convimg bakes these to an
        8bpp indexed sprite where index 0 is transparent and every other index
        is opaque. There is no blending on device, so drawing is idempotent --
        which is precisely what lets the plate redraw an unmoved actor over
        itself without changing a pixel. Compositing with the source PNGs'
        antialiased alpha instead would report false differences."""
        if sid not in self._cache:
            im = Image.open(self.img_dir / self.sprites[sid]["file"]).convert("RGBA")
            im.putalpha(im.getchannel("A").point(lambda v: 255 if v >= 128 else 0))
            self._cache[sid] = im
        return self._cache[sid]

    def layers(self, actor):
        yield actor["sprite"]
        if actor["overlay"] is not None:
            yield actor["overlay"]

    # --- mirrors of the three render.c routines under test -------------------

    def draw_actor(self, canvas, actor, off, zoom):
        cx = actor["pos"] * 2
        feet = ACTOR_BASELINE + off
        for sid in self.layers(actor):
            s = self.sprites[sid]
            w, h, dx, dy = s["w"], s["h"], s["dx"], s["dy"]
            if zoom:
                zw, zh = zdim(w), zdim(h)
                canvas.alpha_composite(self.image(sid).resize((zw, zh), Image.NEAREST),
                                       (cx - zw // 2 + zoff(dx), feet - zh + zoff(dy)))
            else:
                canvas.alpha_composite(self.image(sid), (cx - w // 2 + dx, feet - h + dy))

    def actor_rect(self, actor):
        cx = actor["pos"] * 2
        x0, y0, x1, y1 = SCREEN_W, SCREEN_H, 0, 0
        for sid in self.layers(actor):
            s = self.sprites[sid]
            w, h, dx, dy = s["w"], s["h"], s["dx"], s["dy"]
            for ww, hh, ddx, ddy in ((w, h, dx, dy),
                                     (zdim(w), zdim(h), zoff(dx), zoff(dy))):
                px, py = cx - ww // 2 + ddx, ACTOR_BASELINE - hh + ddy
                x0, y0 = min(x0, px), min(y0, py)
                x1, y1 = max(x1, px + ww), max(y1, py + hh)
        y0, y1 = y0 - PLATE_SLACK, y1 + PLATE_SLACK
        x0, y0 = max(x0, 0), max(y0, 0)
        x1, y1 = min(x1, SCREEN_W), min(y1, SCENE_H)
        return None if x1 <= x0 or y1 <= y0 else (x0, y0, x1 - x0, y1 - y0)

    # --- frame composition ---------------------------------------------------

    def background(self):
        c = Image.new("RGBA", (SCREEN_W, SCREEN_H), (0, 0, 0, 255))
        c.alpha_composite(Image.open(self.img_dir / self.bg_file).convert("RGBA"), (0, 0))
        return c

    @staticmethod
    def render_box(canvas):
        """render_box() opens with an opaque fill of the whole box, which is
        why the plate stops at SCENE_H."""
        canvas.paste(Image.new("RGBA", (SCREEN_W, SCREEN_H - SCENE_H), (60, 20, 45, 255)),
                     (0, SCENE_H))

    def full_redraw(self, actors, offs, zooms):
        c = self.background()
        for i, a in enumerate(actors):
            self.draw_actor(c, a, offs[i], zooms[i])
        self.render_box(c)
        return c

    def plate_sequence(self, actors, k, rest, zooms, trajectory):
        """One capture frame, then an animating frame per offset in
        @p trajectory -- yielding (offset, frame) for each.

        Running a *sequence* off a single capture is the point. The engine
        captures once when an animation starts and reuses that plate for
        every frame of it, so a frame has to erase not just the actor's
        resting position but wherever the previous frame left it. Checking
        only one frame per capture hides exactly the bug PLATE_SLACK exists
        to prevent: without the slack the rectangle still covers the actor at
        rest, so a single step looks perfect, and only the step *after* it
        reveals the pixels stranded outside.
        """
        offs = [rest] * len(actors)
        offs[k] = 0

        c = self.background()
        for i in range(k):
            self.draw_actor(c, actors[i], offs[i], zooms[i])

        rect = self.actor_rect(actors[k])
        assert rect is not None, "empty plate rect"
        x, y, w, h = rect
        plate = c.crop((x, y, x + w, y + h)).copy()          # plate_blit(save)

        for i in range(k, len(actors)):
            self.draw_actor(c, actors[i], offs[i], zooms[i])
        self.render_box(c)

        for off in trajectory:
            offs[k] = off
            c.paste(plate, (x, y))                            # plate_blit(restore)
            for i in range(k, len(actors)):
                self.draw_actor(c, actors[i], offs[i], zooms[i])
            self.render_box(c)
            yield off, list(offs), c.copy(), rect


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-dir", type=Path, default=Path("build"))
    args = ap.parse_args()

    if not (args.build_dir / "manifest.json").is_file():
        print(f"no {args.build_dir}/manifest.json -- run tools/import_game.py first",
              file=sys.stderr)
        return 2

    stage = Stage(args.build_dir)
    cast = [{"pos": round(x / 8), "sprite": stage.sprite_id(*b), "overlay": stage.sprite_id(*o)}
            for x, b, o in CAST_DEF]

    x, b, o = FLOATING_DEF
    floating = {"pos": round(x / 8), "sprite": stage.sprite_id(*b),
                "overlay": stage.sprite_id(*o) if o else None}

    checks = fails = 0
    scenarios = [cast[:n] for n in (2, 3, 4)]
    # ...plus the floating mover, in front of and behind a real character
    scenarios += [[cast[0], floating, cast[3]], [floating, cast[1]]]

    for actors in scenarios:
        n = len(actors)
        for k in range(n):
            for zoom_mover in (False, True):
                # Non-movers held at a standing sink offset as well as at rest:
                # sink persists, so a neighbour can legitimately sit displaced
                # for the whole of someone else's animation.
                for rest_off in (0, SINK_PX):
                    zooms = [False] * n
                    zooms[k] = zoom_mover
                    for off, offs, got, rect in stage.plate_sequence(
                            actors, k, rest_off, zooms, TRAJECTORY):
                        want = stage.full_redraw(actors, offs, zooms)
                        checks += 1
                        diff = ImageChops.difference(got.convert("RGB"), want.convert("RGB"))
                        if diff.getbbox() is not None:
                            fails += 1
                            print(f"  MISMATCH cast={n} mover={k} zoom={zoom_mover} "
                                  f"rest={rest_off} off={off} rect={rect} "
                                  f"diff={diff.getbbox()}")

    print(f"{checks} plate frames compared against full redraws, {fails} mismatches")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
