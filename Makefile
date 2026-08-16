# ----------------------------
# Makefile Options
# ----------------------------

NAME = DDLC
ICON = icon.png
DESCRIPTION = "Doki Doki Literature Club (fan port)"
COMPRESSED = YES
ARCHIVED = YES

CFLAGS = -Wall -Wextra -Oz
CXXFLAGS = -Wall -Wextra -Oz

# CEdev's stock BSSHEAP_HIGH (0xD13FD8) budgets the *combined* .bss + heap
# region -- the linker script places .bss starting at BSSHEAP_LOW and grows
# it upward, so the actual heap is only whatever's left below BSSHEAP_HIGH.
# This engine's own .bss (string-pool tables, static scratch buffers, the vn
# VM struct) runs ~52KB, leaving only ~7KB of real heap under the stock
# default -- nowhere near enough for a single chunk load (some compiled
# chunks decompress to ~20KB) once anything else has even a few KB
# allocated at the same time. That's the real cause of a chunk swap
# (Jump/Call crossing chunk boundaries -- see vn.h's "Chunk-packed
# addresses") intermittently failing with malloc() returning NULL, which
# src/assets.c's assets_load_chunk() correctly reports as a load failure,
# but which previously had no visible symptom beyond vn_step() hitting
# VN_ERR_BOUNDS immediately after.
#
# STACK_HIGH (0xD1A87E) sits right below where the program's own code loads
# (LOAD_ADDR, 0xD1A87F) -- moving BSSHEAP_HIGH up shrinks the stack instead,
# not free address space, so this is a straight heap-vs-stack tradeoff, not
# a free lunch. Tried 2KB of stack first (reasoning: no C recursion in the
# render/asset paths) but hit a real, reproducible crash-to-reset under it
# that 4KB doesn't show -- something on a real call path (graphx's own
# internal drawing routines are the likely suspect, not this engine's own
# code) needs more than 2KB after all, so 4KB is the real safe floor found
# so far, not just a conservative guess. That leaves ~29.5KB for the heap,
# instead of ~7KB under CEdev's stock default. Paired with
# tools/compile_script.py's CHUNK_SIZE_BUDGET (lowered alongside this --
# see its own comment, which had been tuned against a mistaken
# ~150KB-usable-RAM assumption instead of this actual linker-enforced
# heap).
BSSHEAP_HIGH = 0xD1987E

# ----------------------------

include $(shell cedev-config --makefile)

# ----------------------------
# Asset import / bundling
#
# `make bundle GAME_DIR=/path/to/DDLC-1.1.1-pc/game` builds the engine, then
# runs the asset import pipeline (tools/import_game.py) against your own
# legally obtained copy of the game, and packages the freshly built program
# together with every converted AppVar into one build/DDLC.b84. Depending on
# $(BINDIR)/$(TARGET) means the engine is always rebuilt first, so the
# bundle can never end up with a stale binary. See README.md, docs/FORMAT.md.
# ----------------------------

PYTHON       ?= python3
BUNDLE_DIR   ?= build
IMPORT_FLAGS ?=

.PHONY: bundle
bundle: $(BINDIR)/$(TARGET)
	$(if $(GAME_DIR),,$(error GAME_DIR is required, e.g. make bundle GAME_DIR=/path/to/DDLC-1.1.1-pc/game))
	$(PYTHON) tools/import_game.py "$(GAME_DIR)" --build-dir $(BUNDLE_DIR) \
		--prog $(BINDIR)/$(TARGET) $(IMPORT_FLAGS)
