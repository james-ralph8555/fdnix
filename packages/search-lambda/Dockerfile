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

# Build DuckDB from source as static library with all dependencies
RUN git clone --depth 1 --branch v1.3.2 https://github.com/duckdb/duckdb.git && \
    cd duckdb && \
    cmake -Bbuild \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_PREFIX_PATH=/usr/local \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DBUILD_SHARED_LIBS=OFF \
        -DBUILD_UNITTESTS=OFF \
        -DBUILD_SHELL=OFF \
        -DENABLE_SANITIZER=OFF \
        -DENABLE_UBSAN=OFF \
        -DFORCE_COLORED_OUTPUT=OFF \
        -DSTATIC_LIBCPP=ON \
        -GNinja && \
    ninja -C build -j4 && \
    ninja -C build install && \
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

# Minimal runtime stage for self-contained binary
FROM amazonlinux:2023

# Install runtime dependencies including elfutils for libdw
RUN dnf update -y && \
    dnf install -y \
        glibc \
        libstdc++ \
        elfutils-libs \
        libcurl \
        openssl-libs \
        zlib && \
    dnf clean all

# Copy the self-contained Lambda function
COPY --from=builder /build/lambda/build/bootstrap /var/task/bootstrap

# Make bootstrap executable and verify it's self-contained
RUN chmod +x /var/task/bootstrap && \
    ldd /var/task/bootstrap || echo "Static binary - no dynamic dependencies"

# Set working directory and entry point for Lambda
WORKDIR /var/task
CMD ["/var/task/bootstrap"]
