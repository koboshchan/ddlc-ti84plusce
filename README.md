![DDLC calculator icon](icon.png)

**ddlc-ti84plusce**

An unofficial Doki Doki Literature Club fan port for the TI-84 Plus CE. The C/eZ80 engine handles dialogue, choices, save/load, poems and visual effects; the Python pipeline converts your local game copy into calculator AppVars.

> [!IMPORTANT]
> Bring your own legally obtained DDLC game files. Game assets and calculator ROMs are not distributed here. Emulation requires your own ROM.

**Build a playable bundle**

Install the [CE C/C++ toolchain](https://github.com/CE-Programming/toolchain) and Python 3 with venv support. The pipeline uses `convimg` and `convbin` from CEdev, plus the dependencies in [tools/requirements.txt](tools/requirements.txt). The current split-program wrapper expects CEdev at `$HOME/CEdev`.

```sh
git clone https://github.com/koboshchan/ddlc-ti84plusce.git
cd ddlc-ti84plusce
python3 -m venv .venv
.venv/bin/pip install -r tools/requirements.txt
export PATH="$HOME/CEdev/bin:$(pwd)/.venv/bin:$PATH"

make bundle GAME_DIR=/path/to/DDLC-1.1.1-pc/game IMPORT_FLAGS=--files=all
```

Use the original game's `game` directory. `--files=all` selects all supported script files; omitting it builds only the default `script,script-ch0` selection. `--files=act1` selects chapters 0 through 4. Script selection is not a claim of complete gameplay compatibility.

The output is `build/DDLC.b84`, with individual transfer files in `build/transfer_files/` and the optional external CG pack in `build/cgpack/`. The bundler checks variable sizes and the uncompressed archive budget. The compressed bundle's file size is not its on-calculator storage requirement.

`make` alone builds `bin/DDLC.8xp` and its split code AppVars, but not the game assets. That program alone is not a playable installation.

**Install and launch**

Transfer `build/DDLC.b84` to the calculator or CEmu, then transfer the CE C libraries (`clibs.8xg`) and the arTIfiCE jailbreak launcher. Run program `A`, then choose `DDLC`.

Keep the bundle together. `DDLC0` and `DDLC1` are required executable code AppVars, not optional assets. If your transfer tool cannot read the bundle, transfer every file from `build/transfer_files/` instead.

> [!TIP]
> For a clean emulator test, reset RAM and reload your own ROM before sending the bundle, libraries and launcher. Missing assets or a code-part mismatch should be fixed by resending the complete matching build, not just `DDLC.8xp`.

**Controls**

Press `2nd` or `enter` to advance text and confirm; use `up` and `down` to select menu options. `mode` opens the pause menu or cancels a submenu, and `clear` quits.

On first launch, enter your name with the keypad's printed ALPHA letter labels. `del` erases and `enter` confirms.

**Optional USB CG pack**

Copy the contents of `build/cgpack/` into a `/DDLC/` folder on a FAT32 USB drive, then connect it with a compatible USB-OTG adapter. The pack must come from the same bundle build. Without a matching pack, the engine uses the built-in half-resolution CGs.

Physical USB attachment and hotplug have not been verified in the documented emulator checks. See [the format documentation](docs/FORMAT.md#external-full-res-cg-pack) for the layout and matching rules.

**Development and tests**

Enter `up up down down left left right right` at the title screen or during play to open the debug menu. It includes rendering tests, animation previews, assembly diagnostics and in-game chapter jumps. Save-erasure and character-state controls change game data; use a separate test save.

The performance overlay is not part of `main` at present. Experimental performance builds on the development branch add a debug-menu toggle when compiled with `DDLC_PERF_OVERLAY`.

With the Python environment active and a host C compiler installed, run the regression suite with

```sh
python -m unittest discover -s tests -v
```

Host tests do not establish calculator timing or hardware compatibility. See [optimization verification](docs/OPTIMIZATION-VERIFICATION.md), [assembly tests](tools/asm_tests/README.md) and [asset/bytecode formats](docs/FORMAT.md) for the separate target checks and engine internals.

**License and credits**

Original engine code, tools and build files are covered by the [MIT License and IP disclaimer](LICENSE). Doki Doki Literature Club and its assets belong to Team Salvato; obtain them separately and follow [Team Salvato's IP Guidelines](https://teamsalvato.com). This is a non-commercial fan work, not affiliated with or endorsed by Team Salvato.
