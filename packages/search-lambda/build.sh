#!/bin/bash
set -euo pipefail

# Build script for fdnix C++ Lambda function

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="${PROJECT_DIR}/dist"

echo "Building fdnix C++ Lambda function..."

# Create dist directory
mkdir -p "${DIST_DIR}"

# Build Docker image with builder target
echo "Building Docker image with builder target..."
docker build --target builder -t fdnix-lambda-builder .

# Extract the built bootstrap binary and DuckDB library from the Docker image
echo "Extracting bootstrap binary and DuckDB library from Docker image..."
docker run --rm -v "${DIST_DIR}:/output" fdnix-lambda-builder sh -c "
    cp /build/lambda/build/bootstrap /output/bootstrap && 
    mkdir -p /output/lib && 
    cp /usr/local/lib64/libduckdb.so /output/lib/libduckdb.so
"

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
