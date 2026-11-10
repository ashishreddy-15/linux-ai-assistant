#!/usr/bin/env bash
# Install Ollama for the current user only (no sudo).
#
#   ~/.local/opt/ollama   the program
#   ~/.local/bin/ollama   a link so it is on PATH
#
# Downloads the official release from GitHub, verifies its SHA-256 checksum,
# and skips the NVIDIA (CUDA) libraries unless an NVIDIA GPU is present.
#
# Overrides (mainly for tests):
#   OLLAMA_VERSION        release tag, e.g. v0.40.15 (default: latest)
#   OLLAMA_DOWNLOAD_BASE  base URL holding the release assets
#   INSTALL_PREFIX        default: $HOME/.local
#   KEEP_CUDA=1           keep the CUDA libraries even without an NVIDIA GPU
set -euo pipefail

PREFIX="${INSTALL_PREFIX:-$HOME/.local}"
ASSET="ollama-linux-amd64.tar.zst"
ARCH="$(uname -m)"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$ARCH" = "x86_64" ] || die "this installer supports x86_64 only (found $ARCH)"
for tool in curl tar sha256sum zstd; do
    command -v "$tool" >/dev/null || die "'$tool' is missing; install it with your package manager first"
done

if [ -z "${OLLAMA_DOWNLOAD_BASE:-}" ]; then
    VERSION="${OLLAMA_VERSION:-}"
    if [ -z "$VERSION" ]; then
        say "Looking up the latest Ollama release"
        VERSION="$(curl -fsSL https://api.github.com/repos/ollama/ollama/releases/latest \
            | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n1)"
        [ -n "$VERSION" ] || die "could not find the latest release on GitHub"
    fi
    BASE="https://github.com/ollama/ollama/releases/download/$VERSION"
else
    BASE="$OLLAMA_DOWNLOAD_BASE"
    VERSION="${OLLAMA_VERSION:-local}"
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

say "Downloading Ollama $VERSION (about 1.4 GB; resumes if interrupted)"
curl -fL --retry 8 --retry-all-errors --speed-limit 20000 --speed-time 30 \
    --progress-bar -o "$WORK/sha256sum.txt" "$BASE/sha256sum.txt"
curl -fL --retry 8 --retry-all-errors --speed-limit 20000 --speed-time 30 -C - \
    --progress-bar -o "$WORK/$ASSET" "$BASE/$ASSET"

say "Checking the download"
( cd "$WORK" && grep -E "[ *./]$ASSET\$" sha256sum.txt | sed 's#\./##' | sha256sum -c - ) \
    || die "checksum mismatch: the download is damaged or was tampered with"

EXCLUDES=()
if [ "${KEEP_CUDA:-0}" != "1" ] && ! { [ -e /proc/driver/nvidia/version ] || lspci 2>/dev/null | grep -qi nvidia; }; then
    say "No NVIDIA GPU found: skipping the CUDA libraries (saves about 1.2 GB)"
    EXCLUDES=(--exclude='lib/ollama/cuda_v12*' --exclude='lib/ollama/cuda_v13*')
fi

say "Installing into $PREFIX/opt/ollama"
rm -rf "$PREFIX/opt/ollama.new"
mkdir -p "$PREFIX/opt/ollama.new" "$PREFIX/bin"
tar --zstd -xf "$WORK/$ASSET" -C "$PREFIX/opt/ollama.new" "${EXCLUDES[@]}"
rm -rf "$PREFIX/opt/ollama.old"
[ -d "$PREFIX/opt/ollama" ] && mv "$PREFIX/opt/ollama" "$PREFIX/opt/ollama.old"
mv "$PREFIX/opt/ollama.new" "$PREFIX/opt/ollama"
rm -rf "$PREFIX/opt/ollama.old"
ln -sfn "$PREFIX/opt/ollama/bin/ollama" "$PREFIX/bin/ollama"

say "Done: $("$PREFIX/bin/ollama" --version 2>/dev/null | tail -n1 || echo installed)"
