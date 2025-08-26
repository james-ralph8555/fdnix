#!/bin/bash
set -euo pipefail

# Build script for fdnix C++ Lambda function

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${PROJECT_DIR}/build"
DIST_DIR="${PROJECT_DIR}/dist"

echo "Building fdnix C++ Lambda function..."

# Create build directory
mkdir -p "${BUILD_DIR}"
mkdir -p "${DIST_DIR}"

# Configure CMake
echo "Configuring CMake..."
cmake -B"${BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH=/usr/local \
    -GNinja \
    "${PROJECT_DIR}"

# Build the project
echo "Building with Ninja..."
ninja -C "${BUILD_DIR}" -j$(nproc)

# Copy bootstrap to dist directory
echo "Copying bootstrap to dist directory..."
cp "${BUILD_DIR}/bootstrap" "${DIST_DIR}/bootstrap"

# Ensure bootstrap is executable
chmod +x "${DIST_DIR}/bootstrap"

echo "Build complete! Bootstrap binary available at: ${DIST_DIR}/bootstrap"
echo "Ready for Lambda deployment."

# Optional: Show binary info
if command -v file >/dev/null 2>&1; then
    echo "Binary info:"
    file "${DIST_DIR}/bootstrap"
fi

if command -v ldd >/dev/null 2>&1; then
    echo "Library dependencies:"
    ldd "${DIST_DIR}/bootstrap" || echo "Static binary or dependencies not found"
fi