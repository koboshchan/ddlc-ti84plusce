#!/usr/bin/env python3
"""Generate src/demo.c -- the Milestone 1 placeholder script.

The text here is original filler written for engine testing. No dialogue from
Doki Doki Literature Club appears in this repository; real script data is
produced locally by the asset pipeline into the git-ignored assets/ tree.

Usage:  python3 tools/gen_demo.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from vnasm import (  # noqa: E402
    Assembler, CMP_GE, POS_LEFT, POS_RIGHT, TRANS_FADE,
)

# Placeholder ids. The pipeline will emit real tables alongside the bytecode.
SAYORI, NATSUKI, YURI, MONIKA = 0, 1, 2, 3
BG_CLASSROOM, BG_CLUBROOM, BG_CORRIDOR = 0, 1, 2
VAR_VISITS = 0
VAR_LOOP = 1


def build() -> Assembler:
    a = Assembler()

    # --- opening: exercises SCENE + narration + word wrap ------------------
    a.scene(BG_CLASSROOM, TRANS_FADE)
    a.narrate(
        "Engine test script. This line is deliberately long so that the "
        "greedy word wrapper has to break it across several lines inside "
        "the dialogue box."
    )
    a.set(VAR_VISITS, 0)

    # Every branch loops back here, so the counter guarantees termination no
    # matter which options are picked -- including the always-pick-0 default.
    a.label("clubroom")
    a.add(VAR_VISITS, 1)
    a.if_(VAR_VISITS, CMP_GE, 4, "done")
    a.scene(BG_CLUBROOM, TRANS_FADE)
    a.show(SAYORI, 1, pos=POS_LEFT)
    a.show(NATSUKI, 2, pos=POS_RIGHT)
    a.say(SAYORI, "Two actors are on stage. Check both anchor positions.")
    a.say(NATSUKI, "The number on each block is the pre-baked sprite id from OP_SHOW.")

    # --- menu: exercises MENU + JUMP ---------------------------------------
    a.narrate("Pick an option with the arrow keys, confirm with 2nd.")
    a.menu([
        ("Test OP_HIDE", "branch_hide"),
        ("Test OP_CALL", "branch_call"),
        ("Test OP_IF loop", "branch_loop"),
        ("Finish", "done"),
    ])

    a.label("branch_hide")
    a.hide(NATSUKI)
    a.say(SAYORI, "One actor hidden. The other should be untouched.")
    a.jump("clubroom")

    a.label("branch_call")
    a.call("subroutine")
    a.say(SAYORI, "Back from the subroutine, so OP_RETURN restored the pc.")
    a.jump("clubroom")

    # Uses its own counter so it cannot interfere with the outer VAR_VISITS
    # termination guard.
    a.label("branch_loop")
    a.set(VAR_LOOP, 0)
    a.label("loop_top")
    a.scene(BG_CORRIDOR)
    a.add(VAR_LOOP, 1)
    a.say(YURI, "Loop iteration via OP_ADD. Three passes, then it exits.")
    a.if_(VAR_LOOP, CMP_GE, 3, "clubroom")
    a.jump("loop_top")

    # --- subroutine ---------------------------------------------------------
    a.label("subroutine")
    a.scene(BG_CORRIDOR)
    a.say(MONIKA, "Inside a subroutine, reached through OP_CALL.")
    a.ret()

    a.label("done")
    a.scene(BG_CLASSROOM, TRANS_FADE)
    a.narrate("Demo script complete. Press any key to exit.")
    a.end()

    return a


def main() -> int:
    asm = build()
    out = pathlib.Path(__file__).parent.parent / "src" / "demo.c"
    out.write_text(asm.to_c("demo", "Milestone 1 placeholder script (original filler text)."))

    print(f"wrote {out.relative_to(out.parent.parent)}: "
          f"{len(asm.code)} bytes of code, {len(asm.strings)} strings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
