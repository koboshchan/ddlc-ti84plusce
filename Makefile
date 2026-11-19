# ----------------------------
# Makefile Options
# ----------------------------

NAME = DDLC
ICON = icon.png
DESCRIPTION = "Doki Doki Literature Club (fan port)"
COMPRESSED = NO
ARCHIVED = YES

CFLAGS = -Wall -Wextra -Oz
# Balanced contiguous code parts when >64KB; stock convbin extractor copies real part lengths.
# Split derived from actual token-stripped engine by wrapper.

ifeq ($(PERF_OVERLAY),1)
CFLAGS += -DDDLC_PERF_OVERLAY -DDDLC_DEBUG_MENU
else ifeq ($(DEBUG_MENU),1)
CFLAGS += -DDDLC_DEBUG_MENU
endif

CXXFLAGS = -Wall -Wextra -Oz

HAS_PRINTF = NO
HAS_LIBCXX = NO

.PHONY: build

# Keep C BSS/heap below usbdrvce's usbArea (D13FD8..D177B7) and
# OS-heap DMA pool (D18D00..D19800). UI art is streamed from flash and
# CG scratch uses the unused 60 framebuffer rows, reclaiming 34824 BSS
# bytes, so the stock boundary leaves MORE chunk heap than the old extension.
# USB_USE_C_HEAP is NOT used (requires ending C heap at D10000).
BSSHEAP_HIGH = 0xD13FD8

# ----------------------------

include $(shell cedev-config --makefile)
CONVBIN = python3 tools/balanced_convbin.py

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

.PHONY: bundle ddebug
bundle: $(BINDIR)/$(TARGET)
	$(if $(GAME_DIR),,$(error GAME_DIR is required, e.g. make bundle GAME_DIR=/path/to/DDLC-1.1.1-pc/game))
	$(PYTHON) tools/import_game.py "$(GAME_DIR)" --build-dir $(BUNDLE_DIR) \
		--prog $(BINDIR)/$(TARGET) $(IMPORT_FLAGS)

# Build standalone DDEBUG.8xv separately from production game assets.
# Sending build/DDEBUG.8xv onto a calculator causes DDLC.8xp to auto-load it.
DDEBUG_FLAGS ?= PERF_OVERLAY=1
DDEBUG_OUT   ?= $(BUNDLE_DIR)/DDEBUG.8xv

ddebug:
	$(MAKE) $(DDEBUG_FLAGS) OBJDIR=obj/ddebug BINDIR=bin/ddebug
	$(PYTHON) tools/build_ddebug.py --elf bin/ddebug/$(NAME).obj \
		--out $(DDEBUG_OUT) --report $(BUNDLE_DIR)/ddebug-build.json
