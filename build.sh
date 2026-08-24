#!/bin/bash
set -e

# Default to first argument, environment variable, or fallback prompt
GAME_DIR="${1:-${GAME_DIR}}"
IMPORT_FLAGS="${2:-${IMPORT_FLAGS}}"

if [ -z "$GAME_DIR" ]; then
    echo "Usage: ./build.sh <path/to/DDLC/game> [extra_import_flags]"
    echo "Example: ./build.sh /path/to/DDLC-1.1.1-pc/game"
    exit 1
fi

echo "=== Building DDLC TI-84 Plus CE Bundle via Docker ==="
echo "Game directory : $GAME_DIR"
if [ -n "$IMPORT_FLAGS" ]; then
    echo "Import flags   : $IMPORT_FLAGS"
fi

DOCKER_BUILDKIT=1 docker build \
    --build-arg GAME_DIR="$GAME_DIR" \
    --build-arg IMPORT_FLAGS="$IMPORT_FLAGS" \
    -o build .

echo "=== Build complete! Artifacts exported to ./build ==="
