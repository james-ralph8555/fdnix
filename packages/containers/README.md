# fdnix Data Processing Containers

Status: Unified container architecture (metadata + embeddings in one).

## What’s Included

- Nixpkgs Indexer (`packages/containers/nixpkgs-indexer/`)
  - Combines nixpkgs metadata extraction and embedding generation
  - Three modes: `metadata`, `embedding`, or `both` (default)
  - Outputs a single DuckDB artifact with FTS and VSS indexes
  - S3 integration for artifact upload/download
  - Runs as non-root; dependencies installed via Nix on `nixos/nix:2.18.1` (Python 3.11, DuckDB, boto3, httpx, etc.)

Deprecated: the separate `metadata-generator` and `embedding-generator` containers have been replaced by the unified processor.

## How It Runs in AWS

- Orchestration: Step Functions `fdnix-daily-pipeline` runs nightly (02:00 UTC) via EventBridge.
- Sequence: ECS Fargate runs a single indexer task (`PROCESSING_MODE=both`) → Publish Layer.
- Resources (from CDK):
  - Artifacts bucket: `fdnix-artifacts` (stores `.duckdb`).
  - Lambda Layer: `fdnix-db-layer` (packages `.duckdb` under `/opt/fdnix/fdnix.duckdb`).
  - Logs: single log group for the indexer task (e.g., `/fdnix/nixpkgs-indexer`).
  - ECR: single repository (e.g., `fdnix-nixpkgs-indexer`).

## Container Environment Variables

- Common:
  - `AWS_REGION`: AWS region used by clients.
  - `ARTIFACTS_BUCKET`: S3 bucket for `.duckdb` artifacts (e.g., `fdnix-artifacts`).
  - `DUCKDB_KEY`: S3 key for the artifact (e.g., `snapshots/fdnix.duckdb`).
  - `PROCESSING_MODE`: `metadata` | `embedding` | `both` (default: `both`).
  - FTS (optional tuning): `FTS_STOPWORDS` (default `english`), `FTS_STEMMER` (default `english`), `FTS_INDEX_NAME` (default `packages_fts_idx`).
- Embeddings:
  - `GEMINI_API_KEY`: Google Gemini API key for embeddings.
  - `GEMINI_MODEL_ID`: Gemini model id (e.g., `gemini-embedding-001`). If not set, defaults to `gemini-embedding-001`.
  - `GEMINI_OUTPUT_DIMENSIONS`: Embedding dimensions (default: `256`).
  - `GEMINI_TASK_TYPE`: Task type optimization (default: `SEMANTIC_SIMILARITY`).
  - VSS (optional tuning): `VSS_HNSW_M` (default `16`), `VSS_EF_CONSTRUCTION` (default `200`), `VSS_EF_SEARCH` (default `40`).
  - VSS persistence: Enabled by executing `SET hnsw_enable_experimental_persistence = true` in DuckDB.
  - Rate limiting & concurrency:
    - `GEMINI_MAX_CONCURRENT_REQUESTS` (default: `10`)
    - `GEMINI_REQUESTS_PER_MINUTE` (default: `3000`)
    - `GEMINI_TOKENS_PER_MINUTE` (default: `1000000`)
    - `GEMINI_INTER_BATCH_DELAY` seconds (default: `0.02`)
    - The embedding client uses an asyncio semaphore and a sliding 60s window to respect RPM/TPM, with exponential backoff + jitter on throttling.
- Local paths (optional):
  - `OUTPUT_PATH`: Local DuckDB path (default: `/out/fdnix.duckdb`).
  - `DUCKDB_PATH`: Input DuckDB for embedding mode (defaults to `OUTPUT_PATH`).

## Build (Local)

From repo root:

- Nixpkgs Indexer:
  - `docker build -t fdnix/nixpkgs-indexer packages/containers/nixpkgs-indexer`

## Run (Local)

- Metadata-only (produces local `fdnix.duckdb`):
  - `docker run --rm --env-file .env -v "$PWD":/out -e AWS_REGION=us-east-1 -e PROCESSING_MODE=metadata fdnix/nixpkgs-indexer`
- Embedding-only (consumes and updates `fdnix.duckdb`):
  - `docker run --rm --env-file .env -v "$PWD":/out -e GEMINI_API_KEY=your-api-key -e GEMINI_MODEL_ID=gemini-embedding-001 -e PROCESSING_MODE=embedding fdnix/nixpkgs-indexer`
- Both phases and upload to S3:
  - `docker run --rm --env-file .env -v "$PWD":/out -e AWS_REGION=us-east-1 -e GEMINI_API_KEY=your-api-key -e GEMINI_MODEL_ID=gemini-embedding-001 -e PROCESSING_MODE=both -e ARTIFACTS_BUCKET=fdnix-artifacts -e DUCKDB_KEY=snapshots/fdnix.duckdb fdnix/nixpkgs-indexer`

For AWS runs, provide `ARTIFACTS_BUCKET` and `DUCKDB_KEY` to upload the final artifact. Requires credentials with access to S3 and a Google Gemini API key. Embedding mode requires `GEMINI_API_KEY`.

## Deployment Notes

- CDK defines a single ECR repo and task definition sized for the end-to-end job.
- Push image to ECR (example):
  - `aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com`
  - `docker tag fdnix/nixpkgs-indexer $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/fdnix-nixpkgs-indexer:latest`
  - `docker push $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/fdnix-nixpkgs-indexer:latest`
- Step Functions then publishes/updates the Lambda Layer from the artifact.

## Outputs and Schema

- DuckDB file: `fdnix.duckdb` containing tables:
  - `packages(...)` (normalized metadata)
  - `packages_fts_source(...)` with FTS index
  - `embeddings(package_id, vector)` with VSS index

See `packages/containers/nixpkgs-indexer/README.md` for detailed usage and troubleshooting.

## Phase Details

- Metadata phase:
  - Extracts package metadata from the nixpkgs channel via `nix-env -qaP --json` (no git clone).
  - Cleans and normalizes fields (ids, names, attrs, descriptions, maintainers, etc.).
  - Writes `packages` and `packages_fts_source` tables to DuckDB.
  - Builds full‑text search index using DuckDB `fts` extension.
  - Optionally uploads the DuckDB artifact to S3.
- Embedding phase:
  - Opens existing DuckDB (downloaded from S3 when `ARTIFACTS_BUCKET`/`DUCKDB_KEY` provided if not present locally).
  - Generates text embeddings with Google Gemini API (e.g., `gemini-embedding-001`) with 256 dimensions.
  - Inserts new vectors into `embeddings` and maintains referential integrity with `packages`.
  - Builds/refreshes vector similarity index using DuckDB `vss` extension.
  - Optionally uploads the updated DuckDB to S3.
