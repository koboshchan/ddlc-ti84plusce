# Optimization verification

Built and tested on the TI-84 Plus CE Python ROM supplied locally by the owner. No ROM or game-derived assets are committed.

## Notebook storage and redraw

Final full-bundle on-calc usage is 2,800,754 bytes versus baseline 2,862,538, saving 61,784 bytes (2.16%). ZIP-compressed transfer size is 1,976,761 bytes and is not the archive usage.

The notebook stream is 7,164 bytes rather than 76,800 raw bytes, a 69,636-byte reduction before variable overhead. DPOEMBG decodes into the existing graphics framebuffer. Old split DPOEMBG0/1 bundles remain readable and their sizes are checked. Neither minigame nor poem viewer redraws while waiting for unchanged input. The original final-page prompt, scrolling, page overlap, and dismissal behavior are retained.

Emulator checks cover minigame entry, selection movement, and unchanged output after five idle seconds. Notebook pixels outside the randomized word area match baseline. Dear Sunshine viewer entry, final-page scrolling, backwards scrolling, Enter paging, and dismissal passed. Backward scrolling restores the exact first-page screen hash, and five idle seconds leave it unchanged.

## Outline masks

Font indexes 0/1 retain the original fill glyphs and metrics. Indexes 2/3 add pre-rendered cardinal-dilated masks. Drawing uses one outline pass and one fill pass instead of four offset passes plus fill. Explicit fontlib italic adjustment cancels the two extra bitmap columns; SetFont does not initialize this state itself. Old bundles and screen-edge cases use the original path. Wrapping and typewriter prefix lengths still use fill-font widths.

The existing dialogue rendering test screenshot is pixel-identical to baseline. A temporary benchmark rendered the same 39-character outlined string 100 times, using clock() with CLOCKS_PER_SEC=32768. Baseline was 70,584 ticks, optimized 34,719 ticks. That is 2.033x speedup and 50.81% less time for the outlined-text primitive, not the entire frame. Temporary instrumentation is not shipped.

## USB allocation

USB_USE_C_HEAP is deliberately not selected. It requires a heap ending at D10000, incompatible with this application's layout. The C heap now ends at D13FD8, before usbArea, and the OS DMA pool remains above it. Permanent textbox/namebox RAM copies are removed; immutable archived pixels are copied directly to their destination within one open/copy/close sequence. The CG decode uses framebuffer rows 180..224 as scratch, disjoint from its 180-row output. All assets_scene destinations must consequently be full 320x240 framebuffers.

The final linker map places the heap at D0A924..D13FD8. The old layout was D12828..D1987E: reducing BSS more than compensates for removing the overlapping extension. usb_Init now succeeds in the emulator, and no-drive operation, title, dialogue, and internal CG fallback are tested. Physical mass-storage attachment, FAT mounting, hotplug, BUILD.ID validation and full-resolution reads are NOT verified because no physical USB drive is exposed to this environment.

## Assembly audit

All six routines run in ADL=1 with 3-byte arguments/return addresses. IX and IY saves match stack offsets, local allocations are balanced, and EXX returns to the main register bank on every exit. General registers and flags are caller-clobbered. None changes interrupt enable state, uses self-modifying code, or depends on ambient carry. EXX temporarily uses alternate BC/DE/HL, as CEdev library routines do; interrupts must preserve both banks. No exhaustive asynchronous interrupt injection or every 64KB boundary is claimed. ADL LDIR and INC use 24-bit addresses, and accesses are byte-oriented with no alignment requirement. Source/destination memory must be valid and non-overlapping; pitches and frame clipping remain caller responsibilities.

Fixed 24-bit zero-counter tests, the rectangle's truncated 8-bit height, and the horizontal resampler's inability to advance multiple source pixels during downscaling. Sprite halving is intentionally 16-bit because the documented dimensions fit 16 bits. Row scaler output counts are limited to 320 pixels, denominators must be nonzero and the initial accumulator smaller than the denominator.

The existing emulator assembly suite passes all five displayed tests. The additional standalone edge harness passes 344 checks with zero failures: counts 0/1/255/256/320, up/equal/downscaled transparent rows and sentinel guards, zoom widths, all shifts 0..319, zero-width/height rectangles, height=256, and 2D up/downscaling. See tools/asm_tests.

## Reproducibility

Run Python tests with python -m unittest discover -s tests -v. They verify all 95 cardinal masks/advances, the notebook compression directive and converter cache invalidation. Changing converter configuration now invalidates image-cache output, avoiding reuse of a stale uncompressed notebook.

Build with the CEdev toolchain and the owner's extracted DDLC assets using make bundle GAME_DIR=/path/to/game IMPORT_FLAGS=--files=all. The emulator procedure resets RAM, starts from the ROM in a fresh process, sends the bundle, sends clibs.8xg, sends arTIfiCE, runs program A and selects DDLC.

## Story input audit

The actual story run starts from reset RAM and a fresh ROM process, loads the optimized full bundle, launches DDLC, and chooses New Game as kobosh. It does not use the debug-menu QA states. Automation advances with discrete Enter presses only; the emulator key command holds for 80ms then releases, and game input is edge-triggered. No skip feature, hold-to-skip, debug jump, or script/game-state edit is used. Resuming emulator checkpoints resumes this same clean run, not a preexisting progressed save. The separate debug test states above are never loaded into the story run. No skip-triggered restart was necessary.
