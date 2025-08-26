# fdnix Data Processing Containers

Status: Three-phase pipeline with S3 artifacts (metadata → embeddings → minified).

## What’s Included

- Nixpkgs Indexer (`packages/containers/nixpkgs-indexer/`)
  - Phases: Metadata, Embeddings, Minified (run individually or all)
  - Outputs: Full DB `fdnix-data.duckdb` (metadata + embeddings) → Minified DB `fdnix.duckdb` (FTS, essential columns)
  - Modes: `metadata`, `embedding`, `minified`, or `both`/`full` (all phases; default)
  - S3 integration for upload/download per phase for observability
  - Runs as non-root; dependencies installed via Nix on `nixos/nix` (Python, DuckDB, boto3, httpx, etc.)

Deprecated: the separate `metadata-generator` and `embedding-generator` containers have been replaced by the unified processor.

## How It Runs in AWS

- Orchestration: Step Functions `fdnix-daily-pipeline` runs nightly (02:00 UTC) via EventBridge.
- Sequence: ECS Fargate runs a single indexer task (`PROCESSING_MODE=both/full`) which performs metadata → embedding → minified, then (optionally) publishes the minified DB as a Lambda layer.
- Resources (from CDK):
  - Artifacts bucket: `fdnix-artifacts` (stores `.duckdb`).
  - Lambda Layer: `fdnix-db-layer` (packages `.duckdb` under `/opt/fdnix/fdnix.duckdb`).
  - Logs: single log group for the indexer task (e.g., `/fdnix/nixpkgs-indexer`).
  - ECR: single repository (e.g., `fdnix-nixpkgs-indexer`).

## Container Environment Variables

- Common:
  - `AWS_REGION`: AWS region used by clients.
  - `ARTIFACTS_BUCKET`: S3 bucket for `.duckdb` artifacts (e.g., `fdnix-artifacts`).
  - `DUCKDB_DATA_KEY`: S3 key for the full database (e.g., `snapshots/fdnix-data.duckdb`).
  - `DUCKDB_MINIFIED_KEY`: S3 key for the minified database used by the Lambda layer (e.g., `snapshots/fdnix.duckdb`).
  - `PROCESSING_MODE`: `metadata` | `embedding` | `minified` | `both` | `full` (default: `both` → all phases).
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
  - `OUTPUT_PATH`: Local path for the full database (default: `/out/fdnix-data.duckdb`).
  - `OUTPUT_MINIFIED_PATH`: Local path for the minified database (default: `/out/fdnix.duckdb`).
  - `DUCKDB_PATH`: Input DuckDB for embedding mode (defaults to the full DB).

## AWS Guidelines

- Naming: Prefix resources with `fdnix-` (e.g., `fdnix-artifacts`, `fdnix-nixpkgs-indexer`, `fdnix-db-layer`).
- Secrets: Store API keys in SSM Parameter Store or Secrets Manager; reference them in task definitions or via CDK.
- IAM: Grant least-privilege to ECS tasks for S3 read/write on the configured keys and for Lambda layer publishing when enabled.
- DNS/TLS: DNS via Cloudflare; ACM certificates for CloudFront in `us-east-1` (handled in CDK).

## Build (Local)

From repo root:

- Nixpkgs Indexer:
  - `docker build -t fdnix/nixpkgs-indexer packages/containers/nixpkgs-indexer`

## Run (Local)

- All phases with S3 artifacts:
  - `docker run --rm --env-file .env -v "$PWD":/out -e AWS_REGION=us-east-1 -e GEMINI_API_KEY=your-api-key -e GEMINI_MODEL_ID=gemini-embedding-001 -e PROCESSING_MODE=both -e ARTIFACTS_BUCKET=fdnix-artifacts -e DUCKDB_DATA_KEY=snapshots/fdnix-data.duckdb -e DUCKDB_MINIFIED_KEY=snapshots/fdnix.duckdb fdnix/nixpkgs-indexer`
