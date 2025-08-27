# Multi-stage build for C++ Lambda with AWS SDK and DuckDB support
FROM amazonlinux:2023 AS base

# Install build dependencies
RUN dnf update -y && \
    dnf groupinstall -y "Development Tools" && \
    dnf install -y \
        cmake \
        ninja-build \
        curl-devel \
        openssl-devel \
        zlib-devel \
        libcurl-devel \
        git \
        wget \
        tar \
        gzip \
        python3 \
        python3-pip \
        python3-devel \
        libstdc++-static \
        glibc-static \
        elfutils-devel && \
    dnf clean all

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

# Install DuckDB from precompiled binary release for v1.3.2
RUN curl -L https://github.com/duckdb/duckdb/releases/download/v1.3.2/libduckdb-linux-amd64.zip -o duckdb.zip && \
    unzip duckdb.zip && \
    # Install both static and shared libraries and headers
    cp libduckdb_static.a /usr/local/lib64/ && \
    cp libduckdb.so /usr/local/lib64/ && \
    cp duckdb.hpp /usr/local/include/ && \
    cp duckdb.h /usr/local/include/ && \
    rm -rf duckdb.zip libduckdb_static.a libduckdb.so duckdb.hpp duckdb.h

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

# Minimal runtime stage - use same base to avoid package manager issues
FROM amazonlinux:2023

# Create necessary directories
RUN mkdir -p /usr/local/lib64 /var/task

# Copy the Lambda function and DuckDB library
COPY --from=builder /build/lambda/build/bootstrap /var/task/bootstrap
COPY --from=builder /usr/local/lib64/libduckdb.so /usr/local/lib64/libduckdb.so

# Make bootstrap executable and verify libraries are found
RUN chmod +x /var/task/bootstrap && \
    ldconfig && \
    ldd /var/task/bootstrap
