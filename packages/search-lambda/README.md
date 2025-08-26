# fdnix-search-api (C++)

Planned C++ implementation of the fdnix hybrid search Lambda.

- Runtime: AWS Lambda custom runtime (`provided.al2023`).
- Packaging: Compile to a binary named `bootstrap` and zip for upload.
- Query Engine: DuckDB opened read-only from a Lambda Layer (see below).
- Embeddings: Google Gemini Embeddings API (HTTP) with API key auth.
- Libraries: DuckDB (C/C++ API), minimal HTTP client for outbound requests.

Status: C++ implementation in progress. The repo provides a Dockerfile and build script to produce the `bootstrap` binary expected by the CDK.

## Runtime Data Model

- Lambda Layer: `fdnix-db-layer` provides `/opt/fdnix/fdnix.duckdb` (minified database).
- Minified database contents (essential for search):
  - `packages(...)` with essential columns only (name, version, attr path, description, homepage, simplified license/maintainers, flags)
  - `packages_fts_source(...)` and FTS index (BM25)
  - `embeddings(package_id, vector)` with VSS index (e.g., HNSW/IVF)
- Full database (`fdnix-data.duckdb`) is produced by the pipeline and stored in S3 for analytics/debugging; it is not deployed to Lambda.

## Request Handling

- `GET /v1/search?q=<query>`
  - Embed `q` using Google Gemini Embeddings API (e.g., `gemini-embedding-001`) with 256 dimensions.
  - Run two queries against `/opt/fdnix/fdnix.duckdb`:
    - VSS: nearest neighbors over `embeddings.vector` by the query embedding
    - FTS: BM25 over the FTS index from `packages_fts_source`
  - Fuse results (e.g., RRF or normalized weighted sum), then join to `packages` for metadata.
  - Return JSON array of results with scores.

## Configuration

Environment variables used for embeddings:

- `GEMINI_API_KEY`: API key for Gemini requests (required).
- `GEMINI_MODEL_ID`: Embedding model id (default `gemini-embedding-001`).
- `GEMINI_OUTPUT_DIMENSIONS`: Embedding dimensions (default `256`).
- `GEMINI_TASK_TYPE`: Embedding task type (default `SEMANTIC_SIMILARITY`).

Rate limits (matched to the data pipeline defaults):

- `GEMINI_MAX_CONCURRENT_REQUESTS` (default `10`)
- `GEMINI_REQUESTS_PER_MINUTE` (default `3000`)
- `GEMINI_TOKENS_PER_MINUTE` (default `1000000`)

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
# Build a multi-stage image that compiles the Lambda binary
docker build -t fdnix-search-lambda packages/search-lambda

# Extract the bootstrap from the final image into dist/
cid=$(docker create fdnix-search-lambda) && \
  mkdir -p packages/search-lambda/dist && \
  docker cp "$cid":/bootstrap packages/search-lambda/dist/bootstrap && \
  docker rm "$cid"

# Verify binary exists
ls -l packages/search-lambda/dist/bootstrap
```

## Build Tips

- Match Lambda environment: Amazon Linux 2023 is used in the Dockerfile to match `provided.al2023`.
- Optimize size and cold start: Release builds with LTO and stripped symbols are configured in CMake.
- Verify output: Ensure `dist/bootstrap` exists and is executable before running CDK deploy.

## DuckDB Extensions in Lambda

- The pipeline prebuilds FTS/VSS indexes in the DuckDB file.
- If runtime queries require loading extensions, bundle them in the layer and `LOAD` at startup (or statically compile them into the DuckDB library used by the function).

## Deploy Flow

- Build `bootstrap` (one of the methods above)
- Deploy (from CDK folder): `(cd packages/cdk && npm run deploy)`
