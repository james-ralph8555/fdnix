# Multi-stage build for C++ Lambda with AWS SDK and DuckDB support
FROM ubuntu:22.04 AS base

# Prevent interactive package configuration
ENV DEBIAN_FRONTEND=noninteractive

# Install build dependencies
RUN apt-get update && \
    apt-get install -y \
        build-essential \
        cmake \
        ninja-build \
        pkg-config \
        libcurl4-openssl-dev \
        libssl-dev \
        zlib1g-dev \
        uuid-dev \
        libnghttp2-dev \
        libidn2-dev \
        libpsl-dev \
        libgssapi-krb5-2 \
        libkrb5-dev \
        libldap2-dev \
        libssh-dev \
        libbrotli-dev \
        libzstd-dev \
        librtmp-dev \
        git \
        wget \
        tar \
        gzip \
        python3 \
        python3-pip \
        python3-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Build stage
FROM base AS builder

WORKDIR /build

# Install AWS SDK for C++
RUN git clone --recurse-submodules --depth 1 https://github.com/aws/aws-sdk-cpp.git && \
    cd aws-sdk-cpp && \
    cmake -Bbuild \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_PREFIX_PATH=/usr/local \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DBUILD_ONLY="core;bedrock-runtime" \
        -DENABLE_TESTING=OFF \
        -DAUTORUN_UNIT_TESTS=OFF \
        -DBUILD_SHARED_LIBS=OFF \
        -DCMAKE_FIND_LIBRARY_SUFFIXES=.a \
        -GNinja && \
    ninja -C build -j4 && \
    ninja -C build install && \
    rm -rf /build/aws-sdk-cpp

# Install AWS Lambda Runtime for C++
RUN git clone --depth 1 https://github.com/awslabs/aws-lambda-cpp.git && \
    cd aws-lambda-cpp && \
    cmake -Bbuild \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_PREFIX_PATH=/usr/local \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DBUILD_SHARED_LIBS=OFF \
        -GNinja && \
    ninja -C build -j4 && \
    ninja -C build install && \
    rm -rf /build/aws-lambda-cpp

# No additional dependencies needed for FTS, VSS, JSON extensions
# (git, ninja-build, openssl-devel already installed in base)

# Build DuckDB v1.3.2 from source with bundled static library and built-in extensions
RUN git clone --branch v1.3.2 --depth 1 https://github.com/duckdb/duckdb.git && \
    cd duckdb && \
    BUILD_EXTENSIONS="json;core_functions;jemalloc" \
    DISABLE_BUILTIN_EXTENSIONS=0 \
    STATIC_LIBCPP=1 \
    ENABLE_SANITIZER=0 \
    ENABLE_UBSAN=0 \
    make bundle-library && \
    mkdir -p /usr/local/lib && \
    cp build/release/libduckdb_bundle.a /usr/local/lib/libduckdb_bundle.a && \
    cp -r src/include/* /usr/local/include/ && \
    rm -rf /build/duckdb

# Copy source code
COPY . /build/lambda

WORKDIR /build/lambda

# Build the Lambda function
RUN cmake -Bbuild \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_PREFIX_PATH=/usr/local \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -GNinja && \
    ninja -C build -j4

# Minimal runtime stage
FROM ubuntu:22.04

# Create necessary directories
RUN mkdir -p /var/task

# Copy the statically linked Lambda function
COPY --from=builder /build/lambda/build/bootstrap /var/task/bootstrap

# Make bootstrap executable and verify static linking
RUN chmod +x /var/task/bootstrap && \
    ldd /var/task/bootstrap || echo "Static binary - no dynamic dependencies"
