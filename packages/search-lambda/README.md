# fdnix-search-api (C++)

Planned C++ implementation of the fdnix hybrid search Lambda.

- Runtime: AWS Lambda custom runtime (`provided.al2023`).
- Packaging: Compile to a binary named `bootstrap` and zip for upload.
- Query Engine: DuckDB statically compiled into the function binary.
- Embeddings: AWS Bedrock Runtime (Amazon Titan Embeddings) for real-time query embeddings.
- Libraries: DuckDB (statically linked), AWS SDK for C++ (core, bedrock-runtime), AWS Lambda C++ runtime.

Status: C++ implementation in progress. The repo provides a Dockerfile and build script to produce the `bootstrap` binary expected by the CDK.

## Runtime Data Model

- Lambda Layer: `fdnix-db-layer` provides `/opt/fdnix/fdnix.duckdb` (minified database).
- DuckDB Dependencies: All DuckDB libraries are statically compiled into the function binary (no separate library layer needed).
- Minified database contents (essential for search):
  - `packages(...)` with essential columns only (name, version, attr path, description, homepage, simplified license/maintainers, flags)
  - `packages_fts_source(...)` and FTS index (BM25)
  - `embeddings(package_id, vector)` with VSS index (e.g., HNSW/IVF)
- Full database (`fdnix-data.duckdb`) is produced by the pipeline and stored in S3 for analytics/debugging; it is not deployed to Lambda.

## Request Handling

- `GET /v1/search?q=<query>`
  - Embed `q` using AWS Bedrock Runtime (e.g., `amazon.titan-embed-text-v2:0`) with 256 dimensions.
  - Run two queries against `/opt/fdnix/fdnix.duckdb`:
    - VSS: nearest neighbors over `embeddings.vector` by the query embedding
    - FTS: BM25 over the FTS index from `packages_fts_source`
  - Fuse results (e.g., RRF or normalized weighted sum), then join to `packages` for metadata.
  - Return JSON array of results with scores.

## Configuration

Environment variables used for embeddings (real-time via Bedrock):

- `AWS_REGION`: AWS region for Bedrock Runtime (defaults to Lambda region).
- `BEDROCK_MODEL_ID`: Embedding model id (default `amazon.titan-embed-text-v2:0`).
- `BEDROCK_OUTPUT_DIMENSIONS`: Embedding dimensions (default `256`).

## Build

Two options are provided.

1) Local build (requires toolchain and dependencies):

```bash
# Installs not managed here; you need CMake, Ninja, AWS SDK for C++, and aws-lambda-runtime installed locally
(cd packages/search-lambda && npm run build)
# Output: packages/search-lambda/dist/bootstrap
```

2) Docker build (recommended for reproducible output):

```bash
# Use the provided build script (recommended)
cd packages/search-lambda
./build.sh

# Or build manually with Docker
docker build -t fdnix-search-lambda .

# Extract the bootstrap from the final image into dist/
cid=$(docker create fdnix-search-lambda) && \
  mkdir -p dist && \
  docker cp "$cid":/var/task/bootstrap dist/bootstrap && \
  docker rm "$cid"

# Verify binary exists and is self-contained
ls -l dist/bootstrap
ldd dist/bootstrap || echo "Static binary - no dynamic dependencies"
```

## Build Tips

- Match Lambda environment: Amazon Linux 2023 is used in the Dockerfile to match `provided.al2023`.
- Optimize size and cold start: Release builds with LTO and stripped symbols are configured in CMake.
- Verify output: Ensure `dist/bootstrap` exists and is executable before running CDK deploy.

## DuckDB Extensions in Lambda

- The pipeline prebuilds FTS/VSS indexes in the DuckDB file.
- All DuckDB extensions and dependencies are statically compiled into the function binary for optimal performance and cold start times.

## Deploy Flow

- Build `bootstrap` (one of the methods above)
- Deploy (from CDK folder): `(cd packages/cdk && npm run deploy)`
