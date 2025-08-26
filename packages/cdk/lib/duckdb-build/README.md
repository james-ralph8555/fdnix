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

## Build Optimizations (TODO)

- LTO: Enable Link Time Optimization to reduce binary size and improve performance.
- PGO: Evaluate Profile-Guided Optimization using representative query workloads.
- Strip symbols: Ensure `libduckdb.so` is stripped in the final layer stage.
- Prune extensions: Exclude unused extensions (e.g., `parquet`, possibly `httpfs`) for the Lambda runtime; keep `fts`, `vss`, and `json`.
- Compiler flags: Revisit `-O` level (`-O3` vs `-Os`) and link flags for smaller, faster cold starts without sacrificing query latency.
- CI/CD: Add a build matrix or target that produces both a "debug" and an "optimized" layer artifact for comparison.
