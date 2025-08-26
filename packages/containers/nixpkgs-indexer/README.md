# fdnix Nixpkgs Indexer

This container indexes nixpkgs by combining metadata extraction and embedding generation into a single ECS task that can run both phases sequentially or individually. It now also creates a minified DuckDB optimized for deployment in a Lambda layer.

## Features

- Metadata Generation: Extracts nixpkgs package metadata using nix-env and writes a full DuckDB database
- Minified Database: Derives a minified DuckDB with only essential columns, simplified license/maintainer strings, and builds an FTS index
- Embedding Generation: Creates semantic embeddings using Google Gemini API and stores in DuckDB with VSS index
- Unified Execution: Can run both phases in sequence or individually based on configuration
- S3 Integration: Uploads both full and minified artifacts to distinct keys
- Security: Runs as a non-root user

## Environment Variables

### Required
- `AWS_REGION`: AWS region for services
- `GEMINI_API_KEY`: Google Gemini API key for embeddings (only needed for embedding mode)

### Optional
- `PROCESSING_MODE`: Mode to run - "metadata", "embedding", or "both" (default: "both")
- `OUTPUT_PATH`: Local path for the full database (default: "/out/fdnix-data.duckdb")
- `OUTPUT_MINIFIED_PATH`: Local path for the minified database (default: "/out/fdnix.duckdb")
- `ARTIFACTS_BUCKET`: S3 bucket for artifact upload
- `DUCKDB_DATA_KEY`: S3 key for the full database (e.g., `snapshots/fdnix-data.duckdb`)
- `DUCKDB_MINIFIED_KEY`: S3 key for the minified database (e.g., `snapshots/fdnix.duckdb`)
- `DUCKDB_PATH`: Path to DuckDB file for embedding phase (defaults to minified when running both)
  
Embedding concurrency and limits:
- `GEMINI_MAX_CONCURRENT_REQUESTS`: Max in-flight Gemini requests (default: `10`)
- `GEMINI_REQUESTS_PER_MINUTE`: Request budget per minute (default: `3000`)
- `GEMINI_TOKENS_PER_MINUTE`: Approximate token budget/min (default: `1000000`)
- `GEMINI_INTER_BATCH_DELAY`: Delay (seconds) between internal batches (default: `0.02`)
- `GEMINI_MODEL_ID`: Gemini model ID (default: `gemini-embedding-001`)
- `GEMINI_OUTPUT_DIMENSIONS`: Embedding dimensions (default: `256`)
- `GEMINI_TASK_TYPE`: Task type optimization (default: `SEMANTIC_SIMILARITY`)
  
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
2. Creates the full DuckDB (`fdnix-data.duckdb`) with complete metadata.
3. Derives the minified DuckDB (`fdnix.duckdb`) with only essential columns; simplifies license/maintainers to strings; builds FTS (DuckDB `fts` extension).
4. Optionally uploads both artifacts to S3 when `ARTIFACTS_BUCKET` and keys are provided.

### "embedding" mode
1. Reads existing DuckDB file (defaults to minified DB when both were produced; downloads from S3 when keys are provided and the local file is missing).
2. Generates embeddings via Google Gemini API (e.g., `gemini-embedding-001`) for packages without embeddings; resumes idempotently.
3. Writes vectors to the `embeddings` table and creates/refreshes a VSS index for vector search (DuckDB `vss` extension). Embedding calls use asyncio with a semaphore, a sliding-window rate limiter (RPM/TPM), and exponential backoff with jitter for throttling.
4. Optionally uploads the updated DuckDB to S3.

### "both" mode (default)
1. Runs metadata phase (produces full + minified DBs)
2. Runs embedding phase on the minified DB
3. Uploads minified DB for use in the Lambda layer; full DB remains available in S3 for analytics/debugging

## Usage

The container automatically determines what to run based on the `PROCESSING_MODE` environment variable. For unified processing that handles both metadata extraction and embedding generation:

