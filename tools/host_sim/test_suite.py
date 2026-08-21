#!/usr/bin/env python3
"""Comprehensive test suite for tools/host_sim (vnsim).

Tests:
1. Normal chapter progression (Act 1, Act 2, Act 3, Act 4).
2. Character deletion paths (monika.chr deleted, sayori.chr deleted, mid-Act 3 deletion).
3. Dialogue choices, exclusive routes, and poem minigame responses.
4. Special easter egg scenes and glitch sequences.
5. Checks for crashes, invalid opcodes, stack overflow, bounds errors, unhandled jumps, and loops.
"""

from __future__ import annotations

import glob
import os
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
VNSIM = ROOT / "tools" / "host_sim" / "build" / "vnsim"
APPVARS = ROOT / "build" / "appvars"
BUILD = ROOT / "build"


@dataclass
class TestResult:
    name: str
    passed: bool
    status: str
    chunk: int
    pc: int
    lines: int
    menus: int
    quit_requested: bool
    deleted: list[str]
    warnings: list[str]
    error_message: str = ""
    output: str = ""


def get_vnb_args() -> list[str]:
    vnb_files = sorted(BUILD.glob("chunk*.vnb"), key=lambda p: int(p.stem.removeprefix("chunk")))
    return [f"--vnb={p}" for p in vnb_files]


def run_sim(
    name: str,
    *,
    entry_bin: str | None = "dentry.bin",
    pc: int | None = None,
    choices: list[int] | None = None,
    vars_set: dict[int, int] | None = None,
    minigames: list[tuple[int, int, int, int]] | None = None,
    absent: list[str] | None = None,
    seed: int = 1,
    max_lines: int = 10000,
    trace: bool = False,
    quiet: bool = True,
    expected_status: str = "finished",
    expected_quit: bool | None = None,
    expected_deleted: list[str] | None = None,
    min_lines: int = 1,
) -> TestResult:
    cmd = [str(VNSIM)]
    cmd.extend(get_vnb_args())

    labels_bin = APPVARS / "dvlbl.bin"
    if labels_bin.is_file():
        cmd.append(f"--labels={labels_bin}")

    defaults_bin = APPVARS / "dvdef.bin"
    if defaults_bin.is_file():
        cmd.append(f"--defaults={defaults_bin}")

    if entry_bin is not None:
        eb = APPVARS / entry_bin
        if eb.is_file():
            cmd.append(f"--dentry={eb}")

    if pc is not None:
        cmd.append(f"--pc={pc}")

    if choices:
        cmd.append("--choices=" + ",".join(str(c) for c in choices))

    if vars_set:
        for slot, val in vars_set.items():
            cmd.append(f"--var={slot}:{val}")

    if minigames:
        mg_strs = []
        for winner, s, n, y in minigames:
            mg_strs.append(f"{winner},{s},{n},{y}")
        cmd.append("--minigame=" + ";".join(mg_strs))

    if absent:
        cmd.append("--absent=" + ",".join(absent))

    if seed != 1:
        cmd.append(f"--seed={seed}")

    if max_lines != 10000:
        cmd.append(f"--max-lines={max_lines}")

    if trace:
        cmd.append("--trace")

    if quiet:
        cmd.append("--quiet")

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT))

    status = "unknown"
    chunk = 0
    pc_out = 0
    lines = 0
    menus = 0
    quit_req = False
    deleted: list[str] = []
    warnings: list[str] = []

    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith("status :"):
            status = line.split(":", 1)[1].strip()
        elif line.startswith("chunk  :"):
            chunk = int(line.split(":", 1)[1].strip())
        elif line.startswith("pc     :"):
            pc_str = line.split(":", 1)[1].strip().split("/")[0].strip()
            pc_out = int(pc_str)
        elif line.startswith("lines  :"):
            lines = int(line.split(":", 1)[1].strip())
        elif line.startswith("menus  :"):
            menus = int(line.split(":", 1)[1].strip())
        elif line.startswith("quit   :"):
            quit_req = "yes" in line
        elif line.startswith("deleted:"):
            deleted = line.split(":", 1)[1].strip().split()
        elif "WARNING:" in line:
            warnings.append(line)

    passed = True
    error_msg = []

    if status != expected_status:
        passed = False
        error_msg.append(f"Expected status '{expected_status}', got '{status}'")

    if expected_quit is not None and quit_req != expected_quit:
        passed = False
        error_msg.append(f"Expected quit={expected_quit}, got {quit_req}")

    if expected_deleted is not None:
        if sorted(deleted) != sorted(expected_deleted):
            passed = False
            error_msg.append(f"Expected deleted={expected_deleted}, got {deleted}")

    if lines < min_lines:
        passed = False
        error_msg.append(f"Lines executed ({lines}) < minimum expected ({min_lines})")

    if res.returncode != 0 and expected_status == "finished":
        passed = False
        error_msg.append(f"Process exited with non-zero code {res.returncode}")

    if res.stderr:
        error_msg.append(f"stderr: {res.stderr.strip()}")

    return TestResult(
        name=name,
        passed=passed,
        status=status,
        chunk=chunk,
        pc=pc_out,
        lines=lines,
        menus=menus,
        quit_requested=quit_req,
        deleted=deleted,
        warnings=warnings,
        error_message="; ".join(error_msg),
        output=res.stdout,
    )


