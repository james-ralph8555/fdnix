# fdnix Nixpkgs Indexer

This container indexes nixpkgs by combining metadata extraction and embedding generation into a single ECS task that can run both phases sequentially or individually.

## Features

- Metadata Generation: Extracts nixpkgs package metadata using nix-env and stores in DuckDB
- Embedding Generation: Creates semantic embeddings using AWS Bedrock and stores in DuckDB with VSS index
- Unified Execution: Can run both phases in sequence or individually based on configuration
- S3 Integration: Automatically uploads final DuckDB artifact to S3
- Security: Runs as a non-root user

## Environment Variables

### Required
- `AWS_REGION`: AWS region for services
- `BEDROCK_MODEL_ID`: Bedrock model ID for embeddings (only needed for embedding mode)

### Optional
- `PROCESSING_MODE`: Mode to run - "metadata", "embedding", or "both" (default: "both")
- `OUTPUT_PATH`: Local path for DuckDB file (default: "/out/fdnix.duckdb")
- `ARTIFACTS_BUCKET`: S3 bucket for artifact upload
- `DUCKDB_KEY`: S3 key for DuckDB artifact
- `DUCKDB_PATH`: Path to DuckDB file for embedding phase (defaults to OUTPUT_PATH)
  
Layer publish (optional):
- `PUBLISH_LAYER`: When truthy (`true`/`1`/`yes`), publish the DuckDB to a Lambda Layer after processing
- `LAYER_ARN`: Unversioned Lambda Layer ARN or name (e.g., `arn:aws:lambda:us-east-1:123456789012:layer:fdnix-database-layer`)

FTS tuning (metadata phase):
- `FTS_STOPWORDS`: Stopwords language (default: `english`)
- `FTS_STEMMER`: Stemmer language (default: `english`, set empty to disable)
- `FTS_INDEX_NAME`: Index name (default: `packages_fts_idx`)

VSS tuning (embedding phase):
- `VSS_HNSW_M`: HNSW M (default: `16`)
- `VSS_EF_CONSTRUCTION`: HNSW ef_construction (default: `200`)
- `VSS_EF_SEARCH`: ef_search session setting (default: `40`)
 - Persistence: on-disk HNSW index persistence is enabled by executing `SET hnsw_enable_experimental_persistence = true` inside DuckDB.

## Processing Modes

### "metadata" mode
1. Extracts package metadata from the nixpkgs channel using `nix-env -qaP --json`, then cleans and normalizes fields (no git clone).
2. Creates DuckDB with `packages` and `packages_fts_source` tables, and builds an FTS index (DuckDB `fts` extension).
3. Optionally uploads the DuckDB artifact to S3 when `ARTIFACTS_BUCKET` and `DUCKDB_KEY` are set.

### "embedding" mode
1. Reads existing DuckDB file (downloads from S3 when `ARTIFACTS_BUCKET`/`DUCKDB_KEY` are provided and the local file is missing).
2. Generates embeddings via AWS Bedrock (e.g., `cohere.embed-english-v3`) for packages without embeddings; resumes idempotently.
3. Writes vectors to the `embeddings` table and creates/refreshes a VSS index for vector search (DuckDB `vss` extension).
4. Optionally uploads the updated DuckDB to S3.

### "both" mode (default)
1. Runs metadata phase first
2. Then runs embedding phase on the generated DuckDB
3. Results in complete DuckDB with both metadata and embeddings

## Usage

The container automatically determines what to run based on the `PROCESSING_MODE` environment variable. For unified processing that handles both metadata extraction and embedding generation:

```bash
docker run -e AWS_REGION=us-east-1 \
           -e BEDROCK_MODEL_ID=cohere.embed-english-v3 \
           -e PROCESSING_MODE=both \
           -e ARTIFACTS_BUCKET=my-bucket \
           -e DUCKDB_KEY=snapshots/fdnix.duckdb \
           fdnix/nixpkgs-indexer
```

## Database Schema

The indexer produces the following schema:

- `packages` table: Complete nixpkgs metadata
- `packages_fts_source` table: Full-text search content
- `embeddings` table: Vector embeddings with VSS index

## Build & Run

- Build (from repo root):
  - `docker build -t fdnix/nixpkgs-indexer packages/containers/nixpkgs-indexer`
- Run (local output to current dir):
  - Metadata only: `docker run --rm -v "$PWD":/out -e AWS_REGION=us-east-1 -e PROCESSING_MODE=metadata fdnix/nixpkgs-indexer`
  - Embeddings only: `docker run --rm -v "$PWD":/out -e AWS_REGION=us-east-1 -e BEDROCK_MODEL_ID=cohere.embed-english-v3 -e PROCESSING_MODE=embedding fdnix/nixpkgs-indexer`
  - Both + upload to S3: `docker run --rm -v "$PWD":/out -e AWS_REGION=us-east-1 -e BEDROCK_MODEL_ID=cohere.embed-english-v3 -e PROCESSING_MODE=both -e ARTIFACTS_BUCKET=fdnix-artifacts -e DUCKDB_KEY=snapshots/fdnix.duckdb fdnix/nixpkgs-indexer`

Notes:
- Embedding mode requires `AWS_REGION`; `BEDROCK_MODEL_ID` defaults to `cohere.embed-english-v3` if not set.
- S3 upload requires all of `ARTIFACTS_BUCKET`, `DUCKDB_KEY`, and `AWS_REGION`.
- Default local artifact path is `/out/fdnix.duckdb`.

## Image & Dependencies

- Base: `nixos/nix:2.31.0`; dependencies installed via Nix (`nix-env`)
- Installed deps: `python313`, `duckdb`, `boto3`, `numpy`, `requests`
- Entry: `python src/index.py`; runs as non-root user