```bash
docker run --env-file .env -e AWS_REGION=us-east-1 \
           -e GEMINI_API_KEY=your-api-key \
           -e GEMINI_MODEL_ID=gemini-embedding-001 \
           -e GEMINI_OUTPUT_DIMENSIONS=256 \
           -e PROCESSING_MODE=both \
           -e ARTIFACTS_BUCKET=my-bucket \
           -e DUCKDB_DATA_KEY=snapshots/fdnix-data.duckdb \
           -e DUCKDB_MINIFIED_KEY=snapshots/fdnix.duckdb \
           fdnix/nixpkgs-indexer
```

## Database Schema

The indexer produces the following schema:

- Full database (`fdnix-data.duckdb`): Complete nixpkgs metadata and embeddings (when generated)
- Minified database (`fdnix.duckdb`): Essential columns only; simplified license/maintainers; FTS built on minified text
- Core tables in both:
  - `packages` table: Metadata (minified drops non-essential columns)
  - `packages_fts_source` table: Full-text search content with FTS index
  - `embeddings` table: Vector embeddings with VSS index

## Build & Run

- Build (from repo root):
  - `docker build -t fdnix/nixpkgs-indexer packages/containers/nixpkgs-indexer`
- Run (local output to current dir):
  - Metadata only: `docker run --rm --env-file .env -v "$PWD":/out -e AWS_REGION=us-east-1 -e PROCESSING_MODE=metadata fdnix/nixpkgs-indexer`
  - Embeddings only: `docker run --rm --env-file .env -v "$PWD":/out -e GEMINI_API_KEY=your-api-key -e GEMINI_MODEL_ID=gemini-embedding-001 -e GEMINI_OUTPUT_DIMENSIONS=256 -e PROCESSING_MODE=embedding fdnix/nixpkgs-indexer`
  - Both + upload to S3: `docker run --rm --env-file .env -v "$PWD":/out -e AWS_REGION=us-east-1 -e GEMINI_API_KEY=your-api-key -e GEMINI_MODEL_ID=gemini-embedding-001 -e GEMINI_OUTPUT_DIMENSIONS=256 -e PROCESSING_MODE=both -e ARTIFACTS_BUCKET=fdnix-artifacts -e DUCKDB_DATA_KEY=snapshots/fdnix-data.duckdb -e DUCKDB_MINIFIED_KEY=snapshots/fdnix.duckdb fdnix/nixpkgs-indexer`

Notes:
- Embedding mode requires `GEMINI_API_KEY`; `GEMINI_MODEL_ID` defaults to `gemini-embedding-001` if not set. Dimensions default to 256.
- S3 upload requires `ARTIFACTS_BUCKET`, `AWS_REGION`, and at least one of `DUCKDB_DATA_KEY` or `DUCKDB_MINIFIED_KEY`.
- Default local artifact paths: `/out/fdnix-data.duckdb` (full) and `/out/fdnix.duckdb` (minified).

## Image & Dependencies

- Base: `nixos/nix:2.31.0`; dependencies installed via Nix (`nix-env`)
- Installed deps: `python313`, `duckdb`, `boto3`, `numpy`, `requests`, `httpx`
- Entry: `python src/index.py`; runs as non-root user

## Notes on Layer Publishing

- The layer publisher uses the minified database. Set `PUBLISH_LAYER=true` and provide `LAYER_ARN`, `ARTIFACTS_BUCKET`, `AWS_REGION`, and `DUCKDB_MINIFIED_KEY` (required).

## Minified Database Rationale

- Smaller Lambda layer and faster cold starts
- Optimized query performance over a focused schema
- Cost savings on storage and transfer
- Full database preserved in S3 for debugging/analytics

## Legacy Support Removal

- Backward compatibility for `DUCKDB_KEY` has been removed. Use `DUCKDB_DATA_KEY` for the full database and `DUCKDB_MINIFIED_KEY` for the minified database. `DUCKDB_MINIFIED_KEY` is required for layer publishing.
