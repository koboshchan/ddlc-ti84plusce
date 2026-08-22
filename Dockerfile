# syntax=docker/dockerfile:1

ARG TARGETPLATFORM=linux/amd64

# -----------------------------------------------------------------------------
# Stage 1: Toolchain Base
# Sets up Ubuntu amd64, CEdev v15.0, Python 3, and all pipeline requirements
# -----------------------------------------------------------------------------
FROM --platform=${TARGETPLATFORM} ubuntu:22.04 AS toolchain

ENV DEBIAN_FRONTEND=noninteractive
ENV CEDEV_VERSION=v15.0
ENV CEDEV_URL="https://github.com/CE-Programming/toolchain/releases/download/${CEDEV_VERSION}/CEdev-Linux.tar.gz"
ENV PATH="/opt/CEdev/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    tar \
    python3 \
    python3-pip \
    python3-venv \
    libtinfo5 \
    libtinfo6 \
    libncurses5 \
    libncurses6 \
    && rm -rf /var/lib/apt/lists/*

# Install CEdev toolchain
RUN mkdir -p /opt/CEdev && \
    curl -sSL "${CEDEV_URL}" | tar -xz -C /opt/CEdev --strip-components=1 && \
    cedev-config --version

WORKDIR /app
COPY tools/requirements.txt /app/tools/requirements.txt
RUN pip3 install --no-cache-dir -r /app/tools/requirements.txt

# -----------------------------------------------------------------------------
# Stage 2: Standalone Runner
# Image target for containerized runs (docker run -v /path/to/game:/game ...)
# -----------------------------------------------------------------------------
FROM toolchain AS runner
WORKDIR /app
COPY Makefile icon.png docker-entrypoint.sh ./
COPY src/ ./src/
COPY tools/ ./tools/
COPY assets/ ./assets/
RUN make bin/DDLC.8xp
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# -----------------------------------------------------------------------------
# Stage 3: Engine & Bundle Builder
# Builds DDLC.8xp and runs the asset pipeline against the provided GAME_DIR
# -----------------------------------------------------------------------------
FROM toolchain AS builder

ARG GAME_DIR=game
ARG IMPORT_FLAGS=""

WORKDIR /app
COPY Makefile icon.png docker-entrypoint.sh ./
COPY src/ ./src/
COPY tools/ ./tools/
COPY assets/ ./assets/
COPY ${GAME_DIR} /game

# Build engine binary
RUN make bin/DDLC.8xp

# Build game bundle (.b84), transfer files, and full-resolution CG pack
RUN make bundle GAME_DIR=/game IMPORT_FLAGS="${IMPORT_FLAGS}" && \
    cp bin/DDLC.8xp build/

# -----------------------------------------------------------------------------
# Stage 4: Artifact Exporter (Default Final Stage)
# Exports the build directory directly to the host using BuildKit (-o build)
# -----------------------------------------------------------------------------
FROM scratch AS export
COPY --from=builder /app/build /