- Single phases:
  - Metadata: `docker run --rm --env-file .env -v "$PWD":/out -e AWS_REGION=us-east-1 -e PROCESSING_MODE=metadata -e ARTIFACTS_BUCKET=fdnix-artifacts -e DUCKDB_DATA_KEY=snapshots/fdnix-data.duckdb fdnix/nixpkgs-indexer`
  - Embedding: `docker run --rm --env-file .env -v "$PWD":/out -e AWS_REGION=us-east-1 -e PROCESSING_MODE=embedding -e GEMINI_API_KEY=your-api-key -e GEMINI_MODEL_ID=gemini-embedding-001 -e ARTIFACTS_BUCKET=fdnix-artifacts -e DUCKDB_DATA_KEY=snapshots/fdnix-data.duckdb fdnix/nixpkgs-indexer`
  - Minified: `docker run --rm --env-file .env -v "$PWD":/out -e AWS_REGION=us-east-1 -e PROCESSING_MODE=minified -e ARTIFACTS_BUCKET=fdnix-artifacts -e DUCKDB_DATA_KEY=snapshots/fdnix-data.duckdb -e DUCKDB_MINIFIED_KEY=snapshots/fdnix.duckdb fdnix/nixpkgs-indexer`

For AWS runs, provide `ARTIFACTS_BUCKET` and keys for one or both artifacts (`DUCKDB_DATA_KEY` and/or `DUCKDB_MINIFIED_KEY`). Requires credentials with access to S3 and a Google Gemini API key. Embedding mode requires `GEMINI_API_KEY`.

## Deployment Notes

- CDK defines a single ECR repo and task definition sized for the end-to-end job.
- Push image to ECR (example):
  - `aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com`
  - `docker tag fdnix/nixpkgs-indexer $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/fdnix-nixpkgs-indexer:latest`
  - `docker push $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/fdnix-nixpkgs-indexer:latest`
- Step Functions then publishes/updates the Lambda Layer from the artifact.

## Outputs and Schema

- Full database: `fdnix-data.duckdb` (complete metadata + embeddings once generated)
- Minified database: `fdnix.duckdb` (only essential columns + FTS; simplified license/maintainers; used by Lambda layer)
- Core tables present in both, when applicable:
  - `packages(...)` (normalized metadata; minified drops non-essential columns like positions, outputsToInstall, lastUpdated, content_hash)
  - `packages_fts_source(...)` with FTS index (built on minified data)
  - `embeddings(package_id, vector)` with VSS index

See `packages/containers/nixpkgs-indexer/README.md` for detailed usage and troubleshooting.

## Phase Details

- Metadata phase:
  - Extracts package metadata from the nixpkgs channel via `nix-env -qaP --json` (no git clone).
  - Cleans and normalizes fields (ids, names, attrs, descriptions, maintainers, etc.).
  - Writes `packages` and `packages_fts_source` tables to DuckDB.
  - Optionally uploads the full DuckDB artifact to S3.
- Embedding phase:
  - Opens existing DuckDB (downloads from S3 when `ARTIFACTS_BUCKET` plus the relevant key is provided if not present locally).
  - Generates text embeddings with Google Gemini API (e.g., `gemini-embedding-001`) with 256 dimensions.
  - Inserts new vectors into `embeddings` and maintains referential integrity with `packages`.
  - Builds/refreshes vector similarity index using DuckDB `vss` extension.
  - Optionally uploads the updated full DuckDB to S3.
- Minified phase:
  - Creates `fdnix.duckdb` by copying essential data and embeddings from the full DB.
  - Builds the FTS index in the minified DB using DuckDB `fts` extension.
  - Uploads the minified DuckDB to S3 for use by the Lambda layer.

## Legacy Support Removal

- Backward compatibility for `DUCKDB_KEY` has been removed across the indexer and docs. Use `DUCKDB_DATA_KEY` (full DB) and `DUCKDB_MINIFIED_KEY` (minified DB, used for layer publishing). `DUCKDB_MINIFIED_KEY` is explicitly required for publishing the layer.

## Benefits of Minified Layer

- Reduced Lambda layer size and faster cold starts
- Optimized query performance over a smaller dataset
- Lower storage and transfer costs
- Full database preserved in S3 for debugging and analytics
