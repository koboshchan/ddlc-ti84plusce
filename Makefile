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