def load_dchjmp() -> dict[str, int]:
    chjmp_file = APPVARS / "dchjmp.bin"
    if not chjmp_file.is_file():
        return {}
    data = chjmp_file.read_bytes()
    count = struct.unpack("<H", data[:2])[0]
    pos = 2
    chapters = {}
    for _ in range(count):
        nlen = data[pos]
        pos += 1
        name = data[pos:pos+nlen].decode("ascii")
        pos += nlen
        addr = struct.unpack("<I", data[pos:pos+4])[0]
        pos += 4
        chapters[name] = addr
    return chapters


def load_dvlbl() -> dict[int, int]:
    lbl_file = APPVARS / "dvlbl.bin"
    if not lbl_file.is_file():
        return {}
    data = lbl_file.read_bytes()
    count = struct.unpack("<H", data[:2])[0]
    pos = 2
    labels = {}
    for _ in range(count):
        str_id, addr = struct.unpack("<HI", data[pos:pos+6])
        pos += 6
        labels[str_id] = addr
    return labels


def run_all_tests() -> list[TestResult]:
    results: list[TestResult] = []
    chapters = load_dchjmp()
    labels = load_dvlbl()

    print(f"=== Loaded {len(chapters)} chapter entry points from DCHJMP ===")

    # -------------------------------------------------------------
    # Suite 1: Full Act Progression
    # -------------------------------------------------------------
    print("\n--- Running Suite 1: Full Act Progression ---")

    # 1.1 Act 1 Default Path (All Option 0)
    # Sayori confession (Option 0: I love you), Sayori deleted at end of Act 1
    r = run_sim(
        "Act 1 Default Path (Sayori confession, option 0)",
        entry_bin="dentry.bin",
        choices=[0] * 30,
        expected_status="finished",
        expected_deleted=["sayori"],
        min_lines=3000,
    )
    results.append(r)

    # 1.2 Act 1 Option 1 (Dearest Friend)
    # Sayori confession (Option 1: You'll always be my dearest friend)
    r = run_sim(
        "Act 1 Alternate Path (Dearest Friend confession, option 1)",
        entry_bin="dentry.bin",
        choices=[1] * 30,
        expected_status="finished",
        expected_deleted=["sayori"],
        min_lines=3000,
    )
    results.append(r)

    # 1.3 Act 1 Natsuki Minigame Winner & Exclusives
    # Winner=1 (Natsuki) for 3 poem games
    r = run_sim(
        "Act 1 Natsuki Route (Poem minigame winner Natsuki)",
        entry_bin="dentry.bin",
        minigames=[(1, 0, 3, 0), (1, 0, 3, 0), (1, 0, 3, 0)],
        choices=[0] * 30,
        expected_status="finished",
        expected_deleted=["sayori"],
        min_lines=3000,
    )
    results.append(r)

    # 1.4 Act 1 Yuri Minigame Winner & Exclusives
    # Winner=2 (Yuri) for 3 poem games
    r = run_sim(
        "Act 1 Yuri Route (Poem minigame winner Yuri)",
        entry_bin="dentry.bin",
        minigames=[(2, 0, 0, 3), (2, 0, 0, 3), (2, 0, 0, 3)],
        choices=[0] * 30,
        expected_status="finished",
        expected_deleted=["sayori"],
        min_lines=3000,
    )
    results.append(r)

    # 1.5 Act 2 Full Replay (persistent.playthrough = 1)
    # Start at label start with playthrough=1 -> calls ch10_main -> ch20..ch23
    r = run_sim(
        "Act 2 Full Replay (playthrough=1 from label start)",
        entry_bin="dentry.bin",
        vars_set={9: 1},  # slot 9: persistent.playthrough = 1
        choices=[0] * 40,
        minigames=[(1, 0, 3, 0), (2, 0, 0, 3)],
        expected_status="finished",
        min_lines=1500,
    )
    results.append(r)

    # 1.6 Act 2 with Yuri confession "Yes"
    r = run_sim(
        "Act 2 Yuri Confession 'Yes' (choice 0)",
        entry_bin="dentry.bin",
        vars_set={9: 1},
        choices=[0] * 40,
        expected_status="finished",
        min_lines=1500,
    )
    results.append(r)

    # 1.7 Act 2 with Yuri confession "No"
    r = run_sim(
        "Act 2 Yuri Confession 'No' (choice 1)",
        entry_bin="dentry.bin",
        vars_set={9: 1},
        choices=[1] * 40,
        expected_status="finished",
        min_lines=1500,
    )
    results.append(r)

    # 1.8 Act 3 Just Monika (persistent.playthrough = 2)
    # Start at label start with playthrough=2 -> calls ch30_main. Act 3 is an
    # intentional infinite loop until Monika is deleted, so it times out at max_lines.
    if "ch30" in chapters:
        r = run_sim(
            "Act 3 Just Monika (ch30_main entry point, intentional loop)",
            pc=chapters["ch30"],
            vars_set={9: 2},
            choices=[0] * 20,
            max_lines=200,
            expected_status="quit",
            min_lines=10,
        )
        results.append(r)

    # 1.9 Act 4 Normal Ending (persistent.playthrough = 4)
    # Start at label start with playthrough=4 -> calls ch40_main
    if "ch40" in chapters:
        r = run_sim(
            "Act 4 Normal Ending (ch40_main, persistent.clearall=0)",
            pc=chapters["ch40"],
            vars_set={9: 4, 63: 0},
            choices=[0] * 10,
            expected_status="finished",
            min_lines=200,
        )
        results.append(r)

    # 1.10 Act 4 Good Ending (persistent.playthrough = 4, clearall=1)
    if "ch40" in chapters:
        r = run_sim(
            "Act 4 Good / Dan Salvato Ending (ch40_main, persistent.clearall=1)",
            pc=chapters["ch40"],
            vars_set={9: 4, 63: 1},  # slot 63: persistent.clearall = 1
            choices=[0] * 10,
            expected_status="finished",
            min_lines=200,
        )
        results.append(r)

    # -------------------------------------------------------------
    # Suite 2: Character Deletion Paths
    # -------------------------------------------------------------
    print("\n--- Running Suite 2: Character Deletion Paths ---")

    # 2.1 Monika deleted before Act 1 start (ch0_kill)
    # When monika.chr is deleted before starting the game, label start detects it and jumps to ch0_kill
    r = run_sim(
        "Monika deleted before start (ch0_kill early bad ending)",
        entry_bin="dentry.bin",
        absent=["monika"],
        expected_status="finished",
        expected_quit=True,
        min_lines=10,
    )
    results.append(r)

    # 2.2 Sayori deleted before start (s_kill_early splashscreen)
    # When sayori.chr is deleted before starting, splashscreen jumps to s_kill_early
    if "splash" in chapters:
        r = run_sim(
            "Sayori deleted before start (s_kill_early splash screen)",
            entry_bin="dsplash.bin",
            absent=["sayori"],
            expected_status="finished",
            expected_quit=True,
            min_lines=4,
        )
        results.append(r)

    # 2.3 Monika deleted in Act 3 (ch30_end)
    if "ch30" in chapters:
        # Starting ch30 with Monika absent should trigger Monika deletion speech (ch30_end)
        r = run_sim(
            "Monika deleted in Act 3 (ch30_end final speech)",
            pc=chapters["ch30"],
            absent=["monika"],
            vars_set={9: 2},
            expected_status="finished",
            expected_deleted=["monika"],
            min_lines=50,
        )
        results.append(r)

    # 2.4 Both Monika and Sayori deleted before start
    r = run_sim(
        "Both Monika and Sayori deleted before start",
        entry_bin="dentry.bin",
        absent=["monika", "sayori"],
        expected_status="finished",
        expected_quit=True,
        min_lines=5,
    )
    results.append(r)

    # -------------------------------------------------------------
    # Suite 3: Individual Chapters from DCHJMP
    # -------------------------------------------------------------
    print("\n--- Running Suite 3: Individual Chapters from DCHJMP ---")
    for ch_name, ch_addr in sorted(chapters.items()):
        is_ch30 = (ch_name == "ch30")
        is_non_dialogue = ch_name in ("poemgame", "poems", "poems_special")
        r = run_sim(
            f"Chapter direct jump: {ch_name}",
            pc=ch_addr,
            choices=[0] * 30,
            max_lines=200 if is_ch30 else 10000,
            expected_status="quit" if is_ch30 else "finished",
            min_lines=0 if is_non_dialogue else (10 if is_ch30 else 1),
        )
        results.append(r)

    # -------------------------------------------------------------
    # Suite 4: Easter Eggs, Glitches, and Special Scenes
    # -------------------------------------------------------------
    print("\n--- Running Suite 4: Easter Eggs & Glitches ---")

    # 4.1 Splashscreen Normal (seeds 1 to 10)
    for seed in [1, 2, 5, 42, 100]:
        r = run_sim(
            f"Splashscreen execution (seed={seed})",
            entry_bin="dsplash.bin",
            seed=seed,
            expected_status="finished",
            min_lines=1,
        )
        results.append(r)

    # 4.2 Ghost Menu test (seed in Act 2)
    # Ghost menu has a 1/64 chance when persistent.playthrough == 1 or 2 on splashscreen
    # Let's test splashscreen with playthrough=1 across several seeds
    for seed in [1, 7, 64, 128]:
        r = run_sim(
            f"Splashscreen in Act 2 (seed={seed}, playthrough=1)",
            entry_bin="dsplash.bin",
            vars_set={9: 1},
            seed=seed,
            expected_status="finished",
            min_lines=1,
        )
        results.append(r)

    # 4.3 Special Poems (poems_special chunk / direct jump)
    if "poems_special" in chapters:
        for seed in [1, 2, 3, 4, 5]:
            r = run_sim(
                f"Special Poem dispatch (seed={seed})",
                pc=chapters["poems_special"],
                seed=seed,
                expected_status="finished",
                min_lines=0,
            )
            results.append(r)

    # 4.4 Yuri Kill Dynamic Jump Labels (DVLBL entries: 9, 10, 11)
    for lbl_id, name in [(9, "yuri_kill_1"), (10, "yuri_kill_2"), (11, "yuri_kill_3")]:
        if lbl_id in labels:
            r = run_sim(
                f"Dynamic Label: {name} (str_id {lbl_id})",
                pc=labels[lbl_id],
                expected_status="finished",
                min_lines=1,
            )
            results.append(r)

    # 4.5 Credits and Post-credits Loop
    for lbl_id, name in [(41, "credits"), (42, "postcredits_loop")]:
        if lbl_id in labels:
            r = run_sim(
                f"Dynamic Label: {name} (str_id {lbl_id})",
                pc=labels[lbl_id],
                expected_status="finished",
                min_lines=1,
            )
            results.append(r)

    return results


def main() -> int:
    results = run_all_tests()

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    print("\n" + "=" * 70)
    print(f"TEST RESULTS SUMMARY: {passed}/{total} PASSED ({failed} FAILED)")
    print("=" * 70)

    for r in results:
        status_symbol = "✓" if r.passed else "✗"
        print(f"[{status_symbol}] {r.name:60s} lines={r.lines:<5d} menus={r.menus:<2d} status={r.status}")
        if not r.passed:
            print(f"    ERROR: {r.error_message}")
        if r.warnings:
            for w in r.warnings:
                print(f"    {w}")

    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
