"""Locate CEdev toolchain binaries portably across macOS, Linux, Windows, and Docker."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def cedev_prefix() -> Path | None:
    """Return the active CEdev installation prefix if discoverable."""
    env_dir = os.environ.get("CEDEV")
    if env_dir:
        p = Path(env_dir).expanduser()
        if p.is_dir():
            return p

    cfg = shutil.which("cedev-config")
    if cfg:
        res = subprocess.run([cfg, "--prefix"], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            p = Path(res.stdout.strip())
            if p.is_dir():
                return p

    for candidate in (Path.home() / "CEdev", Path("/opt/CEdev")):
        if candidate.is_dir():
            return candidate
    return None


def find_cedev_tool(
    name: str,
    *,
    env_var: str | None = None,
    subdir: str = "bin",
    exclude: Path | None = None,
) -> Path:
    """Resolve a CEdev binary (e.g. 'convbin', 'convimg', 'z80-none-elf-as').

    Checks:
      1. Explicit environment override (@p env_var) if set and not @p exclude.
      2. Active CEdev prefix (`$CEDEV`, `cedev-config --prefix`, `~/CEdev`, `/opt/CEdev`).
      3. Entries on `PATH` (skipping @p exclude so wrapper scripts never recurse).
    """
    suffix = ".exe" if os.name == "nt" and not name.endswith(".exe") else ""
    candidates: list[Path] = []

    if env_var and os.environ.get(env_var):
        candidates.append(Path(os.environ[env_var]).expanduser())

    prefix = cedev_prefix()
    if prefix is not None:
        candidates.append(prefix / subdir / f"{name}{suffix}")

    for path_dir in os.environ.get("PATH", "").split(os.pathsep):
        if path_dir:
            candidates.append(Path(path_dir) / f"{name}{suffix}")

    for cand in candidates:
        if exclude is not None and _same_file(cand, exclude):
            continue
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand

    hint = f" (or set {env_var})" if env_var else ""
    raise FileNotFoundError(
        f"{name} not found{hint}. Install CEdev and ensure <CEdev>/bin is on PATH."
    )
