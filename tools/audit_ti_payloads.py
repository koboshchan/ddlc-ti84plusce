#!/usr/bin/env python3
"""Strict TI-84 Plus CE variable & bundle payload auditor.

Format reference: https://merthsoft.com/linkguide/ti83+/fformat.html
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _cedev import find_cedev_tool

LIMIT = 65505


def parse(blob: bytes, source: str) -> list[dict]:
    """Validate and parse every variable entry in a raw `.8xp`/`.8xv` container."""
    def u16(offset: int) -> int:
        if offset + 2 > len(blob):
            raise ValueError("truncated length")
        return struct.unpack_from("<H", blob, offset)[0]

    if blob[:11] != b"**TI83F*\x1a\x0a\x00":
        raise ValueError("invalid TI signature")

    end = 55 + u16(53)
    if end + 2 != len(blob):
        raise ValueError("file section length mismatch")
    if (sum(blob[55:end]) & 0xFFFF) != u16(end):
        raise ValueError("checksum mismatch")

    rows: list[dict] = []
    pos = 55
    while pos < end:
        hdr_len = u16(pos)
        data_len = u16(pos + 2)
        if hdr_len not in (11, 13):
            raise ValueError("unsupported entry header")
        data_pos = pos + 2 + hdr_len
        if data_pos + 2 + data_len > end or u16(data_pos) != data_len:
            raise ValueError("entry bounds/duplicate length mismatch")

        data = blob[data_pos + 2 : data_pos + 2 + data_len]
        var_type = blob[pos + 4]
        if var_type not in (5, 6, 21):
            raise ValueError("unexpected variable type")
        if data_len < 2 or int.from_bytes(data[:2], "little") != data_len - 2:
            raise ValueError("internal program/AppVar length mismatch")

        name = blob[pos + 5 : pos + 13].rstrip(b"\0").decode("ascii")
        rows.append({
            "source": source,
            "name": name,
            "type": var_type,
            "data_bytes": data_len,
            "content_bytes": data_len - 2,
            "headroom": LIMIT - data_len,
            "passes": data_len <= LIMIT,
        })
        pos = data_pos + 2 + data_len

    if pos != end or not rows:
        raise ValueError("invalid entry count")
    return rows


def audit_bundle(bundle_path: Path, engine_path: Path | None = None) -> dict:
    """Audit a `.b84` bundle ZIP and optionally cross-check its split engine parts."""
    rows: list[dict] = []
    errors: list[dict] = []
    dependency_checks: list[dict] = []

    with zipfile.ZipFile(bundle_path) as zf:
        for name in zf.namelist():
            if name.endswith("/") or name in ("METADATA", "_CHECKSUM"):
                continue
            try:
                rows.extend(parse(zf.read(name), name))
            except Exception as exc:
                errors.append({"source": name, "error": str(exc)})

    if engine_path is not None:
        parts = sorted(engine_path.parent.glob(f"{engine_path.name}.*.8xv"))
        loader = engine_path.read_bytes()
        entry_start = 55 + 2 + int.from_bytes(loader[55:57], "little") + 2
        loader_data = loader[entry_start:-2]

        convbin = find_cedev_tool("convbin", env_var="CONVBIN_REAL")
        with tempfile.TemporaryDirectory() as tmp:
            raw_path = Path(tmp) / "engine.bin"
            subprocess.run(
                [
                    str(convbin),
                    "-j", "elf",
                    "-i", str(engine_path.with_suffix(".obj")),
                    "-k", "bin",
                    "-o", str(raw_path),
                ],
                check=True,
                capture_output=True,
            )
            raw = raw_path.read_bytes()

        if parts:
            required: list[str] = []
            pos = len(loader_data) - 10
            while pos >= 0 and loader_data[pos] == 21:
                req_name = loader_data[pos + 1 : pos + 9].rstrip(b"\0").decode("ascii")
                required.insert(0, req_name)
                pos -= 9

            actual = [parse(p.read_bytes(), str(p))[0]["name"] for p in parts]

            chunks: list[bytes] = []
            for part in parts:
                part_bytes = part.read_bytes()
                start = 55 + 2 + int.from_bytes(part_bytes[55:57], "little") + 4
                chunks.append(part_bytes[start:-2])

            if raw[:2] != b"\xef\x7b" or b"".join(chunks) != raw[2:]:
                errors.append({
                    "source": str(engine_path),
                    "error": "code parts differ from contiguous current ELF engine after ASM tokens",
                })
            if len(required) != 2 or actual != required:
                errors.append({
                    "source": str(engine_path),
                    "error": f"exactly two loader code dependencies required; loader={required!r} built={actual!r}",
                })
        else:
            if raw[:2] != b"\xef\x7b" or loader_data[2:] != raw:
                errors.append({
                    "source": str(engine_path),
                    "error": "unsplit engine binary differs from current ELF output",
                })

        with zipfile.ZipFile(bundle_path) as zf:
            names = set(zf.namelist())
            if engine_path.name not in names or zf.read(engine_path.name) != loader:
                errors.append({
                    "source": str(engine_path),
                    "error": "packaged engine does not match actual binary",
                })
            for part in parts:
                entries = parse(part.read_bytes(), str(part))
                dependency_checks.extend(entries)
                if part.name not in names:
                    errors.append({
                        "source": str(part),
                        "error": "missing loader-required engine AppVar",
                    })
                elif zf.read(part.name) != part.read_bytes():
                    errors.append({
                        "source": str(part),
                        "error": "engine AppVar differs from built code",
                    })
                if any(not entry["passes"] for entry in entries):
                    errors.append({
                        "source": str(part),
                        "error": "engine AppVar DATA exceeds cap",
                    })

    mains = [r for r in rows if r["name"] == "DDLC" and r["type"] in (5, 6)]
    apps = [r for r in rows if r["type"] == 21]
    all_pass = not errors and len(mains) == 1 and bool(apps) and all(r["passes"] for r in rows)

    def _category(row: dict) -> str:
        name = row["name"]
        if name in ("DDLC", "DDLC0", "DDLC1"):
            return "engine"
        if name == "DPKIDX" or name.startswith("DPK"):
            return "asset_pack"
        if name.startswith("DSPR"):
            return "sprites"
        if name.startswith("DSCN"):
            return "scenes"
        if name.startswith("DSCR"):
            return "scripts"
        if name.startswith("DMOT") or name == "DLUTQ":
            return "ch5_motion"
        if name.startswith("DCG"):
            return "cg_palettes"
        if name.startswith("DTIL") or name == "DPALTTL":
            return "title"
        if name.startswith("DPOEM") or name == "DSTKRS":
            return "poems"
        return "ui_fonts_tables"

    size_table: dict[str, dict] = {}
    for row in rows:
        cat = _category(row)
        entry = size_table.setdefault(cat, {"count": 0, "data_bytes": 0, "max_var_bytes": 0})
        entry["count"] += 1
        entry["data_bytes"] += row["data_bytes"]
        if row["data_bytes"] > entry["max_var_bytes"]:
            entry["max_var_bytes"] = row["data_bytes"]

    flash_budget_bytes = 3 * 1024 * 1024
    flash_total_bytes = sum(r["data_bytes"] for r in rows)

    return {
        "bundle": str(bundle_path),
        "sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        "compressed_bytes": bundle_path.stat().st_size,
        "limit": LIMIT,
        "measurement": "variable data including internal 2-byte length, excluding file/entry wrappers",
        "engine_dependencies": dependency_checks,
        "engine_unsplit": not dependency_checks,
        "all_pass": all_pass,
        "variable_count": len(rows),
        "flash_total_bytes": flash_total_bytes,
        "flash_budget_bytes": flash_budget_bytes,
        "flash_headroom_bytes": flash_budget_bytes - flash_total_bytes,
        "size_table": size_table,
        "main": mains,
        "largest_appvar": max(apps, key=lambda r: r["data_bytes"]) if apps else None,
        "variables": rows,
        "errors": errors,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bundle", type=Path, help="path to .b84 bundle")
    ap.add_argument("--out", type=Path, required=True, help="JSON audit output path")
    ap.add_argument("--engine", type=Path, help="path to built DDLC.8xp to verify code split")
    args = ap.parse_args()

    result = audit_bundle(args.bundle, args.engine)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")

    summary = {
        k: result[k]
        for k in ("all_pass", "variable_count", "flash_total_bytes",
                  "flash_headroom_bytes", "main", "largest_appvar", "errors")
    }
    print(json.dumps(summary))
    print("\nCategory           Count    Total Bytes   Max Var")
    print("----------------  ------  -------------  --------")
    for cat, stats in sorted(result["size_table"].items(), key=lambda kv: -kv[1]["data_bytes"]):
        print(f"{cat:<16}  {stats['count']:>6}  {stats['data_bytes']:>13,}  {stats['max_var_bytes']:>8,}")
    print(f"{'TOTAL':<16}  {result['variable_count']:>6}  {result['flash_total_bytes']:>13,}  (headroom: {result['flash_headroom_bytes']:,} B / 3 MiB)")
    return 0 if result["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
