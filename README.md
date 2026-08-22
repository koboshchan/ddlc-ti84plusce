# ddlc-ti84plusce

A visual novel engine for the TI-84 Plus CE, built with the
[CE C/C++ toolchain](https://github.com/CE-Programming/toolchain), plus a local
pipeline that converts copies of Doki Doki Literature
Club into calculator-ready assets.

## Building the engine

Requires the CE toolchain. If `cedev-config` is not on your `PATH`:

```bash
export PATH="$HOME/CEdev/bin:$PATH"
```

Then:

```bash
make
```

This produces `bin/DDLC.8xp`. On its own it shows an "assets not found" screen
— it needs the AppVars from the asset pipeline too (see below) to have
anything to play.

### Controls

| Key | Action |
|---|---|
| `2nd` / `enter` | Advance text, confirm a choice |
| `up` / `down` | Move between menu options |
| `mode` | Pause menu (in-game); cancel out of a submenu |
| `clear` | Quit |

The name-entry screen (first launch only) is typed directly on the keypad,
same as it would be in the TI-OS itself -- letters follow the printed ALPHA
labels (`MATH`=A, `APPS`=B, `PRGM`=C, …), plus `del` to erase and `enter` to
confirm.

## Building the full bundle

The asset pipeline needs its own Python environment (Pillow, PyYAML, unrpa)
and `convimg`/`convbin` from the CE toolchain. A one-time setup:

```bash
python3 -m venv .venv
.venv/bin/pip install -r tools/requirements.txt
export PATH="$HOME/CEdev/bin:$(pwd)/.venv/bin:$PATH"
```

Then, against your own legally obtained copy of the game:

```bash
make bundle GAME_DIR=/path/to/DDLC-1.1.1-pc/game
```

Outputs `build/DDLC.b84`, a bundle containing the engine and all assets, ready to upload to a calculator or CEmu.

## Building with Docker

If you prefer building inside a container without manually installing CEdev or Python dependencies, you can build everything with Docker:

### Direct BuildKit Export (Recommended)

Run `docker build` with `--build-arg GAME_DIR=...` and `-o build` to output all artifacts (`DDLC.b84`, `DDLC.8xp`, `cgpack/`, and `transfer_files/`) directly into your local `build/` directory:

```bash
docker build --build-arg GAME_DIR=/path/to/DDLC-1.1.1-pc/game -o build .
```

*(Note: `GAME_DIR` can be a path relative to the current directory or inside the build context).*

### Container Run Workflow

Alternatively, build the runner image once and mount your game directory:

```bash
# 1. Build the builder image
docker build --target runner -t ddlc-builder .

# 2. Run the build with mounted folders
docker run --rm -v /path/to/DDLC-1.1.1-pc/game:/game -v $(pwd)/build:/app/build ddlc-builder
```

## Full-resolution CG pack (optional)

CGs ship on-calc at half resolution to help fit the archive budget above —
softer than a background, a deliberate tradeoff. `make bundle` also writes
`build/cgpack/`, a full-resolution version of every CG. Copy its contents
onto a FAT32-formatted USB drive as a `/DDLC/` folder and connect it to the
calculator via a USB-OTG-to-flash-drive adapter, and every CG draws at full
resolution instead — no drive, or a drive without a matching pack (one built
from a different `--files` selection won't match: the pack is checked
against the exact build it came from), and the game falls back to the
built-in half-res version, exactly as if this feature didn't exist. See
docs/FORMAT.md's "External full-res CG pack" for how the matching works.

## Loading it onto a calculator or CEmu

Just upload `build/DDLC.b84` to a calculator or CEmu.

## License

Code for the engine and the asset pipeline is licensed under the MIT License. See [LICENSE](LICENSE) for details. Any DDLC assets you use must be obtained separately and legally, in accordance with Team Salvato's IP Guidelines.

Not affiliated with or endorsed by Team Salvato.
