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
        ca-certificates && \
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

# Configure CMake build with static extensions and cross-compilation
## TODO(optimizations): Consider enabling LTO/PGO and tuning -O flags for size/speed.
## Also consider pruning unused extensions at configure-time to reduce lib size.
RUN cmake -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DEXTENSION_CONFIGS=build-config.cmake \
    -DCMAKE_SYSTEM_NAME=Linux \
    -DCMAKE_SYSTEM_PROCESSOR=aarch64 \
    -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc \
    -DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++ \
    -DCMAKE_SHARED_LINKER_FLAGS="-static-libgcc -static-libstdc++" \
    -DBUILD_SHARED_LIBS=ON \
    -GNinja \
    .

# Build with parallel jobs
## TODO(optimizations): Pass appropriate flags to enable Thin/Full LTO and PGO if adopted.
RUN ninja -C build -j$(nproc)

# Stage 3: Package for Lambda Layer
FROM scratch AS layer

# Copy the built shared library
## TODO(optimizations): Ensure final binary is stripped to minimize layer size.
COPY --from=duckdb-build /build/duckdb/build/src/libduckdb.so /opt/lib/libduckdb.so

# Copy any additional required libraries (if needed)
# The ARM64 shared library will be used by the C++ Lambda function
