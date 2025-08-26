#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"
TARGET_TRIPLE="x86_64-unknown-linux-gnu"

mkdir -p "$DIST_DIR"

if ! command -v cargo >/dev/null 2>&1; then
  echo "[fdnix-search-lambda] cargo not found; skipping Rust Lambda build."
  echo "Install Rust and run: (cd packages/search-lambda && ./build.sh)"
  exit 0
fi

echo "[fdnix-search-lambda] Building release binary for $TARGET_TRIPLE..."
RUSTFLAGS="-C target-feature=+crt-static" \
  cargo build --release --target "$TARGET_TRIPLE"

BIN_PATH="$SCRIPT_DIR/target/$TARGET_TRIPLE/release/fdnix-search-api"
if [ ! -f "$BIN_PATH" ]; then
  echo "Build did not produce binary at $BIN_PATH" >&2
  exit 1
fi

cp "$BIN_PATH" "$DIST_DIR/bootstrap"
chmod +x "$DIST_DIR/bootstrap"
echo "[fdnix-search-lambda] Bootstrap placed at $DIST_DIR/bootstrap"

