# Multi-stage build for Rust Lambda with AWS SDK and DuckDB support
FROM rust:1.86-slim AS builder

# Install system dependencies for building
RUN apt-get update && \
    apt-get install -y \
        build-essential \
        cmake \
        ninja-build \
        pkg-config \
        ca-certificates \
        git \
        musl-tools \
        musl-dev \
        clang \
        libclang-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Add musl target for static linking
RUN rustup target add x86_64-unknown-linux-musl

# Set working directory
WORKDIR /build

# Copy Rust project files
COPY Cargo.toml ./
COPY build.rs ./
COPY src/ ./src/

# Set environment variables for static linking and cross-compilation
ENV CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER=musl-gcc
ENV CC_x86_64_unknown_linux_musl=musl-gcc
ENV CXX_x86_64_unknown_linux_musl=musl-g++

# Build the Lambda function for musl target (static linking)
RUN cargo build --release --target x86_64-unknown-linux-musl

# Verify the binary is statically linked
RUN file /build/target/x86_64-unknown-linux-musl/release/bootstrap && \
    ldd /build/target/x86_64-unknown-linux-musl/release/bootstrap || echo "Static binary - no dynamic dependencies"

# Minimal runtime stage using scratch (for ultimate portability)
FROM scratch

# Copy the statically linked Lambda function
COPY --from=builder /build/target/x86_64-unknown-linux-musl/release/bootstrap /var/task/bootstrap

# Copy SSL certificates for HTTPS requests
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/

# Set the CMD to your handler (could also be done as a parameter override outside of the Dockerfile)
CMD ["/var/task/bootstrap"]