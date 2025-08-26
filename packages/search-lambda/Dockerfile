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
        python3-devel && \
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
    ninja -C build -j$(nproc) && \
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
    ninja -C build -j$(nproc) && \
    ninja -C build install && \
    rm -rf /build/aws-lambda-cpp

# Build DuckDB from source for ARM64
RUN git clone https://github.com/duckdb/duckdb.git && \
    cd duckdb && \
    CORE_EXTENSIONS='autocomplete;httpfs;icu;json;tpch' GEN=ninja make && \
    cp build/release/src/libduckdb.so /usr/local/lib/ && \
    cp src/include/duckdb.hpp /usr/local/include/ && \
    cp src/include/duckdb.h /usr/local/include/ && \
    cp -r src/include/duckdb /usr/local/include/ && \
    ldconfig && \
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
    ninja -C build -j$(nproc)

# Runtime stage
FROM amazonlinux:2023

# Copy the built Lambda function
COPY --from=builder /build/lambda/dist/bootstrap /bootstrap

# Make sure bootstrap is executable
RUN chmod +x /bootstrap

# Set the Lambda runtime handler (not used with custom runtime but good practice)
CMD ["bootstrap"]
