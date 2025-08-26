# DuckDB Library Layer Build

This directory contains the build configuration for creating a Lambda layer with DuckDB shared library built from source for ARM64 (Graviton) processors.

## Architecture

- **Target**: AWS Lambda `provided.al2023` runtime on ARM64
- **Extensions**: FTS (Full Text Search) and VSS (Vector Similarity Search) statically compiled
- **Build System**: CMake + Ninja for parallel compilation
- **Cross-compilation**: x86_64 host building for aarch64 target

## Files

- `Dockerfile`: Multi-stage build for cross-compiling DuckDB
- `build-config.cmake`: Extension configuration for static linking
- `layer-structure/lib/`: Target directory for the layer structure

## Build Process

The build is handled automatically by the CDK DockerBuildConstruct, which:

1. Uses the multi-stage Dockerfile to cross-compile for ARM64
2. Statically links FTS and VSS extensions into libduckdb.so
3. Packages the shared library in Lambda layer format at /opt/lib/

## Layer Usage

The resulting layer provides:
- `/opt/lib/libduckdb.so` - Main DuckDB shared library with embedded extensions
- Compatible with `provided.al2023` Lambda runtime
- Ready for linking in C++ Lambda functions

## Integration

This layer works in conjunction with the database file layer:
- Layer 1 (this): DuckDB shared library (`fdnix-duckdb-lib-layer`)
- Layer 2: Database file (`fdnix-db-layer` at `/opt/fdnix/fdnix.duckdb`)

The C++ search Lambda function links against the library from Layer 1 and opens the database from Layer 2.