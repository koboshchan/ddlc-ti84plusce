#!/bin/bash
set -e

GAME_DIR="${GAME_DIR:-/game}"

if [ ! -d "$GAME_DIR" ]; then
    echo "ERROR: Game directory '$GAME_DIR' not found."
    echo "Please mount your DDLC 'game' folder to /game using: -v /path/to/DDLC/game:/game"
    exit 1
fi

echo "=== Building DDLC TI-84 Plus CE Engine ==="
make bin/DDLC.8xp

echo "=== Building Full Bundle and CG Pack ==="
make bundle GAME_DIR="$GAME_DIR" IMPORT_FLAGS="${IMPORT_FLAGS}"

# Copy engine binary into build directory for easy access
cp bin/DDLC.8xp build/

echo "=== Build Complete! Artifacts saved to build/ ==="
ls -lh build/
