# `tools/` — Asset Pipeline & Developer Utilities

All scripts in `tools/` operate against your own local DDLC `game/` directory and output exclusively to gitignored directories (`assets/` and `build/`).

## Build & Asset Pipeline

Orchestrated end-to-end by `make bundle GAME_DIR=/path/to/DDLC-1.1.1-pc/game` (or `python3 tools/import_game.py`):

| File | Role |
| :--- | :--- |
| [import_game.py](import_game.py) | Top-level pipeline driver: extracts archives, compiles scripts, converts images/fonts, packages AppVars, optimizes flash sector order, and builds `build/DDLC.b84`. |
| [extract.py](extract.py) | Extracts `scripts.rpa`, `images.rpa`, and `fonts.rpa` into `assets/raw/` via `unrpa`. |
| [rpyc_ast.py](rpyc_ast.py) | Safe allowlist-unpickler for Ren'Py `.rpyc` AST containers; also runnable as a CLI AST/ATL inspector (`python3 tools/rpyc_ast.py <file.rpyc> [--lines LO HI] [--tree]`). |
| [compile_script.py](compile_script.py) | Compiles Ren'Py AST nodes, expressions, menus, and transforms into chunked VM bytecode. |
| [vnasm.py](vnasm.py) | Bytecode assembler, label linker, and `.vnb` / C-array serializer matching `src/vn.h`. |
| [image_resolve.py](image_resolve.py) | Resolves Ren'Py `image` / `Show` / `Scene` definitions into pre-cropped sprites, layered CGs, stickers, and UI art (`build/manifest.json`). |
| [convert_images.py](convert_images.py) | Generates `build/convimg.yaml` and runs `convimg` to quantize and ZX0-compress game graphics. |
| [ttf_to_convfont.py](ttf_to_convfont.py) & [convert_fonts.py](convert_fonts.py) | Rasterizes `Aller_Rg.ttf` and `RifficFree-Bold.ttf` (plus cardinal outline masks) into the `DFONTS` `fontlibc` pack. |
| [ch5_motion_assets.py](ch5_motion_assets.py), [ch5_fg16_lut.py](ch5_fg16_lut.py), [ch5_blend_luts.py](ch5_blend_luts.py) | Builds ZX0-compressed 8-row motion bands (`DMOT0..3`), palette maps (`DMOTPAL`, `DMOTRGB`), and foreground blend LUTs (`DLUTQ`) for Chapter 5. |
| [export_cgpack.py](export_cgpack.py) | Exports full-resolution (320x180) CG binaries and optional timed sequences to `build/cgpack/` for FAT32 USB streaming (`src/cgpack.c`). |
| [balanced_convbin.py](balanced_convbin.py) & [loader/](loader/) | Wraps `convbin` during `make` to split the engine across `DDLC0`/`DDLC1` and inject a CRC-32 preflight guard (`loader/integrity.s`) into `DDLC.8xp`. |
| [audit_ti_payloads.py](audit_ti_payloads.py) | Verifies `.8xp`/`.8xv`/`.b84` headers, checksums, 64 KB variable caps, and split-engine integrity. |
| [_cedev.py](_cedev.py) | Shared cross-platform locator for CEdev toolchain binaries (`$CEDEV`, `cedev-config`, `PATH`, `~/CEdev`, `/opt/CEdev`). |
| [poem_catalog.json](poem_catalog.json) | Metadata/layout catalog packaged into `DPOEMT`. |

## Standalone Verification & Dev Utilities

- **[host_sim/](host_sim/)**: Native C host build of `src/vn.c` + `src/text.c` (`make -C tools/host_sim`) and full multi-act playthrough verifier ([host_sim/test_suite.py](host_sim/test_suite.py)).
- **[asm_tests/](asm_tests/)**: On-target / CEmu eZ80 assembly edge-case harness (`make -C tools/asm_tests`) exercising `src/fast_ops.s`.
- **[check_plate.py](check_plate.py)**: Pixel-exact host verifier comparing `src/render.c`'s moving-actor dirty-plate sequence against full-scene redraws (`python3 tools/check_plate.py`).
- **[gen_demo.py](gen_demo.py)**: Regenerates the self-contained placeholder bytecode in `src/demo.c`.
- **[render_timed_reference.py](render_timed_reference.py)**: Offline NumPy reference compositor/quantizer for USB timed motion frame verification (`pip install numpy`).
