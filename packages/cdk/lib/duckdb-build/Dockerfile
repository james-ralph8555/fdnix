# Multi-stage build for DuckDB with FTS and VSS extensions for ARM64 Lambda
# Target: AWS Lambda provided.al2023 runtime on ARM64/Graviton

# Stage 1: Build environment setup using Ubuntu for better toolchain support
FROM ubuntu:22.04 AS builder

# Install build dependencies
RUN apt-get update && \
    apt-get install -y \
        git \
        build-essential \
        cmake \
        ninja-build \
        libssl-dev \
        gcc-aarch64-linux-gnu \
        g++-aarch64-linux-gnu \
        wget \
        curl \
        ca-certificates \
        unzip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set up cross-compilation environment
ENV CC=aarch64-linux-gnu-gcc
ENV CXX=aarch64-linux-gnu-g++
ENV AR=aarch64-linux-gnu-ar
ENV STRIP=aarch64-linux-gnu-strip
ENV CMAKE_SYSTEM_NAME=Linux
ENV CMAKE_SYSTEM_PROCESSOR=aarch64
ENV CMAKE_C_COMPILER=aarch64-linux-gnu-gcc
ENV CMAKE_CXX_COMPILER=aarch64-linux-gnu-g++

WORKDIR /build

# Clone DuckDB repository
RUN git clone --depth 1 https://github.com/duckdb/duckdb.git

WORKDIR /build/duckdb

# Copy build configuration
COPY build-config.cmake .

# Stage 2: Build DuckDB with extensions
FROM builder AS duckdb-build

# Replace source compilation with prebuilt ARM64 DuckDB binary
ENV DUCKDB_VERSION=v1.3.2
ENV DUCKDB_ZIP_URL=https://github.com/duckdb/duckdb/releases/download/${DUCKDB_VERSION}/libduckdb-linux-arm64.zip

WORKDIR /build

# Download and extract precompiled DuckDB library and headers
RUN mkdir -p /build/duckdb-prebuilt /build/out/lib /build/out/include && \
    curl -L "$DUCKDB_ZIP_URL" -o /build/duckdb-prebuilt/libduckdb.zip && \
    unzip -q /build/duckdb-prebuilt/libduckdb.zip -d /build/duckdb-prebuilt && \
    # Copy shared library
    find /build/duckdb-prebuilt -maxdepth 2 -type f -name 'libduckdb.so*' -exec cp {} /build/out/lib/libduckdb.so \; && \
    # Copy headers (duckdb.h / duckdb.hpp) if present
    if ls /build/duckdb-prebuilt/*duckdb*.h* >/dev/null 2>&1; then cp /build/duckdb-prebuilt/*duckdb*.h* /build/out/include/; fi && \
    # Fallback if headers are in an include/ directory in the archive
    if [ -d /build/duckdb-prebuilt/include ]; then cp -r /build/duckdb-prebuilt/include/. /build/out/include/; fi

# Stage 3: Package for Lambda Layer
FROM scratch AS layer

# Copy the built shared library
## TODO(optimizations): Ensure final binary is stripped to minimize layer size.
COPY --from=duckdb-build /build/out/lib/libduckdb.so /opt/lib/libduckdb.so

# Include headers in the Lambda layer for potential native builds
COPY --from=duckdb-build /build/out/include/ /opt/include/

# Copy any additional required libraries (if needed)
# The ARM64 shared library will be used by the C++ Lambda function
