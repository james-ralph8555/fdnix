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

# Install DuckDB precompiled library for x86_64
RUN curl -L "https://github.com/duckdb/duckdb/releases/download/v1.3.2/libduckdb-linux-amd64.zip" -o /tmp/libduckdb.zip && \
    unzip -q /tmp/libduckdb.zip -d /tmp/duckdb && \
    find /tmp/duckdb -maxdepth 2 -type f -name 'libduckdb.so*' -exec cp {} /usr/local/lib/libduckdb.so \; && \
    if ls /tmp/duckdb/*duckdb*.h* >/dev/null 2>&1; then cp /tmp/duckdb/*duckdb*.h* /usr/local/include/; fi && \
    if [ -d /tmp/duckdb/include ]; then cp -r /tmp/duckdb/include/. /usr/local/include/; fi && \
    ldconfig && \
    rm -rf /tmp/duckdb /tmp/libduckdb.zip

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

# Runtime stage
FROM amazonlinux:2023

# Copy the built Lambda function
COPY --from=builder /build/lambda/dist/bootstrap /bootstrap

# Make sure bootstrap is executable
RUN chmod +x /bootstrap

# Set the Lambda runtime handler (not used with custom runtime but good practice)
CMD ["bootstrap"]
