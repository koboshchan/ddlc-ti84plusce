#!/usr/bin/env python3
"""convbin wrapper for DDLC.8xp: splits oversized engine into two balanced AppVars
and injects the CRC-32 loader integrity preflight (tools/loader/build_guard.py).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _cedev import find_cedev_tool
from loader.build_guard import apply as apply_build_guard

TI_PART_PAYLOAD_MAX = 65503


def _flag_value(args: list[str], flag: str) -> str | None:
    if flag in args:
        idx = args.index(flag)
        if idx + 1 < len(args):
            return args[idx + 1]
    return None


def _strip_flag_with_arg(args: list[str], flag: str) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == flag:
            i += 2
            continue
        out.append(args[i])
        i += 1
    return out


def main() -> int:
    args = sys.argv[1:]
    real_convbin = find_cedev_tool(
        "convbin", env_var="CONVBIN_REAL", exclude=Path(__file__)
    )

    if _flag_value(args, "-j") != "elf" or _flag_value(args, "-n") != "DDLC":
        return subprocess.call([str(real_convbin), *args])

    out_str = _flag_value(args, "-o")
    src_str = _flag_value(args, "-i")
    if not out_str or not src_str:
        raise SystemExit("balanced_convbin: missing -i or -o flag")

    out_path = Path(out_str)
    src_path = Path(src_str)

    with tempfile.TemporaryDirectory() as tmp_dir:
        raw_path = Path(tmp_dir) / "engine.bin"
        subprocess.run(
            [str(real_convbin), "-j", "elf", "-k", "bin", "-i", str(src_path), "-o", str(raw_path)],
            check=True,
        )
        data = raw_path.read_bytes()

    if not data.startswith(b"\xef\x7b"):
        raise SystemExit("balanced_convbin: engine lacks expected EF7B ASM token")

    raw_engine = data[2:]
    length = len(raw_engine)

    # Remove any stale split parts from previous builds before converting.
    for existing in out_path.parent.glob(f"{out_path.name}.*.8xv"):
        suffix = existing.name.removeprefix(f"{out_path.name}.").removesuffix(".8xv")
        if suffix.isdigit():
            existing.unlink()

    filtered = _strip_flag_with_arg(args, "-m")

    if length <= TI_PART_PAYLOAD_MAX:
        subprocess.run([str(real_convbin), *filtered], check=True)
        report = {
            "split": False,
            "raw_bytes": length,
            "engine_sha256": hashlib.sha256(raw_engine).hexdigest(),
            "parts": [],
        }
        (out_path.parent / "balanced-split.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
        return 0

    half = (length + 1) // 2
    if not 0 < half <= TI_PART_PAYLOAD_MAX:
        raise SystemExit(
            f"balanced_convbin: half-engine ({half} bytes) exceeds TI AppVar cap ({TI_PART_PAYLOAD_MAX})"
        )

    subprocess.run([str(real_convbin), *filtered, "-m", str(half)], check=True)

    parts = sorted(out_path.parent.glob(f"{out_path.name}.*.8xv"))
    expected_names = [f"{out_path.name}.0.8xv", f"{out_path.name}.1.8xv"]
    if [p.name for p in parts] != expected_names:
        raise SystemExit(
            f"balanced_convbin: expected {expected_names}, got {[p.name for p in parts]}"
        )

    apply_build_guard(out_path, parts, raw_engine)

    report = {
        "split": True,
        "raw_bytes": length,
        "target": half,
        "engine_sha256": hashlib.sha256(raw_engine).hexdigest(),
        "parts": [p.name for p in parts],
    }
    (out_path.parent / "balanced-split.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
